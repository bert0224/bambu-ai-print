#!/usr/bin/env python3
"""在不加载 CadQuery 的独立进程中渲染 STL 组件预览。"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染由多个 STL 组成的三视图。")
    parser.add_argument("scene", type=Path, help="组件场景 JSON")
    parser.add_argument("output", type=Path, help="预览 PNG")
    return parser.parse_args()


def read_stl(path: Path) -> list[list[tuple[float, float, float]]]:
    data = path.read_bytes()
    if len(data) >= 84:
        triangle_count = struct.unpack_from("<I", data, 80)[0]
        if 84 + triangle_count * 50 == len(data):
            faces = []
            for index in range(triangle_count):
                values = struct.unpack_from("<12fH", data, 84 + index * 50)
                faces.append(
                    [
                        (values[3], values[4], values[5]),
                        (values[6], values[7], values[8]),
                        (values[9], values[10], values[11]),
                    ]
                )
            return faces

    vertices = []
    for line in data.decode("ascii", errors="ignore").splitlines():
        words = line.strip().split()
        if len(words) == 4 and words[0].lower() == "vertex":
            vertices.append((float(words[1]), float(words[2]), float(words[3])))
    if len(vertices) == 0 or len(vertices) % 3 != 0:
        raise ValueError(f"无法解析 STL：{path}")
    return [vertices[index : index + 3] for index in range(0, len(vertices), 3)]


def render(scene: list[dict[str, Any]], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False

    prepared = []
    all_points: list[tuple[float, float, float]] = []
    for item in scene:
        faces = read_stl(Path(item["path"]))
        for face in faces:
            all_points.extend(face)
        prepared.append({**item, "faces": faces})
    if not all_points:
        raise ValueError("场景中没有可渲染网格。")

    mins = [min(point[index] for point in all_points) for index in range(3)]
    maxs = [max(point[index] for point in all_points) for index in range(3)]
    spans = [max(maxs[index] - mins[index], 1.0) for index in range(3)]
    padding = max(spans) * 0.08

    views = [
        (24, -52, "等轴测"),
        (0, -90, "正视图"),
        (90, -90, "俯视图"),
    ]
    figure = plt.figure(figsize=(15, 5), dpi=180, facecolor="#F5F6F8")
    for position, (elev, azim, title) in enumerate(views, start=1):
        axis = figure.add_subplot(1, 3, position, projection="3d")
        for item in prepared:
            axis.add_collection3d(
                Poly3DCollection(
                    item["faces"],
                    facecolor=item.get("color", "#F28C28"),
                    edgecolor="#263238",
                    linewidth=0.12,
                    alpha=float(item.get("alpha", 1.0)),
                )
            )
        axis.set_xlim(mins[0] - padding, maxs[0] + padding)
        axis.set_ylim(mins[1] - padding, maxs[1] + padding)
        axis.set_zlim(min(0.0, mins[2]) - padding * 0.2, maxs[2] + padding)
        axis.set_box_aspect((spans[0], spans[1], spans[2]))
        axis.view_init(elev=elev, azim=azim)
        axis.set_proj_type("ortho")
        axis.set_title(title, fontsize=12, pad=4)
        axis.set_axis_off()
    figure.suptitle("参数化模型预览（绿色为 PCB 假体）", fontsize=14, y=0.98)
    figure.tight_layout()
    figure.savefig(output, bbox_inches="tight", pad_inches=0.08)
    plt.close(figure)


def main() -> int:
    args = parse_args()
    scene = json.loads(args.scene.read_text(encoding="utf-8"))
    render(scene, args.output)
    print(f"预览已生成：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
