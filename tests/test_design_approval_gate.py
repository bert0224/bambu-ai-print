#!/usr/bin/env python3
"""验证 AI 打印 skill 的设计确认和工程风险门禁。"""

from __future__ import annotations

import hashlib
import io
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cadquery_build.py"
SPEC = importlib.util.spec_from_file_location("cadquery_build", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("无法加载构建脚本。")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class ApprovalGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.image = self.root / "concept.png"
        self.image.write_bytes(b"concept-image")
        self.requirements = self.root / "requirements.md"
        self.requirements.write_text("# 已确认需求\n", encoding="utf-8")
        self.risk_path = self.root / "risk.json"
        categories = list(BUILD.BASE_RISK_COVERAGE) + list(BUILD.PROFILE_RISK_COVERAGE["pcb"])
        self.risk = {
            "design_id": "fixture-v2",
            "risk_profiles": ["pcb"],
            "manufacturing_fingerprint": {
                "status": "locked",
                "printer_model": "Bambu Lab P1S",
                "nozzle_diameter_mm": 0.4,
                "nozzle_material": "硬化钢",
                "filament": "Bambu PETG HF",
                "build_plate": "纹理 PEI 板",
                "slicer": "Bambu Studio",
                "slicer_version": "2.4.0",
                "process_profile": "0.20mm Standard",
            },
            "coverage": {
                category: {"status": "reviewed", "evidence": f"已评审 {category}"}
                for category in categories
            },
            "risks": [
                {
                    "id": "R-001",
                    "category": "assembly_motion",
                    "phase": "pre_cad",
                    "severity": "critical",
                    "status": "closed",
                    "evidence_type": "user_confirmed",
                    "failure_mode": "底部接口阻塞装入路径",
                    "cause": "只检查最终位置",
                    "mitigation": "采用开放式中部夹持",
                    "verification": "检查完整扫掠包络",
                    "evidence": "用户照片和确认记录",
                }
            ],
            "open_questions": [],
        }
        self.record = {
            "design_id": "fixture-v2",
            "requirements_approved": True,
            "risk_review_approved": True,
            "concept_approved": True,
            "requirements_confirmation": "确认需求和风险摘要",
            "concept_confirmation": "确认概念图",
            "requirements_document": self.requirements.name,
            "requirements_sha256": hashlib.sha256(self.requirements.read_bytes()).hexdigest(),
            "risk_register": self.risk_path.name,
            "risk_register_sha256": "",
            "concept_image": self.image.name,
            "concept_image_sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
        }
        self.approval = self.root / "approval.json"
        self.module = SimpleNamespace(DESIGN_ID="fixture-v2", RISK_PROFILES=["pcb"])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_record(self) -> None:
        self.risk_path.write_text(json.dumps(self.risk, ensure_ascii=False), encoding="utf-8")
        self.record["risk_register_sha256"] = hashlib.sha256(self.risk_path.read_bytes()).hexdigest()
        self.approval.write_text(json.dumps(self.record, ensure_ascii=False), encoding="utf-8")

    def test_valid_record_passes(self) -> None:
        self.write_record()
        result = BUILD.validate_approval(self.approval, self.module)
        self.assertEqual(result["design_id"], "fixture-v2")
        self.assertEqual(result["risk_register_data"]["risks"][0]["id"], "R-001")

    def test_requirements_confirmation_is_mandatory(self) -> None:
        self.record["requirements_approved"] = False
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "需求尚未明确确认"):
            BUILD.validate_approval(self.approval, self.module)

    def test_risk_review_confirmation_is_mandatory(self) -> None:
        self.record["risk_review_approved"] = False
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "工程风险审查尚未明确确认"):
            BUILD.validate_approval(self.approval, self.module)

    def test_concept_hash_must_match(self) -> None:
        self.record["concept_image_sha256"] = "0" * 64
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "哈希"):
            BUILD.validate_approval(self.approval, self.module)

    def test_design_id_must_match(self) -> None:
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "design_id"):
            BUILD.validate_approval(
                self.approval,
                SimpleNamespace(DESIGN_ID="another-design", RISK_PROFILES=["pcb"]),
            )

    def test_missing_risk_coverage_blocks_build(self) -> None:
        del self.risk["coverage"]["assembly_motion"]
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "assembly_motion"):
            BUILD.validate_approval(self.approval, self.module)

    def test_unlocked_manufacturing_fingerprint_blocks_build(self) -> None:
        self.risk["manufacturing_fingerprint"]["status"] = "draft"
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "制造指纹尚未锁定"):
            BUILD.validate_approval(self.approval, self.module)

    def test_open_pre_cad_risk_blocks_build(self) -> None:
        self.risk["risks"][0]["status"] = "open"
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "禁止开始 CAD"):
            BUILD.validate_approval(self.approval, self.module)

    def test_changed_risk_register_hash_blocks_build(self) -> None:
        self.write_record()
        self.risk_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "风险台账哈希"):
            BUILD.validate_approval(self.approval, self.module)

    def test_changed_requirements_hash_blocks_build(self) -> None:
        self.write_record()
        self.requirements.write_text("# 被修改的需求\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "需求确认单哈希"):
            BUILD.validate_approval(self.approval, self.module)

    def test_critical_risk_cannot_be_accepted(self) -> None:
        self.risk["risks"][0]["status"] = "accepted"
        self.risk["risks"][0]["acceptance_rationale"] = "用户愿意承担"
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "不得仅接受风险"):
            BUILD.validate_approval(self.approval, self.module)

    def test_high_risk_cannot_close_on_judgment_only(self) -> None:
        self.risk["risks"][0]["severity"] = "high"
        self.risk["risks"][0]["evidence_type"] = "engineering_judgment"
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "不能仅凭工程判断"):
            BUILD.validate_approval(self.approval, self.module)

    def test_blocking_open_question_blocks_build(self) -> None:
        self.risk["open_questions"] = [{"question": "夹持区是否允许接触器件？", "blocking": True}]
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "仍有阻塞问题"):
            BUILD.validate_approval(self.approval, self.module)

    def test_static_contract_read_does_not_execute_model_source(self) -> None:
        source = self.root / "model.py"
        marker = self.root / "不应生成.txt"
        source.write_text(
            "from pathlib import Path\n"
            "DESIGN_ID = 'fixture-v2'\n"
            "RISK_PROFILES = ['pcb']\n"
            f"Path({str(marker)!r}).write_text('已执行', encoding='utf-8')\n",
            encoding="utf-8",
        )
        contract = BUILD.load_model_contract(source)
        self.assertEqual(contract.DESIGN_ID, "fixture-v2")
        self.assertEqual(contract.RISK_PROFILES, ["pcb"])
        self.assertFalse(marker.exists())

    def test_dynamic_contract_is_rejected_before_execution(self) -> None:
        source = self.root / "dynamic_model.py"
        source.write_text(
            "DESIGN_ID = 'fixture-' + 'v2'\nRISK_PROFILES = ['pcb']\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SystemExit, "DESIGN_ID.*字面量"):
            BUILD.load_model_contract(source)

    def test_missing_risk_profiles_is_rejected(self) -> None:
        self.write_record()
        with self.assertRaisesRegex(SystemExit, "RISK_PROFILES"):
            BUILD.validate_approval(
                self.approval,
                SimpleNamespace(DESIGN_ID="fixture-v2", RISK_PROFILES=None),
            )

    def test_cli_rejects_before_source_execution_or_output_creation(self) -> None:
        source = self.root / "unapproved_model.py"
        marker = self.root / "源码被执行.txt"
        output_dir = self.root / "不应创建的输出"
        source.write_text(
            "from pathlib import Path\n"
            "DESIGN_ID = 'fixture-v2'\n"
            "RISK_PROFILES = ['pcb']\n"
            f"Path({str(marker)!r}).write_text('已执行', encoding='utf-8')\n"
            "def build_model():\n    raise RuntimeError('不应执行')\n",
            encoding="utf-8",
        )
        approval = self.root / "未批准.json"
        approval.write_text('{"design_id":"fixture-v2"}', encoding="utf-8")
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(source),
                "--approval",
                str(approval),
                "--output-dir",
                str(output_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("设计确认门禁未通过", completed.stderr + completed.stdout)
        self.assertFalse(marker.exists())
        self.assertFalse(output_dir.exists())

    def test_open_stl_mesh_is_rejected(self) -> None:
        stl_path = self.root / "开放三角形.stl"
        stl_path.write_text(
            "solid open\n"
            "facet normal 0 0 1\nouter loop\n"
            "vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
            "endloop\nendfacet\nendsolid open\n",
            encoding="ascii",
        )
        with self.assertRaisesRegex(ValueError, "闭合流形"):
            BUILD.inspect_stl(stl_path)

    def test_fully_approved_box_builds_with_manifold_meshes(self) -> None:
        self.write_record()
        source = self.root / "approved_box.py"
        source.write_text(
            "import cadquery as cq\n"
            "MODEL_NAME = '门禁测试方块'\n"
            "DESIGN_ID = 'fixture-v2'\n"
            "RISK_PROFILES = ['pcb']\n"
            "MODEL_PARAMETERS = {'边长': 10.0}\n"
            "EXPECTED_BOUNDS_MM = {'x': 10.0, 'y': 10.0, 'z': 10.0}\n"
            "def build_model():\n"
            "    return cq.Workplane('XY').box(10, 10, 10, centered=(True, True, False))\n",
            encoding="utf-8",
        )
        output_dir = self.root / "approved_output"
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(source),
                "--approval",
                str(self.approval),
                "--output-dir",
                str(output_dir),
                "--name",
                "gate_box",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        report = json.loads((output_dir / "gate_box_validation.json").read_text(encoding="utf-8"))
        self.assertTrue(report["STL检查"]["拓扑"]["闭合流形"])
        self.assertTrue(report["3MF检查"]["网格"][0]["拓扑"]["闭合流形"])
        self.assertEqual(report["3MF检查"]["单位"], "millimeter")

    def prepare_waiver(self, gates: tuple[str, ...] = ("concept",)) -> Path:
        """为隔离方块建立逐门禁跳过记录，不伪造人工批准。"""
        self.source = self.root / "waiver_box.py"
        self.source.write_text(
            "import cadquery as cq\nDESIGN_ID = 'fixture-v2'\nRISK_PROFILES = ['pcb']\n"
            "EXPECTED_BOUNDS_MM = {'x': 10, 'y': 10, 'z': 10}\n"
            "def build_model():\n"
            "    return cq.Workplane('XY').box(10, 10, 10, centered=(True, True, False))\n",
            encoding="utf-8",
        )
        self.definition = self.root / "concept_definition.json"
        self.definition.write_text(json.dumps({
            "design_id": "fixture-v2", "structural_principle": "十毫米实心方块",
            "interfaces_keepouts": "无装配接口或禁触区域",
            "assembly_motion": "独立验证件，无装拆动作",
            "critical_dimensions": "三个方向均为十毫米，底面为 Z=0",
        }, ensure_ascii=False), encoding="utf-8")
        self.record.pop("concept_image", None)
        self.record.pop("concept_image_sha256", None)
        self.record["concept_definition"] = self.definition.name
        self.record["concept_definition_sha256"] = BUILD.sha256(self.definition)
        for gate in gates:
            self.record[f"{gate}_approved"] = False
        self.write_record()
        artifacts = {
            "source": BUILD.sha256(self.source),
            "requirements": BUILD.sha256(self.requirements),
            "risk_register": BUILD.sha256(self.risk_path),
            "concept": BUILD.sha256(self.definition),
        }
        self.record["gate_waivers"] = {
            gate: {"status": "skipped", "design_id": "fixture-v2",
                   "user_request": f"本次 fixture-v2 方块跳过 {gate} 人工确认",
                   "reason": "本地隔离几何验证", "scope": "当前方块文件，不涉及设备操作",
                   "artifacts_sha256": artifacts.copy()}
            for gate in gates
        }
        self.write_record()
        return self.source

    def run_waiver_cli(self, gates: tuple[str, ...]) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.source), "--approval", str(self.approval),
             "--output-dir", str(self.root / "waiver_output"), "--name", "waiver_box",
             *[value for gate in gates for value in ("--skip-gate", gate)]],
            capture_output=True, text=True, encoding="utf-8", env=environment, check=False,
        )

    def test_cli_explicit_waivers_export_without_claiming_approval_or_print(self) -> None:
        gates = ("requirements", "risk_review", "concept")
        self.prepare_waiver(gates)
        result = self.run_waiver_cli(gates)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = self.root / "waiver_output"
        report = json.loads((output / "waiver_box_validation.json").read_text(encoding="utf-8"))
        self.assertEqual(report["设计确认"]["gate_statuses"], dict.fromkeys(gates, "skipped"))
        for gate in gates:
            self.assertFalse(report["设计确认"][f"{gate}_approved"])
            self.assertEqual(report["设计确认"]["gate_waivers"][gate]["status"], "skipped")
        self.assertIsNone(report["设计确认"]["concept_image"])
        self.assertTrue(report["STL检查"]["拓扑"]["闭合流形"])
        self.assertTrue(report["3MF检查"]["网格"][0]["拓扑"]["闭合流形"])
        self.assertIn("打印机真实打印", report["尚未验证"])
        self.assertEqual({path.suffix for path in output.iterdir()}, {".step", ".stp", ".stl", ".3mf", ".png", ".json"})

    def test_cli_waiver_record_does_not_enable_skip_without_flag(self) -> None:
        self.prepare_waiver()
        result = self.run_waiver_cli(())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "waiver_output").exists())

    def test_cli_single_waiver_keeps_other_gates_approved(self) -> None:
        self.prepare_waiver()
        result = self.run_waiver_cli(("concept",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report_path = self.root / "waiver_output" / "waiver_box_validation.json"
        statuses = json.loads(report_path.read_text(encoding="utf-8"))["设计确认"]["gate_statuses"]
        self.assertEqual(statuses, {"requirements": "approved", "risk_review": "approved", "concept": "skipped"})

    def test_cli_skip_does_not_hide_referenced_concept_hash_failure(self) -> None:
        self.prepare_waiver()
        self.record["concept_image"] = self.image.name
        self.record["concept_image_sha256"] = "0" * 64
        self.record["gate_waivers"]["concept"]["artifacts_sha256"]["concept"] = BUILD.sha256(self.image)
        self.write_record()
        result = self.run_waiver_cli(("concept",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("概念效果图哈希", result.stderr)
        self.assertFalse((self.root / "waiver_output").exists())

    def test_cli_skip_does_not_approve_unselected_gate(self) -> None:
        self.prepare_waiver()
        self.record["requirements_approved"] = False
        self.write_record()
        result = self.run_waiver_cli(("concept",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("需求尚未明确确认", result.stderr)
        self.assertFalse((self.root / "waiver_output").exists())

    def test_skip_main_only_spawns_local_preview_renderer(self) -> None:
        self.prepare_waiver()
        args = [str(SCRIPT), str(self.source), "--approval", str(self.approval),
                "--output-dir", str(self.root / "preview_only_output"), "--skip-gate", "concept"]
        with patch.object(sys, "argv", args), patch.object(BUILD.subprocess, "run", wraps=subprocess.run) as child, redirect_stdout(io.StringIO()):
            self.assertEqual(BUILD.main(), 0)
        child.assert_called_once()
        command = child.call_args.args[0]
        self.assertEqual(command[:2], [sys.executable, str(SCRIPT.with_name("render_mesh_preview.py"))])
        self.assertEqual(len(command), 4)

    def test_cli_skip_requires_explicit_matching_record(self) -> None:
        for missing in ("user_request", "reason", "scope", "status", "artifacts_sha256"):
            with self.subTest(missing=missing):
                self.prepare_waiver()
                del self.record["gate_waivers"]["concept"][missing]
                self.write_record()
                result = self.run_waiver_cli(("concept",))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "waiver_output").exists())

    def test_cli_unknown_gate_rejected(self) -> None:
        self.prepare_waiver()
        result = self.run_waiver_cli(("geometry",))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "waiver_output").exists())

    def test_cli_waiver_cannot_override_hash_or_design_id(self) -> None:
        for field in ("design_id", "source", "requirements", "risk_register", "concept"):
            with self.subTest(field=field):
                self.prepare_waiver()
                waiver = self.record["gate_waivers"]["concept"]
                if field == "design_id":
                    waiver[field] = "其他设计"
                else:
                    waiver["artifacts_sha256"][field] = "0" * 64
                self.write_record()
                result = self.run_waiver_cli(("concept",))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "waiver_output").exists())

    def test_cli_waiver_preserves_risk_and_geometry_checks(self) -> None:
        original_risk = deepcopy(self.risk)
        for problem in ("accepted_high", "missing_coverage", "wrong_dimensions", "missing_structure"):
            with self.subTest(problem=problem):
                self.risk = deepcopy(original_risk)
                self.prepare_waiver()
                if problem == "accepted_high":
                    self.risk["risks"][0].update(severity="high", status="accepted", acceptance_rationale="愿意承担")
                elif problem == "missing_coverage":
                    del self.risk["coverage"]["assembly_motion"]
                elif problem == "wrong_dimensions":
                    self.source.write_text(self.source.read_text(encoding="utf-8").replace("'x': 10", "'x': 11"), encoding="utf-8")
                else:
                    self.definition.write_text('{"design_id":"fixture-v2"}', encoding="utf-8")
                self.write_record()
                self.record["concept_definition_sha256"] = BUILD.sha256(self.definition)
                self.record["gate_waivers"]["concept"]["artifacts_sha256"].update(
                    source=BUILD.sha256(self.source), risk_register=BUILD.sha256(self.risk_path), concept=BUILD.sha256(self.definition))
                self.write_record()
                result = self.run_waiver_cli(("concept",))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(list((self.root / "waiver_output").glob("*.stl")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
