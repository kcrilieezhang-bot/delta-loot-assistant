"""Offline, reviewable screenshot estimate: python -m ...screenshot_report IMAGE."""
from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from .catalog import Catalog
from .orzice import OrziceCatalogRepository
from .paths import default_database_path, layout_profile_path, sample_catalog_path, template_root
from .pricing import LocalPricePackProvider, ValuationService
from .recognition import HybridRecognizer, RapidOcrEngine, load_layout_profile
from .value_ranking import InventoryValueRanker


def inspect_image(path: Path) -> str:
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法读取图片")
    catalog = Catalog.load(sample_catalog_path())
    catalog.items.update(OrziceCatalogRepository(default_database_path()).active_definitions())
    profile_path = layout_profile_path()
    started = perf_counter()
    recognizer = HybridRecognizer(
        catalog, template_root(), ocr=RapidOcrEngine(),
        layout=load_layout_profile(profile_path) if profile_path.exists() else None,
    )
    ready = perf_counter()
    result = recognizer.recognize(image)
    finished = perf_counter()
    provider = LocalPricePackProvider(default_database_path())
    ranker = InventoryValueRanker(catalog, ValuationService(provider))
    pack = provider.current_pack()
    rows = ranker.rank_carried_items(result.state)
    lines = [
        "# 截图估价草稿（待核对）", "",
        f"价格快照：{pack.generated_at.isoformat() if pack else '无'}。仅使用本地数据。", "",
        f"识别候选 {len(result.state.items)} 件；待核对 {len(result.state.unresolved)} 项。",
        f"初始化 {ready - started:.2f} 秒；截图分析 {finished - ready:.2f} 秒。", "",
        "此表按单格参考价升序，含未经确认候选，不能直接作为丢弃建议。",
        "可能漏识别，枪械价格仅包含目录中的枪体，配件、弹药、耐久和占格需核对。", "",
        "| 位置 | 识别候选 | 数量 | 单价 | 参考总价 | 目录占格 | 单格参考价 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        price = f"{row.unit_price:,}" if row.price_available else "未定价"
        total = f"{row.total_value:,}" if row.price_available else "未定价"
        per_cell = f"{row.value_per_cell:,}" if row.price_available else "未定价"
        lines.append(
            f"| {row.source_container_id} | {row.name} | {row.quantity} | {price} | "
            f"{total} | {row.occupied_cells} | {per_cell} |"
        )
    lines.extend(["", "## 待核对项目", ""])
    for entry in result.state.unresolved:
        names = [catalog.items[key].name for key in entry.candidate_definition_ids]
        lines.append(
            f"- {entry.metadata.get('source_container_id', '')} "
            f"{entry.metadata.get('rect', '')}：{entry.metadata.get('ocr_text', '')}；"
            f"候选 {' / '.join(names) or '未知'}。{entry.reason}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="从本地截图和价格快照生成待核对估价草稿")
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect_image(args.image)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"报告已保存：{args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
