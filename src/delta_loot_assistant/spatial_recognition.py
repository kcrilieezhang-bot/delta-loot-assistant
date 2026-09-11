"""Multi-scale OCR and item orientation from visible labels, not fixed UI coordinates."""
from __future__ import annotations

import re

import cv2

from .catalog import _normalize_ocr_text
from .recognition import OcrLine


def collect_lines(image, ocr, enhanced=False):
    lines = list(ocr.recognize_lines(image))
    if not enhanced:
        return lines
    height, width = image.shape[:2]
    # Overlap keeps labels on tile seams intact. Scale coordinates back before merging.
    overlap = max(24, round(min(width / 1920, height / 1080) * 70))
    for row in range(2):
        for col in range(3):
            x = max(0, col * width // 3 - overlap)
            y = max(0, row * height // 2 - overlap)
            right = min(width, (col + 1) * width // 3 + overlap)
            bottom = min(height, (row + 1) * height // 2 + overlap)
            crop = image[y:bottom, x:right]
            zoom = min(2.0, 1600 / max(crop.shape[:2]))
            scaled = cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
            for line in ocr.recognize_lines(scaled):
                lx, ly, w, h = line.rect
                mapped = OcrLine(line.text, line.confidence,
                                 (x + round(lx / zoom), y + round(ly / zoom),
                                  max(1, round(w / zoom)), max(1, round(h / zoom))))
                duplicate = next((i for i, old in enumerate(lines)
                                  if same_label(old, mapped)), None)
                if duplicate is None:
                    lines.append(mapped)
                elif mapped.confidence > lines[duplicate].confidence:
                    lines[duplicate] = mapped
    # Small ammo subtypes and merged adjacent labels need a second, tighter crop.
    cell = max(24, round(min(width / 1920, height / 1080) * 64))
    extras = []
    crop_count = 0
    visited = []
    for line in list(lines):
        x, y, w, h = line.rect
        caliber = re.search(r"\d[.\-]\d+\s*[x×]\s*\d", line.text, re.IGNORECASE)
        merged = w > cell * 1.5 and h < cell * .4 and len(line.text) >= 6
        if not caliber and not merged:
            continue
        # Limit work and skip UI/stream text without nearby inventory-like repeated labels.
        neighbors = sum(abs(other.rect[1] - y) < cell * .4 and
                        abs(other.rect[0] - x) < cell * 5 for other in lines)
        if neighbors < 2:
            continue
        for offset in range(min(3, max(1, round(w / cell)))):
            left = x + offset * cell
            if any(abs(left - ox) < cell * .4 and abs(y - oy) < cell * .45
                   for ox, oy in visited):
                continue
            if crop_count >= 16:
                break
            visited.append((left, y))
            crop_count += 1
            crop = image[max(0, y-2):min(height, y+round(cell*.62)),
                         left:min(width, left+cell)]
            if crop.size == 0:
                continue
            for extra in ocr.recognize_lines(cv2.resize(crop, None, fx=3, fy=3)):
                ex, ey, ew, eh = extra.rect
                mapped = OcrLine(extra.text, extra.confidence,
                                 (left + round(ex/3), max(0, y-2)+round(ey/3),
                                  max(1, round(ew/3)), max(1, round(eh/3))))
                if not any(same_label(old, mapped) for old in lines + extras):
                    extras.append(mapped)
        if len(extras) >= 100 or crop_count >= 16:
            break
    return lines + extras


def same_label(a, b):
    ax, ay, aw, ah = a.rect
    bx, by, bw, bh = b.rect
    # Do not collapse repeated identical items in adjacent cells.
    nearby = abs(ax - bx) < max(5, min(aw, bw) * .3) and abs(ay - by) < max(5, ah, bh) * .6
    return nearby and _normalize_ocr_text(a.text) == _normalize_ocr_text(b.text)


def weapon_rect(x, y, definition, cell, lines, width, height):
    """Choose orientation using the ammo counter at the item's bottom-right corner."""
    sizes = [(definition.width, definition.height), (definition.height, definition.width)]
    scored = []
    for cols, rows in sizes:
        expected_right, expected_bottom = x + cols * cell, y + rows * cell
        counters = [line for line in lines
                    if re.fullmatch(r"\d{1,3}\s*/\s*\d{1,3}", line.text.strip())
                    and x <= line.rect[0] < expected_right + cell * .3
                    and y + cell * .4 < line.rect[1] < expected_bottom + cell * .4]
        for counter in counters:
            cx, cy, cw, ch = counter.rect
            error = abs(cx + cw - expected_right) + abs(cy + ch - expected_bottom)
            if error < cell * .8:
                scored.append((error, cols, rows, cx + cw, cy + ch))
    if scored:
        _, _cols, _rows, right, bottom = min(scored)
        return [x, y, min(width - x, right - x + 3), min(height - y, bottom - y + 3)]
    return [x, y, min(width - x, definition.width * cell),
            min(height - y, definition.height * cell)]


def visible_panels(image, cell):
    edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 35, 85)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    panels = set()
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if cell * .75 <= w <= cell * 6 and cell * .75 <= h <= cell * 8:
            outline = cv2.approxPolyDP(contour, .015 * cv2.arcLength(contour, True), True)
            if len(outline) == 4 and cv2.contourArea(contour) > w * h * .85:
                panels.add((x, y, w, h))
    return panels
