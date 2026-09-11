from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import numpy as np

from .catalog import Catalog
from .models import (
    InventoryState,
    ItemInstance,
    Origin,
    UnresolvedRecognition,
)


@dataclass(slots=True)
class RegionProfile:
    id: str
    name: str
    kind: str
    rect: tuple[int, int, int, int]
    origin: Origin
    may_scroll: bool = False
    cell_size: int | None = None
    columns: int | None = None
    rows: int | None = None


@dataclass(slots=True)
class LayoutProfile:
    resolution: tuple[int, int]
    regions: list[RegionProfile]
    cell_size_range: tuple[int, int] = (42, 82)

    @classmethod
    def delta_force_1080p(cls) -> LayoutProfile:
        # The three grid rectangles are calibrated from a native 1920x1080,
        # 100%-scale Simplified Chinese inventory screen.
        return cls(
            resolution=(1920, 1080),
            regions=[
                RegionProfile(
                    "backpack",
                    "背包",
                    "backpack",
                    (882, 222, 1207, 677),
                    Origin.CARRIED,
                    may_scroll=True,
                    cell_size=65,
                    columns=5,
                    rows=7,
                ),
                RegionProfile(
                    "safe_box",
                    "安全箱",
                    "safe_box",
                    (882, 762, 1077, 957),
                    Origin.CARRIED,
                    cell_size=65,
                    columns=3,
                    rows=3,
                ),
                RegionProfile(
                    "loot",
                    "敌方物资",
                    "loot",
                    (1291, 147, 1616, 927),
                    Origin.LOOT,
                    may_scroll=True,
                    cell_size=65,
                    columns=5,
                    rows=12,
                ),
            ],
        )


@dataclass(slots=True)
class RecognitionCandidate:
    definition_id: str
    confidence: float


@dataclass(slots=True)
class DetectedObject:
    region_id: str
    rect: tuple[int, int, int, int]
    crop: np.ndarray
    visual_hash: str
    width_cells: int
    height_cells: int
    candidates: list[RecognitionCandidate] = field(default_factory=list)
    ocr_text: str = ""
    quantity: int = 1


@dataclass(slots=True)
class RecognitionResult:
    capture_id: str
    state: InventoryState
    needs_more_captures: bool
    frame_hash: str


class Recognizer(ABC):
    @abstractmethod
    def recognize(self, image: np.ndarray) -> RecognitionResult:
        raise NotImplementedError


