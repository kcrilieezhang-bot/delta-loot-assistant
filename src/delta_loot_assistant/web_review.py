"""Local screenshot review; the paid snapshot is opened strictly read-only."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

from .adaptive_recognition import propose_items
from .advisor import build_advice
from .catalog import Catalog
from .llm_settings import LlmSettings
from .models import PricePack
from .orzice import item_definition_from_row
from .recognition import (
    HybridRecognizer,
    LayoutProfile,
    RapidOcrEngine,
    TemplateMatcher,
    load_layout_profile,
)

MAX_UPLOAD = 15 * 1024 * 1024
SCOPES = {"backpack", "safe_box", "loot", "carried", "unassigned"}
RANKING_SCOPES = SCOPES | {"all"}
SPECIAL = {"weapon", "armor", "helmet", "equipment", "consumable", "key"}


class ReviewError(ValueError):
    pass


def integer(value, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ReviewError(f"{label}必须是 {low}–{high} 之间的整数")
    return value


def optional_price(value):
    return None if value is None else integer(value, "价格", 0, 1_000_000_000)


def decode_upload(raw: bytes) -> np.ndarray:
    if not raw or len(raw) > MAX_UPLOAD:
        raise ReviewError("请选择不超过 15 MB 的 PNG 或 JPEG 截图")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ReviewError("只支持 PNG 或 JPEG 图片")
            width, height = image.size
            if min(width, height) < 240 or max(width, height) > 4096 or width * height > 12_000_000:
                raise ReviewError("图片边长需为 240–4096 像素，且不超过 1200 万像素")
            return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    except ReviewError:
        raise
    except Exception as exc:
        raise ReviewError("图片无法解码，请重新选择有效截图") from exc


class Snapshot:
    def __init__(self, database: Path, templates: Path):
        self.templates = templates.resolve()
        if not database.is_file():
            raise ReviewError("未找到本地物品数据库；网页不会自动下载或同步物价")
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            records = db.execute(
                "SELECT payload FROM catalog_items WHERE snapshot_id = "
                "(SELECT id FROM catalog_snapshots WHERE active=1 ORDER BY id DESC LIMIT 1)"
            ).fetchall()
            price = db.execute(
                "SELECT payload FROM price_packs WHERE active=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        definitions = [item_definition_from_row(json.loads(row[0])) for row in records]
        self.pack = PricePack.from_dict(json.loads(price[0])) if price else None
        self.catalog = Catalog(1, self.pack.season if self.pack else "unknown",
                               {item.id: item for item in definitions}, {})
        self.icons = {}
        index = templates / "icon-index.json"
        if index.is_file():
            raw = json.loads(index.read_text(encoding="utf-8"))
            # The desktop index is an object with an items mapping.
            self.icons = raw.get("items", raw)

    def metadata(self) -> dict:
        generated = self.pack.generated_at if self.pack else None
        if generated and generated.tzinfo is None:
            generated = generated.replace(tzinfo=UTC)
        return {
            "catalog_count": len(self.catalog.items),
            "price_count": len(self.pack.prices) if self.pack else 0,
            "generated_at": generated.isoformat() if generated else None,
            "season": self.pack.season if self.pack else "未知",
            "region": self.pack.region if self.pack else "未知",
            "stale": not generated or (datetime.now(UTC) - generated).total_seconds() > 86400,
        }

    def item(self, definition_id: str) -> dict:
        definition = self.catalog.items.get(definition_id)
        if not definition:
            raise ReviewError("物品不在本地目录中，请重新搜索选择")
        entry = self.pack.prices.get(definition_id) if self.pack else None
        return {
            "id": definition.id, "name": definition.name, "category": definition.category,
            "cells": definition.width * definition.height,
            "unit_price": entry.unit_price if entry else None,
            "needs_total": definition.category in SPECIAL, "grade": definition.grade,
        }

    def icon(self, definition_id: str) -> bytes | None:
        record = self.icons.get(definition_id)
        relative = record.get("path") if isinstance(record, dict) else record
        if not isinstance(relative, str):
            return None
        path = (self.templates / relative).resolve()
        if path.is_relative_to(self.templates) and path.suffix.lower() == ".png" and path.is_file():
            return path.read_bytes()
        return None


class CorrectionStore:
    """Separate local DB; only opt-in confirmed crops contain image pixels."""
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS corrections (image_hash TEXT, row_key TEXT, "
                "payload TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "PRIMARY KEY(image_hash, row_key))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS confirmed_crops (image_hash TEXT, row_key TEXT, "
                "png BLOB NOT NULL, label TEXT NOT NULL, PRIMARY KEY(image_hash, row_key))"
            )

    def load(self, image_hash: str) -> dict:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT row_key,payload FROM corrections WHERE image_hash=?", (image_hash,)
            ).fetchall()
        return {key: json.loads(payload) for key, payload in rows}

    def save(self, image_hash: str, row: dict, png: bytes | None = None):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO corrections VALUES (?,?,?,?) ON CONFLICT(image_hash,row_key) "
                "DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (image_hash, row["key"], json.dumps(row, ensure_ascii=False),
                 datetime.now(UTC).isoformat()),
            )
            if png is not None:
                db.execute(
                    "INSERT INTO confirmed_crops VALUES (?,?,?,?) "
                    "ON CONFLICT(image_hash,row_key) DO UPDATE SET "
                    "png=excluded.png,label=excluded.label",
                    (image_hash, row["key"], png, row["definition_id"]),
                )
            else:
                # Keep previously consented sample labels in sync with subsequent corrections.
                db.execute(
                    "UPDATE confirmed_crops SET label=? WHERE image_hash=? AND row_key=?",
                    (row["definition_id"], image_hash, row["key"]),
                )


def validate_rect(rect, size=(1920, 1080)) -> list[int]:
    if not isinstance(rect, list) or len(rect) != 4:
        raise ReviewError("请在截图上框选物品")
    x, y, w, h = [integer(v, "框选坐标", 0, 4096) for v in rect]
    if w < 5 or h < 5 or x + w > size[0] or y + h > size[1]:
        raise ReviewError("框选区域超出截图或太小")
    return [x, y, w, h]


def draft_row(rect: list[int], scope: str, candidates: list[str], quantity: int = 1,
              ocr: str = "", manual: bool = False) -> dict:
    return {
        "key": ":".join(map(str, rect)), "rect": rect, "scope": scope,
        "definition_id": candidates[0] if candidates else "", "candidates": candidates,
        "quantity": quantity, "cells": 1, "manual_unit_price": None, "manual_total": None,
        "confirmed": False, "excluded": False, "locked": False, "manual": manual,
        "ocr": ocr[:160], "recalled": False,
    }


class ReviewService:
    def __init__(self, snapshot: Snapshot, store: CorrectionStore, recognizer_factory=None):
        self.snapshot, self.store = snapshot, store
        self.factory = recognizer_factory or self._recognizer
        self.recognizer = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-ocr")
        self.lock = threading.RLock()
        self.session = None
        self.image = None
        self.crop_lock = threading.Lock()
        self.crop_ocr = None
        self.crop_matcher = None
        self.agent = None
        self.agent_lock = threading.Lock()
        self.llm_settings = LlmSettings(store.path.parent)

    def _recognizer(self):
        profile = self.snapshot.templates.parent / "layout_1080p.json"
        layout = load_layout_profile(profile) if profile.is_file() else None
        if self.session.get("profile") == "sample02":
            layout = LayoutProfile.delta_force_1080p()
            regions = {
                "backpack": ((875, 309, 1195, 693), 5, 6),
                "safe_box": ((875, 755, 1067, 947), 3, 3),
                "loot": ((1282, 128, 1602, 960), 5, 13),
            }
            for region in layout.regions:
                region.rect, region.columns, region.rows = regions[region.id]
                region.cell_size = 64
        return HybridRecognizer(
            self.snapshot.catalog, self.snapshot.templates,
            layout=layout,
            ocr=RapidOcrEngine(),
        )

    def upload(self, raw: bytes, profile: str = "auto") -> str:
        if profile not in {"auto", "default", "sample02", "manual"}:
            raise ReviewError("识别布局无效")
        image = decode_upload(raw)
        if profile in {"default", "sample02"} and image.shape[:2] != (1080, 1920):
            raise ReviewError("示例布局仅适用于 1920×1080；其他图片请选择全图自适应")
        with self.lock:
            if self.session and self.session["status"] == "processing":
                raise ReviewError("上一张截图仍在识别，请稍候")
            session_id = uuid4().hex
            self.image = image
            self.session = {
                "id": session_id, "image_hash": hashlib.sha256(image.tobytes()).hexdigest(),
                "status": "processing", "rows": [], "revision": 0, "scope": "all",
                "coverage_confirmed": False, "accept_stale": False, "error": None,
                "profile": profile,
                "width": image.shape[1], "height": image.shape[0],
            }
            self.pool.submit(self._analyze, session_id, image)
            return session_id

    def _analyze(self, session_id, image):
        try:
            if self.session["profile"] == "manual":
                self._finish_analysis(session_id, [])
                return
            if self.session["profile"] == "auto":
                proposals = propose_items(image, self.snapshot.catalog, RapidOcrEngine(),
                                          enhanced=True)
                rows = [draft_row(**proposal) for proposal in proposals]
                for row in rows:
                    if row["definition_id"]:
                        row["cells"] = self.snapshot.item(row["definition_id"])["cells"]
                self._finish_analysis(session_id, rows)
                return
            self.recognizer = self.factory()
            result = self.recognizer.recognize(image)
            rows, keys = [], set()
            handled = set()
            for unresolved in result.state.unresolved:
                meta = unresolved.metadata
                handled.add(meta.get("provisional_instance_id"))
                row = draft_row(list(meta["rect"]), meta.get("source_container_id", "backpack"),
                                unresolved.candidate_definition_ids,
                                int(meta.get("quantity", 1)), meta.get("ocr_text", ""))
                if row["key"] not in keys:
                    rows.append(row)
                    keys.add(row["key"])
            for item in result.state.items:
                if item.instance_id in handled or "rect" not in item.metadata:
                    continue
                row = draft_row(list(item.metadata["rect"]),
                                item.metadata.get("source_container_id", "backpack"),
                                [item.definition_id], item.quantity,
                                item.metadata.get("ocr_text", ""))
                if row["key"] not in keys:
                    rows.append(row)
                    keys.add(row["key"])
            for row in rows:
                if row["scope"] not in SCOPES:
                    row["scope"] = "backpack"
                if row["definition_id"] in self.snapshot.catalog.items:
                    row["cells"] = self.snapshot.item(row["definition_id"])["cells"]
            self._finish_analysis(session_id, rows)
        except Exception:
            with self.lock:
                if self.session and self.session["id"] == session_id:
                    self.session.update(
                        status="ready", error="自动识别未完成。可手动框选、搜索物品并确认估价。"
                    )

    def _finish_analysis(self, session_id, rows):
        with self.lock:
            if self.session["id"] != session_id:
                return
            saved = self.store.load(self.session["image_hash"])
            keys = {row["key"] for row in rows}
            restored = set()
            for row in rows:
                match = row["key"] if row["key"] in saved else next((
                    key for key, old in saved.items()
                    if key not in restored and abs(old["rect"][0] - row["rect"][0]) < 14 and
                    abs(old["rect"][1] - row["rect"][1]) < 14), None)
                if match is not None:
                    # Same-image OCR may shift a few pixels. Preserve explicit user corrections.
                    row.update(saved[match])
                    restored.add(match)
                    row.update(confirmed=False, recalled=True)
                    if row["definition_id"] not in self.snapshot.catalog.items:
                        row["definition_id"] = ""
            for key, row in saved.items():
                if key not in keys and key not in restored and row.get("manual"):
                    rows.append({**row, "confirmed": False, "recalled": True})
            self.session.update(status="ready", rows=rows)

    def _current(self, session_id: str):
        if not self.session or self.session["id"] != session_id:
            raise ReviewError("截图会话已失效，请刷新或重新上传")
        if self.session["status"] != "ready":
            raise ReviewError("正在识别，请稍候")
        return self.session

    def rescan(self, session_id, data):
        with self.lock:
            session = self._current(session_id)
            self._revision(session, data)
            session.update(status="processing", error=None)
            self.pool.submit(self._rescan, session_id, self.image.copy())
            return session_id

    def _rescan(self, session_id, image):
        try:
            proposals = propose_items(image, self.snapshot.catalog, RapidOcrEngine(), enhanced=True)
            with self.lock:
                if not self.session or self.session["id"] != session_id:
                    return
                session = self.session
                protected = self.store.load(session["image_hash"])
                merged = self.merge_proposals(session["rows"], proposals, protected)
                session.update(rows=merged, status="ready")
                self._changed(session)
        except Exception:
            with self.lock:
                if self.session and self.session["id"] == session_id:
                    self.session.update(status="ready", error="补扫未完成，原有物品和纠错已保留。")

    def merge_proposals(self, rows, proposals, protected):
        merged = deepcopy(rows)
        for proposal in proposals:
            rect = proposal["rect"]
            row = next((r for r in merged if abs(r["rect"][0]-rect[0]) < 24 and
                        abs(r["rect"][1]-rect[1]) < 24), None)
            if row and (row["key"] in protected or row["confirmed"] or row["locked"] or
                        row["manual"] or row["excluded"]):
                continue
            incoming = draft_row(**proposal)
            if incoming["definition_id"]:
                incoming["cells"] = self.snapshot.item(incoming["definition_id"])["cells"]
            if row:
                # Keep stable identity so an open editor cannot point at another item.
                incoming["key"] = row["key"]
                row.update(incoming)
            elif len(merged) < 160:
                merged.append(incoming)
        return merged

    def suggest_crop(self, session_id: str, key: str) -> list[dict]:
        with self.lock:
            session = self._current(session_id)
            row = next((r for r in session["rows"] if r["key"] == key), None)
            if row is None:
                raise ReviewError("物品不存在")
            x, y, w, h = row["rect"]
            crop = self.image[y:y+h, x:x+w].copy()
        if not self.crop_lock.acquire(blocking=False):
            raise ReviewError("另一项裁剪正在识别，请稍候")
        try:
            self.crop_ocr = self.crop_ocr or RapidOcrEngine()
            self.crop_matcher = self.crop_matcher or TemplateMatcher(
                self.snapshot.catalog, self.snapshot.templates
            )
            scores = {}
            for turn in (0, 1, 2, 3):
                rotated = np.ascontiguousarray(np.rot90(crop, turn))
                text = self.crop_ocr.recognize(rotated)
                for definition, score in self.snapshot.catalog.match_text(text, minimum_score=.65):
                    scores[definition.id] = max(scores.get(definition.id, 0), score)
                # Layout-independent local features aid rotated icons; still proposals only.
                for match in self.crop_matcher.match(rotated, max(1, round(w/64)),
                                                     max(1, round(h/64))):
                    scores[match.definition_id] = max(
                        scores.get(match.definition_id, 0), match.confidence * .8
                    )
            with self.lock:
                self._current(session_id)
            ids = sorted(scores, key=lambda candidate: -scores[candidate])[:8]
            return [self.snapshot.item(item_id) for item_id in ids]
        finally:
            self.crop_lock.release()

    def _revision(self, session, data):
        if data.get("revision") != session["revision"]:
            raise ReviewError("结果已在其他窗口更新，请刷新后再操作")

    def _changed(self, session):
        session["revision"] += 1
        session["coverage_confirmed"] = False

    def correct(self, session_id: str, key: str, data: dict):
        with self.lock:
            session = self._current(session_id)
            self._revision(session, data)
            row = next((r for r in session["rows"] if r["key"] == key), None)
            if row is None:
                raise ReviewError("找不到该物品")
            revised = deepcopy(row)
            for flag in ("confirmed", "excluded", "locked"):
                if not isinstance(data.get(flag), bool):
                    raise ReviewError("确认、排除和锁定状态无效")
                revised[flag] = data[flag]
            definition_id = data.get("definition_id", "")
            if not isinstance(definition_id, str):
                raise ReviewError("物品 ID 无效")
            if definition_id:
                self.snapshot.item(definition_id)
            elif not revised["excluded"]:
                raise ReviewError("先搜索并选择一个具体物品")
            revised.update(
                definition_id=definition_id,
                quantity=integer(data.get("quantity"), "数量", 1, 9999),
                cells=integer(data.get("cells"), "占格", 1, 200),
                manual_unit_price=optional_price(data.get("manual_unit_price")),
                manual_total=optional_price(data.get("manual_total")),
            )
            if data.get("scope") not in SCOPES:
                raise ReviewError("物品区域无效")
            revised["scope"] = data["scope"]
            if data.get("contribute") not in (True, False, None):
                raise ReviewError("样本保存选项无效")
            png = None
            if data.get("contribute") is True and revised["confirmed"] and not revised["excluded"]:
                png = self._crop(revised["rect"])
            self.store.save(session["image_hash"], revised, png)
            row.update(revised)
            self._changed(session)
            return self.view()

    def add(self, session_id: str, data: dict):
        with self.lock:
            session = self._current(session_id)
            self._revision(session, data)
            if len(session["rows"]) >= 160:
                raise ReviewError("单张截图最多 160 个物品")
            rect = validate_rect(data.get("rect"), (session["width"], session["height"]))
            row = draft_row(rect, "unassigned", [], manual=True)
            if any(r["key"] == row["key"] for r in session["rows"]):
                raise ReviewError("这个位置已有物品，请直接编辑")
            session["rows"].append(row)
            self._changed(session)
            return self.view(), row["key"]

    def settings(self, session_id: str, data: dict):
        with self.lock:
            session = self._current(session_id)
            self._revision(session, data)
            if data.get("scope") not in RANKING_SCOPES:
                raise ReviewError("排名范围无效")
            for name in ("coverage_confirmed", "accept_stale"):
                if not isinstance(data.get(name), bool):
                    raise ReviewError("核对选项无效")
            session.update({key: data[key] for key in
                            ("scope", "coverage_confirmed", "accept_stale")})
            session["revision"] += 1
            return self.view()

    def valued(self, row: dict) -> dict:
        definition = self.snapshot.item(row["definition_id"]) if row["definition_id"] else None
        unit = row["manual_unit_price"]
        if unit is None and definition:
            unit = definition["unit_price"]
        total = row["manual_total"]
        if total is None and unit is not None:
            total = unit * row["quantity"]
        reasons = []
        if not definition:
            reasons.append("物品未确定")
        if not row["confirmed"]:
            reasons.append("名称、数量和占格待核对")
        if row["scope"] == "unassigned":
            reasons.append("所在区域待选择")
        if total is None:
            reasons.append("缺少价格")
        if definition and definition["needs_total"] and row["manual_total"] is None:
            reasons.append("枪械/耐久/剩余次数需填写当前整件或整堆总价")
        limitations = []
        if not definition:
            limitations.append("未识别")
        if total is None:
            limitations.append("缺价")
        if row["scope"] == "unassigned":
            limitations.append("来源待指定")
        incomplete_weapon = (definition and definition["category"] == "weapon" and
                             row["manual_total"] is None)
        if incomplete_weapon:
            limitations.append("仅枪体价")
        return {
            **row, "definition": definition,
            "name": definition["name"] if definition else "未知物品",
            "unit_price": unit, "total": total,
            "per_cell": total / row["cells"] if total is not None else None,
            "price_source": "手工总价" if row["manual_total"] is not None else (
                "手工单价" if row["manual_unit_price"] is not None else "本地价格快照"
            ),
            "reasons": reasons, "eligible": not reasons and not row["excluded"],
            "limitations": limitations,
            "comparable": bool(definition and total is not None and not limitations and
                               not row["excluded"]),
            "acceptance": "human_corrected" if row["confirmed"] else "automatic_assumption",
        }

    def view(self) -> dict | None:
        with self.lock:
            if self.session is None:
                return None
            session = deepcopy(self.session)
            session["rows"] = [self.valued(row) for row in session["rows"]]
            scope = session["scope"]
            selected = [r for r in session["rows"] if not r["excluded"] and (
                scope == "all" or r["scope"] in {scope, "unassigned"} or
                scope == "carried" and r["scope"] != "loot"
            )]
            ranked = sorted([r for r in selected if r["eligible"]],
                            key=lambda r: (r["per_cell"], r["total"], r["key"]))
            stale_block = self.snapshot.metadata()["stale"] and not session["accept_stale"]
            complete = bool(selected) and all(r["eligible"] for r in selected)
            session["ranking"] = {
                "keys": [r["key"] for r in ranked], "total": sum(r["total"] for r in ranked),
                "pending": sum(not r["eligible"] for r in selected),
                "ready": complete and session["coverage_confirmed"] and not stale_block,
                "stale_block": stale_block, "selected": len(selected),
            }
            session["advice"] = build_advice(session, self.snapshot.metadata())
            return session

    def ask_agent(self, session_id, data):
        with self.lock:
            session = self._current(session_id)
            self._revision(session, data)
            snapshot = self.view()
        if not self.agent_lock.acquire(blocking=False):
            raise ReviewError("上一条建议正在生成，请稍候")
        try:
            from .agent_workflow import ReviewAgent
            from .deepseek_provider import deepseek_generate
            config = self.llm_settings.read()
            provider = config["provider"]
            use_llm = data.get("use_llm", False)
            if use_llm and provider == "disabled":
                raise ReviewError("请先在 AI 模型设置中选择 DeepSeek 或本机模型")
            if use_llm and provider == "deepseek" and data.get("allow_cloud") is not True:
                raise ReviewError("需允许发送本次文字物资列表和问题到 DeepSeek；不含截图")
            self.agent = self.agent or ReviewAgent(self.snapshot)
            if provider == "deepseek":
                def generate(question, evidence, model):
                    return deepseek_generate(question, evidence, model,
                                             self.llm_settings.tokens.load())
                self.agent.generator = generate
            else:
                from .agent_workflow import local_llm
                self.agent.generator = local_llm
            try:
                result = self.agent.run(snapshot, data.get("question"),
                                        use_llm, config["model"], provider=provider)
            except ValueError as exc:
                raise ReviewError(str(exc)) from exc
            with self.lock:
                self._revision(self._current(session_id), data)
            return result
        finally:
            self.agent_lock.release()

    def _crop(self, rect):
        x, y, w, h = rect
        return cv2.imencode(".png", self.image[y:y+h, x:x+w])[1].tobytes()

    def image_bytes(self, session_id: str, key: str | None = None) -> bytes:
        with self.lock:
            if not self.session or self.session["id"] != session_id:
                raise ReviewError("截图已失效")
            if key is None:
                return cv2.imencode(".png", self.image)[1].tobytes()
            row = next((r for r in self.session["rows"] if r["key"] == key), None)
            if row is None:
                raise ReviewError("物品不存在")
            return self._crop(row["rect"])

    def close(self):
        self.pool.shutdown(wait=True)
