from __future__ import annotations


def test_catalog_fuzzy_matches_spacing_and_partial_ocr(catalog) -> None:
    exact = catalog.match_text("高价值 小物品")
    partial = catalog.match_text("测试步枪")

    assert exact[0][0].id == "small_high"
    assert exact[0][1] == 1.0
    assert partial[0][0].id == "weapon"


def test_catalog_ignores_quantity_only_ocr(catalog) -> None:
    assert catalog.match_text("60") == []
