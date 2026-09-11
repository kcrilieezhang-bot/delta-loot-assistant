"""Position-independent name proposals, not certified object detection."""
from __future__ import annotations

import re

import numpy as np

from .catalog import Catalog, _normalize_ocr_text
from .recognition import RapidOcrEngine
from .spatial_recognition import collect_lines, visible_panels, weapon_rect


def propose_items(image: np.ndarray, catalog: Catalog, ocr: RapidOcrEngine,
                  enhanced: bool = False) -> list[dict]:
    height, width = image.shape[:2]
    scale = min(width / 1920, height / 1080)
    cell = max(24, round(64 * scale))
    lines = collect_lines(image, ocr, enhanced)
    panels = visible_panels(image, cell) if enhanced else []
    telemetry_y = [line.rect[1] for line in lines
                   if re.search(r"\bFPS\b|GPU|显存|VRAM", line.text, re.IGNORECASE)]
    weapon_aliases = {}
    for definition in catalog.items.values():
        if definition.category == "weapon":
            alias = re.sub(r"(突击步枪|狙击步枪|战斗步枪|精确射手步枪|射手步枪|"
                           r"冲锋枪|轻机枪|霰弹枪)$",
                           "", definition.name)
            weapon_aliases[_normalize_ocr_text(alias)] = definition.id
    anchors = []
    for line in lines:
        text = line.text.replace(" ", "")
        for name, scope in (("背包", "backpack"), ("安全箱", "safe_box"),
                            ("散落物", "loot"), ("口袋", "carried"), ("胸挂", "carried")):
            if text.startswith(name) and (name == "散落物" or re.search(r"\d|[:：]", text)):
                anchors.append((line.rect[0], line.rect[1], scope))
    proposals = []
    cache = {}
    for line in sorted(lines, key=lambda obj: (obj.rect[1], obj.rect[0])):
        text = line.text.strip()
        if any(abs(line.rect[1] - y) < max(12, line.rect[3] * 1.5) for y in telemetry_y):
            continue
        if len(text) < 2 or re.fullmatch(r"[\d\s.,/+:：%]+", text):
            continue
        if any(word in text for word in ("本局收获", "剩余", "背包：", "安全箱：", "口袋：")):
            continue
        caliber_only = re.fullmatch(
            r"[\d.]+\s*[x×]\s*[\d.]+\s*(mm|毫米)?", text, re.IGNORECASE
        )
        x, y, tw, th = line.rect
        variants = [text]
        # Nearby second lines often contain the ammunition subtype; prefer their combined evidence.
        for other in lines:
            ox, oy, _ow, oh = other.rect
            if abs(ox - x) < cell * .45 and 0 < oy - y < max(th, oh) * 2.8:
                if not re.fullmatch(r"[\d\s.,/+:：%]+", other.text):
                    variants.append(text + " " + other.text)
        if caliber_only:
            # A caliber alone is not a stack; a nearby subtype can identify actual ammunition.
            variants = variants[1:]
            if not variants:
                continue
        best = {}
        for variant in variants:
            if variant not in cache:
                cache[variant] = catalog.match_text(variant, limit=3, minimum_score=.84)
            for definition, score in cache[variant]:
                score *= .96 if len(variants) > 1 and variant == text else 1
                best[definition.id] = max(best.get(definition.id, 0), score)
        weapon_id = weapon_aliases.get(_normalize_ocr_text(text))
        if weapon_id:
            best[weapon_id] = 1.05
        if not best:
            continue
        candidates = sorted(best, key=lambda key: -best[key])[:3]
        if any(abs(p["rect"][0] - x) < cell * .45 and
               abs(p["rect"][1] - y) < cell * .55 for p in proposals):
            continue
        definition = catalog.items[candidates[0]]
        # Crop dimensions are proposals only. Rotation/skin/occlusion requires manual review.
        rw = min(width - x, max(tw, min(definition.width, 5) * cell))
        rh = min(height - y, max(th, min(definition.height, 6) * cell))
        nearby = [(ay, scope) for ax, ay, scope in anchors
                  if ay < y and -cell * .3 <= x - ax < cell * 7.5]
        scope = max(nearby, default=(0, "unassigned"))[1]
        quantity = 1
        if definition.category == "ammo":
            numbers = [other for other in lines if re.fullmatch(r"\d{1,3}", other.text.strip())
                       and x <= other.rect[0] < x + rw and y < other.rect[1] < y + rh]
            if numbers:
                quantity = max(1, int(max(numbers, key=lambda obj: obj.rect[1]).text))
        rect = [x, y, rw, rh]
        if definition.category == "weapon":
            rect = weapon_rect(x, y, definition, cell, lines, width, height)
        else:
            # Slot borders are more reliable than storage dimensions for worn equipment.
            enclosing = [p for p in panels if -3 <= x - p[0] <= cell * .2 and
                         -3 <= y - p[1] <= cell * .2 and p[2] >= tw * .85 and
                         p[2] * p[3] <= rw * rh * 1.4]
            if enclosing:
                rect = list(min(enclosing, key=lambda p: p[2] * p[3]))
        # Equipment left of the detected inventory column is carried, not ground loot.
        if scope == "unassigned" and anchors:
            inventory_x = min(ax for ax, _ay, s in anchors if s != "loot") if any(
                s != "loot" for _ax, _ay, s in anchors) else None
            if inventory_x and inventory_x - cell * 5 < x < inventory_x:
                scope = "carried"
        proposals.append({"rect": rect, "scope": scope,
                          "candidates": candidates, "quantity": quantity, "ocr": text})
        if len(proposals) >= 160:
            break
    if enhanced:
        for line in lines:
            x, y, tw, th = line.rect
            if any(p["rect"][0] - 8 <= x < p["rect"][0] + p["rect"][2] - 8 and
                   p["rect"][1] - 8 <= y < p["rect"][1] + p["rect"][3] - 8
                   for p in proposals):
                continue
            enclosing = [p for p in panels if -3 <= x-p[0] <= cell*.2 and
                         -3 <= y-p[1] <= cell*.2 and tw <= p[2]]
            nearby = [(ay, scope) for ax, ay, scope in anchors
                      if ay < y and -cell*.3 <= x-ax < cell*7.5]
            if not nearby:
                continue
            numbers = [other for other in lines if re.fullmatch(r"\d{1,3}", other.text)
                       and x <= other.rect[0] < x+cell and y+cell*.6 < other.rect[1] < y+cell]
            caliber = re.search(r"\d[.\-]\d+\s*[x×]\s*\d", line.text)
            if not (caliber and numbers) and not (enclosing and
                    re.search(r"[\u4e00-\u9fff]{2,}", line.text)):
                continue
            if any(word in line.text for word in ("背包", "口袋", "安全箱", "收获", "剩余")):
                continue
            rect = list(min(enclosing, key=lambda p: p[2]*p[3])) if enclosing else [
                x, y, min(cell, width-x), min(cell, height-y)]
            proposals.append({"rect": rect, "scope": max(nearby)[1], "candidates": [],
                              "quantity": int(numbers[0].text) if numbers else 1,
                              "ocr": line.text + "（检测到区域，型号未确定）"})
            if len(proposals) >= 160:
                break
    return proposals