class OcrEngine(ABC):
    @abstractmethod
    def recognize(self, crop: np.ndarray) -> str:
        raise NotImplementedError

    def recognize_lines(self, crop: np.ndarray) -> list[OcrLine]:
        return []

    def recognize_snippets(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        return [(self.recognize(crop), 0.0) for crop in crops]


@dataclass(slots=True, frozen=True)
class OcrLine:
    text: str
    confidence: float
    rect: tuple[int, int, int, int]


class NullOcrEngine(OcrEngine):
    def recognize(self, crop: np.ndarray) -> str:
        return ""


class RapidOcrEngine(OcrEngine):
    """Bundled, fully local Chinese OCR used for native 1080p inventory text."""

    def __init__(self) -> None:
        self.engine = None

    def _load_engine(self):
        import rapidocr
        from rapidocr import RapidOCR

        root = Path(rapidocr.__file__).parent / "models"
        filenames = {
            "Det": "PP-OCRv6_det_small.onnx",
            "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            "Rec": "PP-OCRv6_rec_small.onnx",
        }
        params = {"Global.log_level": "error"}
        for stage, filename in filenames.items():
            model_path = root / filename
            if not model_path.is_file():
                raise RuntimeError("本地 OCR 模型不完整，请重新安装识别组件")
            params[f"{stage}.model_path"] = str(model_path)
        return RapidOCR(params=params)

    def recognize(self, crop: np.ndarray) -> str:
        return " ".join(line.text for line in self.recognize_lines(crop))

    def recognize_snippets(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        from rapidocr.ch_ppocr_rec import TextRecInput

        if not crops:
            return []
        if self.engine is None:
            self.engine = self._load_engine()
        result = self.engine.text_rec(TextRecInput(img=crops))
        if result.txts is None:
            return [("", 0.0) for _ in crops]
        return list(zip(result.txts, result.scores, strict=True))

    def recognize_lines(self, crop: np.ndarray) -> list[OcrLine]:
        if crop.size == 0:
            return []
        if self.engine is None:
            self.engine = self._load_engine()
        result = self.engine(crop)
        if result.boxes is None or result.txts is None or result.scores is None:
            return []
        lines: list[OcrLine] = []
        for box, text, score in zip(
            result.boxes,
            result.txts,
            result.scores,
            strict=True,
        ):
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            x1, y1 = round(min(xs)), round(min(ys))
            x2, y2 = round(max(xs)), round(max(ys))
            lines.append(
                OcrLine(
                    text=str(text).strip(),
                    confidence=float(score),
                    rect=(x1, y1, max(1, x2 - x1), max(1, y2 - y1)),
                )
            )
        return lines


class OnnxCtcOcrEngine(OcrEngine):
    """轻量CTC文本识别器。

    用户需自行提供已获授权的ONNX文字识别模型和字典。本类不包含游戏素材或模型。
    """

    def __init__(self, model_path: str | Path, alphabet_path: str | Path):
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        self.alphabet = [""] + Path(alphabet_path).read_text(
            encoding="utf-8"
        ).splitlines()

    def recognize(self, crop: np.ndarray) -> str:
        if crop.size == 0:
            return ""
        height = 32
        width = max(32, min(320, round(crop.shape[1] * height / crop.shape[0])))
        resized = cv2.resize(crop, (width, height))
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        tensor = gray[None, None, :, :]
        logits = self.session.run(None, {self.input_name: tensor})[0]
        sequence = logits.argmax(axis=-1).reshape(-1).tolist()
        chars: list[str] = []
        previous = -1
        for index in sequence:
            if index != 0 and index != previous and index < len(self.alphabet):
                chars.append(self.alphabet[index])
            previous = index
        return "".join(chars).strip()


class TemplateMatcher:
    def __init__(self, catalog: Catalog, template_root: str | Path):
        self.catalog = catalog
        self.template_root = Path(template_root)
        self.templates: dict[str, list[np.ndarray]] = {}
        self.orb_templates: dict[str, list[np.ndarray]] = {}
        self._orb = cv2.ORB_create(nfeatures=350, fastThreshold=7)
        self.reload()

    def reload(self) -> None:
        self.templates.clear()
        self.orb_templates.clear()
        if not self.template_root.exists():
            return
        descriptor_cache: dict[Path, np.ndarray] = {}
        orb_cache: dict[Path, np.ndarray | None] = {}
        manifest_path = self.template_root / "icon-index.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for definition_id, relative_path in manifest.get("items", {}).items():
                if definition_id not in self.catalog.items:
                    continue
                path = self.template_root / str(relative_path)
                if path not in descriptor_cache:
                    image = cv2.imread(str(path), cv2.IMREAD_REDUCED_COLOR_4)
                    if image is None:
                        continue
                    descriptor_cache[path] = self._descriptor(image)
                    orb_cache[path] = self._orb_descriptor(image)
                self.templates.setdefault(definition_id, []).append(
                    descriptor_cache[path]
                )
                if orb_cache[path] is not None:
                    self.orb_templates.setdefault(definition_id, []).append(
                        orb_cache[path]
                    )
        for definition_id in self.catalog.items:
            item_dir = self.template_root / definition_id
            if not item_dir.exists():
                continue
            images: list[np.ndarray] = []
            orb_images: list[np.ndarray] = []
            for path in sorted(item_dir.glob("*")):
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is not None:
                    images.append(self._descriptor(image))
                    orb_descriptor = self._orb_descriptor(image)
                    if orb_descriptor is not None:
                        orb_images.append(orb_descriptor)
            if images:
                self.templates[definition_id] = images
            if orb_images:
                self.orb_templates[definition_id] = orb_images

    @staticmethod
    def _descriptor(image: np.ndarray) -> np.ndarray:
        resized = cv2.resize(image, (64, 64))
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        histogram = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(histogram, histogram)
        edges = cv2.Canny(cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY), 60, 160)
        return np.concatenate([histogram.flatten(), edges.flatten() / 255.0]).astype(
            np.float32
        )

    def _orb_descriptor(self, image: np.ndarray) -> np.ndarray | None:
        """Describe icon artwork independently of its tile background and text.

        Provider icons and in-game tiles use the same artwork at different scales.
        ORB local features are therefore substantially more reliable than comparing
        the whole tile histogram, which is dominated by rarity colours and labels.
        """

        if image.shape[1] > 400:
            scale = 400 / image.shape[1]
            image = cv2.resize(
                image,
                (400, max(32, round(image.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, descriptor = self._orb.detectAndCompute(gray, None)
        return descriptor

    @staticmethod
    def _orb_evidence(reference: np.ndarray, query: np.ndarray) -> float:
        if len(reference) < 2 or len(query) < 2:
            return 0.0
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        pairs = matcher.knnMatch(reference, query, k=2)
        evidence = 0.0
        for pair in pairs:
            if len(pair) != 2:
                continue
            best, second = pair
            if best.distance < 60 and best.distance < 0.78 * second.distance:
                evidence += max(0.0, (60.0 - best.distance) / 30.0)
        return evidence

    def definition_evidence(self, crop: np.ndarray, definition_id: str) -> float:
        query = self._orb_descriptor(crop)
        if query is None:
            return 0.0
        return max(
            (
                self._orb_evidence(reference, query)
                for reference in self.orb_templates.get(definition_id, [])
            ),
            default=0.0,
        )

    def match(
        self,
        crop: np.ndarray,
        width_cells: int,
        height_cells: int,
        limit: int = 3,
    ) -> list[RecognitionCandidate]:
        if not self.templates:
            return []
        descriptor = self._descriptor(crop)
        query_orb = self._orb_descriptor(crop)
        scored: list[tuple[float, float, str]] = []
        for definition_id, templates in self.templates.items():
            definition = self.catalog.items[definition_id]
            if (width_cells, height_cells) not in definition.orientations():
                continue
            distances = [
                float(np.linalg.norm(descriptor - template) / np.sqrt(descriptor.size))
                for template in templates
            ]
            histogram_confidence = max(0.0, min(1.0, 1.0 - min(distances)))
            orb_evidence = 0.0
            if query_orb is not None:
                orb_evidence = max(
                    (
                        self._orb_evidence(reference, query_orb)
                        for reference in self.orb_templates.get(definition_id, [])
                    ),
                    default=0.0,
                )
            score = orb_evidence + histogram_confidence * 0.35
            scored.append((score, histogram_confidence, definition_id))
        scored.sort(reverse=True)
        if not scored:
            return []
        best_score = scored[0][0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        margin = max(0.0, best_score - second_score)
        candidates: list[RecognitionCandidate] = []
        for index, (score, histogram_confidence, definition_id) in enumerate(
            scored[:limit]
        ):
            absolute = min(1.0, score / 8.0)
            separation = min(1.0, margin / 3.0) if index == 0 else 0.0
            confidence = max(
                histogram_confidence * 0.75,
                min(0.99, 0.35 + absolute * 0.45 + separation * 0.19),
            )
            candidates.append(
                RecognitionCandidate(
                    definition_id=definition_id,
                    confidence=confidence,
                )
            )
        return candidates


class GridDetector:
    def __init__(self, cell_size_range: tuple[int, int] = (42, 82)):
        self.cell_size_range = cell_size_range

    def detect_objects(
        self,
        image: np.ndarray,
        region: RegionProfile,
    ) -> list[DetectedObject]:
        x1, y1, x2, y2 = self._clip_rect(region.rect, image.shape[1], image.shape[0])
        roi = image[y1:y2, x1:x2]
        if roi.size == 0:
            return []
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        contrast = cv2.equalizeHist(gray)
        edges = cv2.Canny(contrast, 45, 135)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        cell = self._estimate_cell_size(edges)
        objects: list[DetectedObject] = []
        for contour in contours:
            rx, ry, width, height = cv2.boundingRect(contour)
            if width < cell * 0.55 or height < cell * 0.55:
                continue
            if width * height > roi.shape[0] * roi.shape[1] * 0.45:
                continue
            width_cells = max(1, round(width / cell))
            height_cells = max(1, round(height / cell))
            if width_cells > 12 or height_cells > 12:
                continue
            crop = roi[ry : ry + height, rx : rx + width].copy()
            visual_hash = self.visual_hash(crop)
            objects.append(
                DetectedObject(
                    region_id=region.id,
                    rect=(x1 + rx, y1 + ry, width, height),
                    crop=crop,
                    visual_hash=visual_hash,
                    width_cells=width_cells,
                    height_cells=height_cells,
                )
            )
        return self._remove_contained(objects)

    def _estimate_cell_size(self, edges: np.ndarray) -> int:
        minimum, maximum = self.cell_size_range
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180,
            threshold=50,
            minLineLength=minimum,
            maxLineGap=5,
        )
        lengths: list[int] = []
        if lines is not None:
            for line in lines[:, 0]:
                x1, y1, x2, y2 = map(int, line)
                length = int(round(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5))
                if minimum <= length <= maximum * 2:
                    lengths.append(length)
        if not lengths:
            return round((minimum + maximum) / 2)
        lengths.sort()
        return max(minimum, min(maximum, lengths[len(lengths) // 2]))

    @staticmethod
    def visual_hash(crop: np.ndarray) -> str:
        tiny = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (16, 16))
        digest = hashlib.sha1(tiny.tobytes(), usedforsecurity=False)
        return digest.hexdigest()

    @staticmethod
    def _clip_rect(
        rect: tuple[int, int, int, int], width: int, height: int
    ) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = rect
        return (
            max(0, min(width, x1)),
            max(0, min(height, y1)),
            max(0, min(width, x2)),
            max(0, min(height, y2)),
        )

    @staticmethod
    def _remove_contained(objects: list[DetectedObject]) -> list[DetectedObject]:
        result: list[DetectedObject] = []
        for candidate in sorted(
            objects, key=lambda obj: obj.rect[2] * obj.rect[3], reverse=True
        ):
            cx, cy, cw, ch = candidate.rect
            contained = False
            for kept in result:
                kx, ky, kw, kh = kept.rect
                if kx <= cx and ky <= cy and kx + kw >= cx + cw and ky + kh >= cy + ch:
                    contained = True
                    break
            if not contained:
                result.append(candidate)
        return result


class HybridRecognizer(Recognizer):
    def __init__(
        self,
        catalog: Catalog,
        template_root: str | Path,
        layout: LayoutProfile | None = None,
        ocr: OcrEngine | None = None,
        high_confidence: float = 0.9,
        medium_confidence: float = 0.65,
    ):
        self.catalog = catalog
        self.layout = layout or LayoutProfile.delta_force_1080p()
        self.ocr = ocr or NullOcrEngine()
        self.matcher = TemplateMatcher(catalog, template_root)
        self.detector = GridDetector(self.layout.cell_size_range)
        self.high_confidence = high_confidence
        self.medium_confidence = medium_confidence

    def recognize(self, image: np.ndarray) -> RecognitionResult:
        expected_width, expected_height = self.layout.resolution
        height, width = image.shape[:2]
        if (width, height) != (expected_width, expected_height):
            raise ValueError(
                f"仅支持 {expected_width}×{expected_height}，当前为 {width}×{height}"
            )
        capture_id = uuid4().hex
        frame_hash = hashlib.sha1(
            cv2.resize(image, (64, 36)).tobytes(), usedforsecurity=False
        ).hexdigest()
        items: list[ItemInstance] = []
        unresolved: list[UnresolvedRecognition] = []
        warnings: list[str] = []
        scrollable_content_seen = False

        for region in self.layout.regions:
            detected = self._detect_region(image, region)
            scrollable_content_seen |= region.may_scroll and bool(detected)
            for obj in detected:
                candidates = obj.candidates or self.matcher.match(
                    obj.crop,
                    obj.width_cells,
                    obj.height_cells,
                )
                if not obj.ocr_text:
                    obj.ocr_text = self.ocr.recognize(obj.crop)
                if obj.ocr_text:
                    exact = self.catalog.find_item(obj.ocr_text)
                    if exact:
                        candidates = [
                            RecognitionCandidate(exact.id, 0.99),
                            *[
                                candidate
                                for candidate in candidates
                                if candidate.definition_id != exact.id
                            ],
                        ][:3]
                obj.candidates = candidates
                metadata = {
                    "visual_hash": obj.visual_hash,
                    "rect": obj.rect,
                    "width_cells": obj.width_cells,
                    "height_cells": obj.height_cells,
                    "ocr_text": obj.ocr_text,
                    "origin": region.origin.value,
                    "source_container_id": region.id,
                    "quantity": obj.quantity,
                    "review_required": True,
                    "_crop": obj.crop.copy(),
                }
                if candidates and candidates[0].confidence >= self.medium_confidence:
                    best = candidates[0]
                    definition = self.catalog.items[best.definition_id]
                    if definition.category == "weapon":
                        metadata["weapon_details_pending"] = True
                    recognized_item = ItemInstance(
                        definition_id=best.definition_id,
                        origin=region.origin,
                        quantity=obj.quantity,
                        confidence=best.confidence,
                        source_capture_id=capture_id,
                        source_container_id=region.id,
                        metadata=metadata,
                    )
                    items.append(recognized_item)
                    # A name match alone cannot certify quantity, durability,
                    # instance boundaries or attachments from a single frame.
                    if metadata["review_required"]:
                        unresolved_metadata = dict(metadata)
                        unresolved_metadata["provisional_instance_id"] = (
                            recognized_item.instance_id
                        )
                        unresolved.append(
                            UnresolvedRecognition(
                                recognition_id=uuid4().hex,
                                confidence=best.confidence,
                                candidate_definition_ids=[
                                    candidate.definition_id for candidate in candidates
                                ],
                                source_capture_id=capture_id,
                                reason=(
                                    "整枪：请核对枪体、配件和装填弹药"
                                    if definition.category == "weapon"
                                    else "请核对名称、占格、数量及剩余耐久"
                                ),
                                metadata=unresolved_metadata,
                            )
                        )
                else:
                    unresolved.append(
                        UnresolvedRecognition(
                            recognition_id=uuid4().hex,
                            confidence=candidates[0].confidence if candidates else 0.0,
                            candidate_definition_ids=[
                                candidate.definition_id for candidate in candidates
                            ],
                            source_capture_id=capture_id,
                            reason=f"未知物品 {obj.width_cells}×{obj.height_cells}",
                            metadata=dict(metadata),
                        )
                    )

        if not self.matcher.templates:
            warnings.append("尚未安装物品模板；检测结果需要人工录入")
        if scrollable_content_seen:
            warnings.append("检测到可能滚动的区域；请确认底部内容已补拍")
        warnings.append("当前为可见区域识别草稿，可能漏项；逐项核对后才能给出替换建议")

        return RecognitionResult(
            capture_id=capture_id,
            state=InventoryState(
                containers={
                    key: value for key, value in self.catalog.containers.items()
                },
                items=items,
                unresolved=unresolved,
                capture_ids=[capture_id],
                warnings=warnings,
                complete_scan=not unresolved and not scrollable_content_seen,
            ),
            needs_more_captures=bool(unresolved) or scrollable_content_seen,
            frame_hash=frame_hash,
        )

    def _detect_region(
        self,
        image: np.ndarray,
        region: RegionProfile,
    ) -> list[DetectedObject]:
        if region.cell_size and region.columns and region.rows:
            objects = self._detect_named_grid_objects(image, region)
            if objects:
                return objects
        return self.detector.detect_objects(image, region)

    def _detect_named_grid_objects(
        self,
        image: np.ndarray,
        region: RegionProfile,
    ) -> list[DetectedObject]:
        x1, y1, x2, y2 = self.detector._clip_rect(
            region.rect,
            image.shape[1],
            image.shape[0],
        )
        roi = image[y1:y2, x1:x2]
        lines = self.ocr.recognize_lines(roi)
        if not lines:
            return []
        cell_size = int(region.cell_size or 1)
        # OCR occasionally joins adjacent one-cell labels into one text line.
        # Re-read those strips separately while keeping the original candidate.
        snippets: list[np.ndarray] = []
        snippet_rects: list[tuple[int, int, int, int]] = []
        for line in lines:
            lx, ly, lw, lh = line.rect
            if re.match(r"^\d[\d.\-]*[x×]\d", line.text):
                column, _ = self._grid_index(lx, cell_size)
                row, offset = self._grid_index(ly, cell_size)
                left, top = column * cell_size + 30, row * cell_size + 44
                right = min((column + 1) * cell_size - 1, roi.shape[1])
                bottom = min((row + 1) * cell_size, roi.shape[0])
                if offset <= 10 and left < right and top < bottom:
                    snippets.append(cv2.resize(
                        roi[top:bottom, left:right], None, fx=3, fy=3
                    ))
                    snippet_rects.append((left, top, right - left, bottom - top))
            if lw < cell_size * 1.5 or self._grid_index(ly, cell_size)[1] > 12:
                continue
            first_column = lx // cell_size
            last_column = min(int(region.columns or 1) - 1, (lx + lw - 1) // cell_size)
            for column in range(first_column, last_column + 1):
                left = max(lx, column * cell_size + 2)
                right = min(lx + lw, (column + 1) * cell_size - 2, roi.shape[1])
                top, bottom = max(0, ly - 1), min(roi.shape[0], ly + lh + 1)
                if right - left < 15 or bottom <= top:
                    continue
                snippets.append(roi[top:bottom, left:right].copy())
                snippet_rects.append((left, top, right - left, bottom - top))
        for (text, score), rect in zip(
            self.ocr.recognize_snippets(snippets), snippet_rects, strict=True
        ):
            if score >= 0.7:
                lines.append(OcrLine(text, float(score), rect))
        groups: dict[tuple[int, int], list[OcrLine]] = {}
        for line in lines:
            line_x, line_y, _, _ = line.rect
            column, column_offset = self._grid_index(line_x, cell_size)
            row, row_offset = self._grid_index(line_y, cell_size)
            column = max(0, min(int(region.columns or 1) - 1, column))
            row = max(0, min(int(region.rows or 1) - 1, row))
            # Item names are rendered in the top-left quarter of their first cell.
            if row_offset > 24 or column_offset > 22:
                continue
            groups.setdefault((column, row), []).append(line)

        objects: list[DetectedObject] = []
        occupied_starts: set[tuple[int, int]] = set()
        for (column, row), cell_lines in sorted(groups.items(), key=lambda pair: pair[0][::-1]):
            text_variants = [line.text for line in cell_lines]
            if len(text_variants) > 1:
                text_variants.append("".join(text_variants))
            matched: dict[str, tuple[float, float]] = {}
            for text_value in text_variants:
                for definition, text_score in self.catalog.match_text(text_value):
                    previous = matched.get(definition.id, (0.0, 0.0))
                    line_confidence = max(
                        (
                            line.confidence
                            for line in cell_lines
                            if line.text in text_value or text_value in line.text
                        ),
                        default=max(line.confidence for line in cell_lines),
                    )
                    matched[definition.id] = max(
                        previous,
                        (text_score, line_confidence),
                    )
            if not matched or (column, row) in occupied_starts:
                continue

            model_tokens = {
                re.sub(r"[^0-9a-z]+", "", value.casefold())
                for value in text_variants
                if len(value.strip()) <= 8
                and re.search(r"[A-Za-z]", value)
                and "mm" not in value.casefold()
            }

            scored: list[tuple[float, float, str, np.ndarray, int, int]] = []
            for definition_id, (text_score, line_confidence) in matched.items():
                definition = self.catalog.items[definition_id]
                for width_cells, height_cells in definition.orientations():
                    if (
                        column + width_cells > int(region.columns or 0)
                        or row + height_cells > int(region.rows or 0)
                    ):
                        continue
                    crop = roi[
                        row * cell_size : (row + height_cells) * cell_size,
                        column * cell_size : (column + width_cells) * cell_size,
                    ].copy()
                    visual_evidence = self.matcher.definition_evidence(
                        crop,
                        definition_id,
                    )
                    combined = (
                        text_score * 0.72
                        + line_confidence * 0.08
                        + min(1.0, visual_evidence / 8.0) * 0.20
                    )
                    compact_name = re.sub(
                        r"[^0-9a-z]+",
                        "",
                        definition.name.casefold(),
                    )
                    if definition.category == "weapon" and any(
                        compact_name.startswith(token) for token in model_tokens
                    ):
                        combined += 0.18
                    if definition.category == "ammo":
                        subtype_tokens = {
                            token.casefold()
                            for token in re.findall(r"\b[A-Za-z]{2,4}\b", " ".join(text_variants))
                            if token.casefold() not in {"mm", "lm"}
                        }
                        if subtype_tokens and any(
                            token in definition.name.casefold().split()
                            for token in subtype_tokens
                        ):
                            combined += 0.15
                    scored.append(
                        (
                            combined,
                            visual_evidence,
                            definition_id,
                            crop,
                            width_cells,
                            height_cells,
                        )
                    )
            if not scored:
                continue
            scored.sort(
                key=lambda row: (row[0], row[1], row[2], row[4], row[5]),
                reverse=True,
            )
            best = scored[0]
            candidate_rows: list[RecognitionCandidate] = []
            seen_ids: set[str] = set()
            for score, _, definition_id, _, _, _ in scored:
                if definition_id in seen_ids:
                    continue
                seen_ids.add(definition_id)
                candidate_rows.append(
                    RecognitionCandidate(
                        definition_id=definition_id,
                        confidence=min(0.99, score),
                    )
                )
                if len(candidate_rows) == 3:
                    break
            _, _, best_definition_id, crop, width_cells, height_cells = best
            absolute_x = x1 + column * cell_size
            absolute_y = y1 + row * cell_size
            quantity = self._quantity_for_object(
                lines,
                column,
                row,
                width_cells,
                height_cells,
                cell_size,
                best_definition_id,
            )
            objects.append(
                DetectedObject(
                    region_id=region.id,
                    rect=(
                        absolute_x,
                        absolute_y,
                        width_cells * cell_size,
                        height_cells * cell_size,
                    ),
                    crop=crop,
                    visual_hash=self.detector.visual_hash(crop),
                    width_cells=width_cells,
                    height_cells=height_cells,
                    candidates=candidate_rows,
                    ocr_text=" ".join(line.text for line in cell_lines),
                    quantity=quantity,
                )
            )
            occupied_starts.add((column, row))
        return objects

    @staticmethod
    def _grid_index(coordinate: int, cell_size: int) -> tuple[int, int]:
        index, offset = divmod(max(0, coordinate), cell_size)
        if offset >= cell_size - 4:
            return index + 1, 0
        return index, offset

    def _quantity_for_object(
        self,
        lines: list[OcrLine],
        column: int,
        row: int,
        width_cells: int,
        height_cells: int,
        cell_size: int,
        definition_id: str,
    ) -> int:
        definition = self.catalog.items[definition_id]
        if definition.stack_limit <= 1:
            return 1
        left = column * cell_size
        top = row * cell_size
        right = (column + width_cells) * cell_size
        bottom = (row + height_cells) * cell_size
        values: list[int] = []
        for line in lines:
            x, y, width, height = line.rect
            match = re.fullmatch(r"\s*(\d{1,3})\s*", line.text)
            if (
                match
                and left <= x + width / 2 < right
                and top + height_cells * cell_size * 0.55 <= y + height / 2 < bottom
            ):
                values.append(int(match.group(1)))
        return max(1, min(definition.stack_limit, max(values, default=1)))


def save_layout_profile(path: str | Path, profile: LayoutProfile) -> None:
    payload: dict[str, Any] = {
        "resolution": list(profile.resolution),
        "cell_size_range": list(profile.cell_size_range),
        "regions": [
            {
                "id": region.id,
                "name": region.name,
                "kind": region.kind,
                "rect": list(region.rect),
                "origin": region.origin.value,
                "may_scroll": region.may_scroll,
                "cell_size": region.cell_size,
                "columns": region.columns,
                "rows": region.rows,
            }
            for region in profile.regions
        ],
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_layout_profile(path: str | Path) -> LayoutProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return LayoutProfile(
        resolution=tuple(map(int, payload["resolution"])),
        cell_size_range=tuple(map(int, payload.get("cell_size_range", [42, 82]))),
        regions=[
            RegionProfile(
                id=str(region["id"]),
                name=str(region["name"]),
                kind=str(region["kind"]),
                rect=tuple(map(int, region["rect"])),
                origin=Origin(region["origin"]),
                may_scroll=bool(region.get("may_scroll", False)),
                cell_size=(
                    int(region["cell_size"])
                    if region.get("cell_size") is not None
                    else None
                ),
                columns=(
                    int(region["columns"])
                    if region.get("columns") is not None
                    else None
                ),
                rows=int(region["rows"]) if region.get("rows") is not None else None,
            )
            for region in payload["regions"]
        ],
    )
