#!/usr/bin/env python3
"""构建 CadQuery 模型并生成可审计的打印交付物。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

try:
    import cadquery as cq
except ImportError as exc:  # pragma: no cover - 由运行环境决定
    raise SystemExit("缺少 CadQuery。请在隔离的 Python 环境中安装 cadquery 后重试。") from exc


BASE_RISK_COVERAGE = (
    "evidence_measurement",
    "object_envelope",
    "assembly_motion",
    "interfaces_keepouts",
    "loads_stability",
    "tolerance_fit",
    "fdm_process",
    "material_environment",
    "cad_export",
    "slicer_first_article",
)
PROFILE_RISK_COVERAGE = {
    "pcb": ("pcb_contact", "electrical_rf_thermal"),
}
EARLY_RISK_PHASES = {"pre_concept", "pre_cad"}
RISK_PHASES = EARLY_RISK_PHASES | {"pre_export", "pre_print", "post_print"}
RISK_SEVERITIES = {"critical", "high", "medium", "low"}
RISK_STATUSES = {"closed", "accepted", "not_applicable", "planned", "open"}
RISK_EVIDENCE_TYPES = {
    "measured",
    "drawing",
    "photo_observed",
    "user_confirmed",
    "official_source",
    "engineering_judgment",
    "planned_test",
}
MANUFACTURING_FINGERPRINT_FIELDS = (
    "printer_model",
    "nozzle_diameter_mm",
    "nozzle_material",
    "filament",
    "build_plate",
    "slicer",
    "slicer_version",
    "process_profile",
)
APPROVAL_GATES = {
    "requirements": ("需求", "requirements_confirmation"),
    "risk_review": ("工程风险审查", "requirements_confirmation"),
    "concept": ("概念效果图", "concept_confirmation"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建并验证 CadQuery 参数化模型。")
    parser.add_argument("source", type=Path, help="实现 build_model() 的 Python 源文件")
    parser.add_argument("--approval", type=Path, required=True, help="已获用户确认的设计审批 JSON")
    parser.add_argument("--output-dir", type=Path, required=True, help="交付物输出目录")
    parser.add_argument("--name", help="覆盖模型输出名")
    parser.add_argument("--dimension-tolerance", type=float, default=0.05, help="包围盒尺寸允许误差，单位 mm")
    parser.add_argument("--mesh-tolerance", type=float, default=0.05, help="STL 网格线性公差，单位 mm")
    parser.add_argument(
        "--skip-gate", action="append", default=[], choices=tuple(APPROVAL_GATES),
        help="仅跳过指定人工确认；可重复，每项必须有绑定当前文件的用户明确跳过记录",
    )
    return parser.parse_args()


def load_module(source: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("cad_model", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模型源码：{source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_model_contract(source: Path) -> SimpleNamespace:
    """在不执行模型源码的前提下读取审批所需的字面量常量。"""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise SystemExit(f"无法静态读取模型源码：{exc}") from exc

    values: dict[str, Any] = {}
    for node in tree.body:
        name: str | None = None
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value_node = node.value
        if name not in {"DESIGN_ID", "RISK_PROFILES"} or value_node is None:
            continue
        try:
            values[name] = ast.literal_eval(value_node)
        except (ValueError, TypeError, SyntaxError):
            raise SystemExit(f"模型源码的 {name} 必须是可静态读取的字面量。")

    return SimpleNamespace(
        DESIGN_ID=values.get("DESIGN_ID"),
        RISK_PROFILES=values.get("RISK_PROFILES"),
    )


def validate_runtime_contract(module: ModuleType, contract: SimpleNamespace) -> None:
    """防止源码导入后的运行时常量与审批前静态值不一致。"""
    errors: list[str] = []
    if getattr(module, "DESIGN_ID", None) != contract.DESIGN_ID:
        errors.append("模型运行时 DESIGN_ID 与审批前静态值不一致。")
    runtime_profiles = getattr(module, "RISK_PROFILES", None)
    if runtime_profiles != contract.RISK_PROFILES:
        errors.append("模型运行时 RISK_PROFILES 与审批前静态值不一致。")
    if errors:
        detail = "\n".join(f"- {error}" for error in errors)
        raise SystemExit(f"模型契约校验未通过：\n{detail}")


def as_shape(value: Any) -> cq.Shape:
    if isinstance(value, cq.Workplane):
        value = value.val()
    if not isinstance(value, cq.Shape):
        raise TypeError("build_model() 必须返回 CadQuery Workplane 或 Shape。")
    return value


def rounded(value: float) -> float:
    return round(float(value), 4)


def shape_metrics(shape: cq.Shape) -> dict[str, Any]:
    bounds = shape.BoundingBox()
    solids = shape.Solids()
    return {
        "实体有效": bool(shape.isValid()),
        "实体数量": len(solids),
        "包围盒_mm": {
            "x": rounded(bounds.xlen),
            "y": rounded(bounds.ylen),
            "z": rounded(bounds.zlen),
            "xmin": rounded(bounds.xmin),
            "ymin": rounded(bounds.ymin),
            "zmin": rounded(bounds.zmin),
            "xmax": rounded(bounds.xmax),
            "ymax": rounded(bounds.ymax),
            "zmax": rounded(bounds.zmax),
        },
        "体积_mm3": rounded(shape.Volume()),
        "表面积_mm2": rounded(shape.Area()),
    }


def validate_geometry(metrics: dict[str, Any], expected: dict[str, float], tolerance: float) -> list[str]:
    errors: list[str] = []
    if not metrics["实体有效"]:
        errors.append("CadQuery 报告实体无效。")
    if metrics["实体数量"] != 1:
        errors.append(f"期望单一连通实体，实际为 {metrics['实体数量']} 个。")
    if metrics["体积_mm3"] <= 0:
        errors.append("模型体积必须大于零。")
    bounds = metrics["包围盒_mm"]
    if abs(bounds["zmin"]) > tolerance:
        errors.append(f"打印基准面应位于 Z=0，实际 zmin={bounds['zmin']} mm。")
    for axis in ("x", "y", "z"):
        if axis in expected and abs(bounds[axis] - float(expected[axis])) > tolerance:
            errors.append(
                f"{axis.upper()} 尺寸不符：期望 {expected[axis]} mm，实际 {bounds[axis]} mm，"
                f"允许误差 {tolerance} mm。"
            )
    return errors


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_linked_file(base_file: Path, value: Any, label: str, errors: list[str]) -> Path | None:
    """解析确认记录中引用的文件，并报告缺失项。"""
    if not isinstance(value, str) or not value.strip():
        errors.append(f"缺少{label}路径。")
        return None
    linked_path = Path(value)
    if not linked_path.is_absolute():
        linked_path = (base_file.parent / linked_path).resolve()
    else:
        linked_path = linked_path.resolve()
    if not linked_path.is_file():
        errors.append(f"{label}不存在：{linked_path}")
        return None
    return linked_path


def validate_linked_hash(path: Path | None, value: Any, label: str, errors: list[str]) -> None:
    """确认外部证据文件没有在用户确认后被替换。"""
    if not isinstance(value, str) or len(value) != 64:
        errors.append(f"缺少有效的{label} SHA-256。")
        return
    if path is not None and sha256(path).lower() != value.lower():
        errors.append(f"{label}哈希与用户确认记录不一致。")


def validate_risk_register(
    approval_path: Path,
    approval: dict[str, Any],
    module: ModuleType,
    errors: list[str],
) -> tuple[dict[str, Any] | None, Path | None]:
    """验证风险覆盖矩阵，并阻止带未关闭前置风险的模型进入构建。"""
    risk_path = resolve_linked_file(approval_path, approval.get("risk_register"), "风险台账", errors)
    validate_linked_hash(risk_path, approval.get("risk_register_sha256"), "风险台账", errors)
    if risk_path is None:
        return None, None

    try:
        risk_register = json.loads(risk_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"无法读取风险台账：{exc}")
        return None, risk_path
    if not isinstance(risk_register, dict):
        errors.append("风险台账根节点必须是 JSON 对象。")
        return None, risk_path

    design_id = getattr(module, "DESIGN_ID", None)
    if risk_register.get("design_id") != design_id:
        errors.append("风险台账的 design_id 与模型源码 DESIGN_ID 不一致。")

    model_profiles = getattr(module, "RISK_PROFILES", None)
    if not isinstance(model_profiles, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in model_profiles
    ):
        errors.append("模型源码的 RISK_PROFILES 必须是字符串列表。")
        model_profiles = []
    register_profiles = risk_register.get("risk_profiles", [])
    if not isinstance(register_profiles, list) or not all(
        isinstance(item, str) and item.strip() for item in register_profiles
    ):
        errors.append("风险台账的 risk_profiles 必须是字符串列表。")
        register_profiles = []
    if set(model_profiles) != set(register_profiles):
        errors.append("风险台账的 risk_profiles 与模型源码 RISK_PROFILES 不一致。")

    fingerprint = risk_register.get("manufacturing_fingerprint")
    if not isinstance(fingerprint, dict):
        errors.append("风险台账缺少 manufacturing_fingerprint 对象。")
        fingerprint = {}
    for field in MANUFACTURING_FINGERPRINT_FIELDS:
        value = fingerprint.get(field)
        if field == "nozzle_diameter_mm":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                errors.append("制造指纹的 nozzle_diameter_mm 必须是正数。")
        elif not isinstance(value, str) or not value.strip():
            errors.append(f"制造指纹缺少非空字段 {field}。")
    if fingerprint.get("status") != "locked":
        errors.append("制造指纹尚未锁定；status 必须为 locked。")

    required_coverage = list(BASE_RISK_COVERAGE)
    for profile in model_profiles:
        required_coverage.extend(PROFILE_RISK_COVERAGE.get(profile, ()))
    coverage = risk_register.get("coverage")
    if not isinstance(coverage, dict):
        errors.append("风险台账缺少 coverage 对象。")
        coverage = {}
    for category in required_coverage:
        item = coverage.get(category)
        if not isinstance(item, dict):
            errors.append(f"风险覆盖矩阵缺少类别：{category}。")
            continue
        status = item.get("status")
        if status == "reviewed":
            evidence = item.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                errors.append(f"风险类别 {category} 已标记 reviewed，但缺少具体 evidence。")
        elif status == "not_applicable":
            rationale = item.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                errors.append(f"风险类别 {category} 已标记 not_applicable，但缺少 rationale。")
        else:
            errors.append(f"风险类别 {category} 的 status 必须是 reviewed 或 not_applicable。")

    risks = risk_register.get("risks")
    if not isinstance(risks, list) or not risks:
        errors.append("风险台账至少需要一个项目特定风险项，不能只填写通用覆盖矩阵。")
        risks = []
    seen_ids: set[str] = set()
    required_text = (
        "id",
        "category",
        "phase",
        "severity",
        "status",
        "evidence_type",
        "failure_mode",
        "cause",
        "mitigation",
        "verification",
    )
    for index, risk in enumerate(risks, start=1):
        if not isinstance(risk, dict):
            errors.append(f"风险项 #{index} 必须是 JSON 对象。")
            continue
        for key in required_text:
            value = risk.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"风险项 #{index} 缺少非空字段 {key}。")
        risk_id = risk.get("id")
        if isinstance(risk_id, str) and risk_id.strip():
            if risk_id in seen_ids:
                errors.append(f"风险编号重复：{risk_id}。")
            seen_ids.add(risk_id)
        category = risk.get("category")
        if not isinstance(category, str) or category not in coverage:
            errors.append(f"风险 {risk_id or index} 的 category 未对应风险覆盖矩阵：{category}。")
        phase = risk.get("phase")
        severity = risk.get("severity")
        status = risk.get("status")
        evidence_type = risk.get("evidence_type")
        if not isinstance(phase, str) or phase not in RISK_PHASES:
            errors.append(f"风险 {risk_id or index} 的 phase 无效：{phase}。")
        if not isinstance(severity, str) or severity not in RISK_SEVERITIES:
            errors.append(f"风险 {risk_id or index} 的 severity 无效：{severity}。")
        if not isinstance(status, str) or status not in RISK_STATUSES:
            errors.append(f"风险 {risk_id or index} 的 status 无效：{status}。")
            continue
        if not isinstance(evidence_type, str) or evidence_type not in RISK_EVIDENCE_TYPES:
            errors.append(f"风险 {risk_id or index} 的 evidence_type 无效：{evidence_type}。")
        if status == "closed":
            evidence = risk.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                errors.append(f"风险 {risk_id or index} 已关闭，但缺少 evidence。")
            if severity in {"critical", "high"} and evidence_type in {
                "engineering_judgment",
                "planned_test",
            }:
                errors.append(
                    f"风险 {risk_id or index} 为 {severity}，不能仅凭工程判断或计划试验标记为关闭。"
                )
        elif status == "accepted":
            rationale = risk.get("acceptance_rationale")
            if severity in {"critical", "high"}:
                errors.append(f"风险 {risk_id or index} 为 {severity}，不得仅接受风险后进入建模。")
            if not isinstance(rationale, str) or not rationale.strip():
                errors.append(f"风险 {risk_id or index} 已接受，但缺少 acceptance_rationale。")
        elif status == "not_applicable":
            rationale = risk.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                errors.append(f"风险 {risk_id or index} 标记不适用，但缺少 rationale。")
        if phase in EARLY_RISK_PHASES and status in {"open", "planned"}:
            errors.append(f"前置风险 {risk_id or index} 尚未关闭，禁止开始 CAD。")

    questions = risk_register.get("open_questions", [])
    if not isinstance(questions, list):
        errors.append("风险台账的 open_questions 必须是列表。")
    else:
        for index, question in enumerate(questions, start=1):
            if not isinstance(question, dict):
                errors.append(f"待确认问题 #{index} 必须包含 question 和 blocking 字段。")
                continue
            text = question.get("question")
            blocking = question.get("blocking")
            if not isinstance(text, str) or not text.strip() or not isinstance(blocking, bool):
                errors.append(f"待确认问题 #{index} 的 question 或 blocking 无效。")
            elif blocking:
                errors.append(f"仍有阻塞问题未解决：{text}")

    return risk_register, risk_path


def validate_approval(
    path: Path, module: ModuleType, skip_gates: tuple[str, ...] = (), source: Path | None = None,
) -> dict[str, Any]:
    """验证人工确认或逐次明确跳过记录；事实、风险与文件一致性始终校验。"""
    approval_path = path.resolve()
    if not approval_path.is_file():
        raise SystemExit(f"设计确认文件不存在：{approval_path}")
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取设计确认文件：{exc}") from exc

    if not isinstance(approval, dict):
        raise SystemExit("设计确认文件根节点必须是 JSON 对象。")

    errors: list[str] = []
    design_id = getattr(module, "DESIGN_ID", None)
    if not isinstance(design_id, str) or not design_id.strip():
        errors.append("模型源码缺少非空 DESIGN_ID。")
    if approval.get("design_id") != design_id:
        errors.append("确认记录的 design_id 与模型源码 DESIGN_ID 不一致。")
    selected = set(skip_gates)
    if len(selected) != len(skip_gates) or selected - APPROVAL_GATES.keys():
        errors.append("跳过列表包含重复或未知的人工确认门禁。")
    waivers = approval.get("gate_waivers", {})
    if not isinstance(waivers, dict):
        errors.append("gate_waivers 必须是按门禁标识索引的对象。")
        waivers = {}
    if set(waivers) != selected:
        errors.append("gate_waivers 必须与本次 --skip-gate 选择完全一致；记录本身不会启用跳过。")
    for gate, (label, confirmation_key) in APPROVAL_GATES.items():
        if gate in selected:
            approval_key = f"{gate}_approved"
            if approval_key in approval and approval[approval_key] is not False:
                errors.append(f"{label}已选择跳过，批准字段必须为 false 或省略，不得标记为 approved。")
            continue
        if approval.get(f"{gate}_approved") is not True:
            errors.append(f"{label}尚未明确确认。")
        value = approval.get(confirmation_key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"缺少{label}确认原文。")

    requirements_path = resolve_linked_file(
        approval_path,
        approval.get("requirements_document"),
        "需求确认单",
        errors,
    )
    validate_linked_hash(
        requirements_path,
        approval.get("requirements_sha256"),
        "需求确认单",
        errors,
    )
    risk_register, risk_path = validate_risk_register(approval_path, approval, module, errors)
    concept_path = None
    definition_path = None
    # 跳过概念人工确认且不提供图像时，核心结构事实仍须保留为可校验工件。
    if "concept" in selected and "concept_image" not in approval and "concept_image_sha256" not in approval:
        definition_path = resolve_linked_file(
            approval_path, approval.get("concept_definition"), "核心结构说明", errors,
        )
        validate_linked_hash(definition_path, approval.get("concept_definition_sha256"), "核心结构说明", errors)
        if definition_path is not None:
            try:
                definition = json.loads(definition_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                errors.append(f"无法读取核心结构说明：{exc}")
                definition = {}
            if not isinstance(definition, dict):
                errors.append("核心结构说明必须为 JSON 对象。")
                definition = {}
            if definition.get("design_id") != design_id:
                errors.append("核心结构说明的 design_id 与模型源码 DESIGN_ID 不一致。")
            for key in ("structural_principle", "interfaces_keepouts", "assembly_motion", "critical_dimensions"):
                if not isinstance(definition.get(key), str) or not definition[key].strip():
                    errors.append(f"核心结构说明缺少非空字段 {key}。")
    else:
        concept_path = resolve_linked_file(approval_path, approval.get("concept_image"), "概念效果图", errors)
        validate_linked_hash(concept_path, approval.get("concept_image_sha256"), "概念效果图", errors)

    if selected:
        artifacts = {"source": source, "requirements": requirements_path,
                     "risk_register": risk_path, "concept": concept_path or definition_path}
        for gate in selected:
            waiver = waivers.get(gate)
            if not isinstance(waiver, dict):
                errors.append(f"{gate} 缺少明确跳过记录。")
                continue
            if waiver.get("status") != "skipped":
                errors.append(f"{gate} 跳过记录的 status 必须为 skipped。")
            if waiver.get("design_id") != design_id:
                errors.append(f"{gate} 跳过记录的 design_id 与模型源码不一致。")
            for key in ("user_request", "reason", "scope"):
                if not isinstance(waiver.get(key), str) or not waiver[key].strip():
                    errors.append(f"{gate} 跳过记录缺少非空字段 {key}。")
            hashes = waiver.get("artifacts_sha256")
            if not isinstance(hashes, dict) or set(hashes) != set(artifacts):
                errors.append(f"{gate} 的 artifacts_sha256 必须准确覆盖源码、需求、风险台账和概念工件。")
                continue
            for key, artifact in artifacts.items():
                if artifact is None or not artifact.is_file():
                    errors.append(f"{gate} 无法绑定当前 {key} 文件。")
                else:
                    validate_linked_hash(artifact, hashes.get(key), f"{gate} 跳过对象 {key}", errors)

    if errors:
        detail = "\n".join(f"- {error}" for error in errors)
        raise SystemExit(f"设计确认门禁未通过：\n{detail}")

    return {
        **approval,
        "approval_file": str(approval_path),
        "requirements_document": str(requirements_path),
        "concept_image": str(concept_path) if concept_path else None,
        "concept_definition": str(definition_path) if definition_path else None,
        "risk_register": str(risk_path),
        "risk_register_data": risk_register,
        "gate_statuses": {gate: "skipped" if gate in selected else "approved" for gate in APPROVAL_GATES},
    }


def mesh_topology(triangles: list[tuple[tuple[float, float, float], ...]]) -> dict[str, Any]:
    """检查三角网格的退化面、闭合流形边和相邻面方向。"""
    if not triangles:
        raise ValueError("网格不包含三角面。")

    merge_tolerance = 1e-5

    def vertex_key(vertex: tuple[float, float, float]) -> tuple[int, int, int]:
        if not all(math.isfinite(value) for value in vertex):
            raise ValueError("网格包含非有限坐标。")
        return tuple(round(value / merge_tolerance) for value in vertex)

    edge_directions: dict[
        tuple[tuple[int, int, int], tuple[int, int, int]],
        list[bool],
    ] = {}
    face_counts: dict[tuple[tuple[int, int, int], ...], int] = {}
    unique_vertices: set[tuple[int, int, int]] = set()
    degenerate_faces = 0
    signed_volume = 0.0

    for triangle in triangles:
        if len(triangle) != 3:
            raise ValueError("网格面不是三角形。")
        keys = tuple(vertex_key(vertex) for vertex in triangle)
        unique_vertices.update(keys)
        face_key = tuple(sorted(keys))
        face_counts[face_key] = face_counts.get(face_key, 0) + 1
        if len(set(keys)) != 3:
            degenerate_faces += 1
            continue

        a, b, c = triangle
        ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        if sum(value * value for value in cross) <= 1e-24:
            degenerate_faces += 1
            continue
        signed_volume += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        ) / 6.0

        for start, end in ((keys[0], keys[1]), (keys[1], keys[2]), (keys[2], keys[0])):
            undirected = tuple(sorted((start, end)))
            edge_directions.setdefault(undirected, []).append(start == undirected[0])

    boundary_edges = sum(len(directions) == 1 for directions in edge_directions.values())
    nonmanifold_edges = sum(len(directions) != 2 for directions in edge_directions.values())
    inconsistent_edges = sum(
        len(directions) == 2 and directions[0] == directions[1]
        for directions in edge_directions.values()
    )
    duplicate_faces = sum(count - 1 for count in face_counts.values() if count > 1)
    outward_normals = signed_volume > 1e-9
    closed_manifold = (
        degenerate_faces == 0
        and duplicate_faces == 0
        and boundary_edges == 0
        and nonmanifold_edges == 0
        and inconsistent_edges == 0
        and outward_normals
    )
    return {
        "三角面数量": len(triangles),
        "唯一顶点数量": len(unique_vertices),
        "退化面数量": degenerate_faces,
        "重复面数量": duplicate_faces,
        "边界边数量": boundary_edges,
        "非流形边数量": nonmanifold_edges,
        "方向不一致边数量": inconsistent_edges,
        "法向朝外": outward_normals,
        "闭合流形": closed_manifold,
        "有符号体积_mm3": rounded(signed_volume),
        "顶点合并公差_mm": merge_tolerance,
    }


def parse_stl_triangles(path: Path) -> tuple[str, list[tuple[tuple[float, float, float], ...]]]:
    """读取二进制或 ASCII STL 的三角形。"""
    data = path.read_bytes()
    if len(data) < 15:
        raise ValueError("STL 文件过小。")
    if len(data) >= 84:
        triangle_count = struct.unpack_from("<I", data, 80)[0]
        expected_size = 84 + triangle_count * 50
        if expected_size == len(data):
            triangles: list[tuple[tuple[float, float, float], ...]] = []
            for index in range(triangle_count):
                values = struct.unpack_from("<12fH", data, 84 + index * 50)
                triangles.append(
                    (
                        (values[3], values[4], values[5]),
                        (values[6], values[7], values[8]),
                        (values[9], values[10], values[11]),
                    )
                )
            return "binary", triangles

    vertices: list[tuple[float, float, float]] = []
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("无法识别 STL 格式。") from exc
    for line in text.splitlines():
        fields = line.strip().split()
        if len(fields) == 4 and fields[0].lower() == "vertex":
            try:
                vertices.append((float(fields[1]), float(fields[2]), float(fields[3])))
            except ValueError as exc:
                raise ValueError("ASCII STL 包含无效顶点。") from exc
    if not vertices or len(vertices) % 3 != 0:
        raise ValueError("ASCII STL 不包含完整三角面。")
    triangles = [tuple(vertices[index : index + 3]) for index in range(0, len(vertices), 3)]
    return "ascii", triangles


def inspect_stl(path: Path) -> dict[str, Any]:
    mesh_format, triangles = parse_stl_triangles(path)
    topology = mesh_topology(triangles)
    if not topology["闭合流形"]:
        raise ValueError(f"STL 网格不是方向一致的闭合流形：{topology}")
    return {
        "格式": mesh_format,
        "三角面数量": len(triangles),
        "字节数": path.stat().st_size,
        "拓扑": topology,
    }


def inspect_3mf(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        model_entries = [name for name in names if name.lower().endswith(".model")]
        if not model_entries:
            raise ValueError("3MF 包中没有 .model 几何条目。")
        bad_entry = archive.testzip()
        if bad_entry is not None:
            raise ValueError(f"3MF 压缩包校验失败：{bad_entry}")
        if len(model_entries) != 1:
            raise ValueError(f"3MF 包应有一个主模型条目，实际为 {len(model_entries)} 个。")
        try:
            root = ET.fromstring(archive.read(model_entries[0]))
        except ET.ParseError as exc:
            raise ValueError(f"3MF 模型 XML 无效：{exc}") from exc

    namespace = root.tag.partition("}")[0].lstrip("{") if "}" in root.tag else ""

    def tag(name: str) -> str:
        return f"{{{namespace}}}{name}" if namespace else name

    unit = root.attrib.get("unit", "millimeter")
    if unit != "millimeter":
        raise ValueError(f"3MF 单位必须为 millimeter，实际为 {unit}。")

    object_ids: set[str] = set()
    mesh_reports: list[dict[str, Any]] = []
    for object_node in root.findall(f".//{tag('object')}"):
        object_id = object_node.attrib.get("id")
        if object_id:
            object_ids.add(object_id)
        mesh_node = object_node.find(tag("mesh"))
        if mesh_node is None:
            continue
        vertex_nodes = mesh_node.findall(f"./{tag('vertices')}/{tag('vertex')}")
        triangle_nodes = mesh_node.findall(f"./{tag('triangles')}/{tag('triangle')}")
        vertices: list[tuple[float, float, float]] = []
        try:
            for vertex in vertex_nodes:
                vertices.append(
                    (
                        float(vertex.attrib["x"]),
                        float(vertex.attrib["y"]),
                        float(vertex.attrib["z"]),
                    )
                )
        except (KeyError, ValueError) as exc:
            raise ValueError("3MF 包含无效顶点。") from exc
        triangles: list[tuple[tuple[float, float, float], ...]] = []
        try:
            for triangle in triangle_nodes:
                indices = tuple(int(triangle.attrib[key]) for key in ("v1", "v2", "v3"))
                if any(index < 0 or index >= len(vertices) for index in indices):
                    raise ValueError("三角面引用了不存在的顶点。")
                triangles.append(tuple(vertices[index] for index in indices))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"3MF 包含无效三角面：{exc}") from exc
        topology = mesh_topology(triangles)
        if not topology["闭合流形"]:
            raise ValueError(f"3MF 网格不是方向一致的闭合流形：{topology}")
        mesh_reports.append(
            {
                "object_id": object_id,
                "顶点记录数量": len(vertices),
                "拓扑": topology,
            }
        )

    if not mesh_reports:
        raise ValueError("3MF 主模型不包含可制造网格。")
    build_items = root.findall(f"./{tag('build')}/{tag('item')}")
    if not build_items:
        raise ValueError("3MF 主模型没有 build item。")
    missing_objects = [
        item.attrib.get("objectid")
        for item in build_items
        if item.attrib.get("objectid") not in object_ids
    ]
    if missing_objects:
        raise ValueError(f"3MF build 引用了不存在的 object：{missing_objects}")
    return {
        "压缩条目数量": len(names),
        "模型条目": model_entries,
        "单位": unit,
        "build_item数量": len(build_items),
        "网格": mesh_reports,
    }


def render_preview(module: ModuleType, model: cq.Shape, output_path: Path, mesh_tolerance: float) -> None:
    """把预览网格交给独立进程渲染，避免 CAD 内核与绘图库退出阶段互相影响。"""
    if hasattr(module, "preview_components"):
        components = module.preview_components(model)
    else:
        components = [{"name": "模型", "shape": model, "color": "#F28C28", "alpha": 1.0}]
    renderer = Path(__file__).with_name("render_mesh_preview.py")
    if not renderer.is_file():
        raise RuntimeError(f"缺少独立预览渲染器：{renderer}")

    with tempfile.TemporaryDirectory(prefix="cad_preview_") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        scene: list[dict[str, Any]] = []
        for index, item in enumerate(components):
            mesh_path = temp_dir / f"component_{index:02d}.stl"
            cq.exporters.export(
                as_shape(item["shape"]),
                str(mesh_path),
                exportType="STL",
                tolerance=mesh_tolerance,
                angularTolerance=0.1,
            )
            scene.append(
                {
                    "name": item.get("name", f"组件 {index + 1}"),
                    "path": str(mesh_path),
                    "color": item.get("color", "#F28C28"),
                    "alpha": float(item.get("alpha", 1.0)),
                }
            )
        scene_path = temp_dir / "scene.json"
        scene_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, str(renderer), str(scene_path), str(output_path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"独立预览渲染失败，退出码 {completed.returncode}：{detail}")


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    if not source.is_file():
        raise SystemExit(f"模型源码不存在：{source}")

    contract = load_model_contract(source)
    approval = validate_approval(args.approval, contract, tuple(args.skip_gate), source)
    module = load_module(source)
    validate_runtime_contract(module, contract)
    if not hasattr(module, "build_model"):
        raise SystemExit("模型源码缺少 build_model()。")
    output_dir.mkdir(parents=True, exist_ok=True)

    name = args.name or getattr(module, "MODEL_NAME", source.stem)
    model = as_shape(module.build_model())
    metrics = shape_metrics(model)
    expected = getattr(module, "EXPECTED_BOUNDS_MM", {})
    errors = validate_geometry(metrics, expected, args.dimension_tolerance)
    if errors:
        for error in errors:
            print(f"[失败] {error}", file=sys.stderr)
        return 2

    step_path = output_dir / f"{name}.step"
    stp_path = output_dir / f"{name}.stp"
    stl_path = output_dir / f"{name}.stl"
    three_mf_path = output_dir / f"{name}.3mf"
    preview_path = output_dir / f"{name}_preview.png"
    report_path = output_dir / f"{name}_validation.json"

    cq.exporters.export(model, str(step_path), exportType="STEP")
    shutil.copyfile(step_path, stp_path)
    cq.exporters.export(
        model,
        str(stl_path),
        exportType="STL",
        tolerance=args.mesh_tolerance,
        angularTolerance=0.1,
    )
    cq.exporters.export(model, str(three_mf_path), exportType="3MF", tolerance=args.mesh_tolerance)

    reloaded = as_shape(cq.importers.importStep(str(step_path)))
    reload_metrics = shape_metrics(reloaded)
    if not reload_metrics["实体有效"] or reload_metrics["实体数量"] != 1:
        raise RuntimeError("STEP 回读后的实体无效或不再是单一实体。")

    stl_info = inspect_stl(stl_path)
    three_mf_info = inspect_3mf(three_mf_path)
    render_preview(module, model, preview_path, args.mesh_tolerance)

    files = [step_path, stp_path, stl_path, three_mf_path, preview_path]
    report = {
        "结论": "几何已验证",
        "模型名称": name,
        "生成时间": datetime.now().astimezone().isoformat(timespec="seconds"),
        "CadQuery版本": getattr(cq, "__version__", "未知"),
        "源码": str(source),
        "设计确认": approval,
        "参数": getattr(module, "MODEL_PARAMETERS", {}),
        "几何": metrics,
        "STEP回读": reload_metrics,
        "STL检查": stl_info,
        "3MF检查": three_mf_info,
        "文件": {
            path.name: {"字节数": path.stat().st_size, "sha256": sha256(path)} for path in files
        },
        "尚未验证": [
            "STL/3MF 全局自交需由切片器复核",
            "Bambu Studio 实际切片",
            "打印机真实打印",
            "目标实物适配",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[通过] {name} 几何已验证。")
    print(f"[输出] {output_dir}")
    print(f"[尺寸] {metrics['包围盒_mm']['x']} × {metrics['包围盒_mm']['y']} × {metrics['包围盒_mm']['z']} mm")
    print(f"[网格] STL 三角面 {stl_info['三角面数量']} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
