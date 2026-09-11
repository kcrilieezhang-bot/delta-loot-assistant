from __future__ import annotations

import io
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest
from PIL import Image

from delta_loot_assistant.adaptive_recognition import propose_items
from delta_loot_assistant.models import ItemDefinition, PriceEntry, PricePack
from delta_loot_assistant.recognition import OcrLine
from delta_loot_assistant.web_review import (
    CorrectionStore,
    ReviewError,
    ReviewService,
    Snapshot,
    decode_upload,
    draft_row,
    validate_rect,
)
from delta_loot_assistant.web_server import ReviewServer


@pytest.fixture
def review(tmp_path, catalog):
    snapshot = object.__new__(Snapshot)
    snapshot.catalog = catalog
    snapshot.templates = tmp_path
    snapshot.icons = {}
    snapshot.pack = PricePack(1, "cn", "test", datetime.now(UTC), "test",
                              {"ammo": PriceEntry(100), "weapon": PriceEntry(1000),
                               "small_high": PriceEntry(600), "small_low": PriceEntry(200)})
    service = ReviewService(snapshot, CorrectionStore(tmp_path / "corrections.sqlite3"))
    service.image = np.zeros((480, 640, 3), dtype=np.uint8)
    service.session = {"id": "test-session", "image_hash": "test-hash", "status": "ready",
                       "rows": [], "revision": 0, "scope": "backpack", "profile": "auto",
                       "coverage_confirmed": False, "accept_stale": False, "error": None,
                       "width": 640, "height": 480}
    yield service
    service.close()


def add_row(review, definition="ammo", rect=None):
    row = draft_row(rect or [50, 50, 60, 60], "backpack", [definition] if definition else [])
    review.session["rows"].append(row)
    return row


def correction(review, row, **kwargs):
    return {"revision": review.session["revision"], "definition_id": row["definition_id"],
            "quantity": 60, "cells": 1, "manual_unit_price": None, "manual_total": None,
            "scope": "backpack", "confirmed": True, "excluded": False, "locked": False,
            **kwargs}


def save(review, row, **kwargs):
    return review.correct("test-session", row["key"], correction(review, row, **kwargs))


def test_unconfirmed_never_ranked(review):
    add_row(review)
    assert review.view()["ranking"]["keys"] == []


def test_all_scope_includes_body_ground_safe_box_unknown(review):
    for index, scope in enumerate(["backpack", "carried", "safe_box", "loot", "unassigned"]):
        row = add_row(review, rect=[50 + index * 70, 50, 60, 60])
        row["scope"] = scope
    result = review.settings("test-session", {"revision": 0, "scope": "all",
                             "coverage_confirmed": False, "accept_stale": False})
    assert result["ranking"]["selected"] == 5
    assert result["ranking"]["ready"] is False


def test_all_is_ranking_scope_not_item_location(review):
    row = add_row(review)
    with pytest.raises(ReviewError, match="区域"):
        save(review, row, scope="all")


def test_advice_excludes_locked_and_safe_box_but_accepts_recognition(review):
    low = add_row(review, "small_low")
    save(review, low, quantity=1)
    high = add_row(review, "small_high", [150, 50, 60, 60])
    save(review, high, quantity=1, scope="loot")
    advice = review.view()["advice"]
    assert advice["swaps"][0]["gain"] == 400
    assert advice["swaps"][0]["status"] == "conditional"
    save(review, low, quantity=1, confirmed=False)
    assert review.view()["advice"]["swaps"][0]["gain"] == 400
    for override in [{"locked": True}, {"scope": "safe_box"}, {"scope": "carried"}]:
        save(review, low, quantity=1, **override)
        assert review.view()["advice"]["swaps"] == []


def test_advice_unpriced_and_incomplete_weapons_are_not_drop_candidates(review):
    gun = add_row(review, "weapon")
    save(review, gun, quantity=1)
    missing = add_row(review, "large", [150, 50, 60, 60])
    save(review, missing, quantity=1)
    advice = review.view()["advice"]
    assert gun["key"] in advice["keep_keys"]
    assert not advice["low_keys"]


def test_advice_stale_prices_warn_but_allow_reference_comparison(review):
    low = add_row(review, "small_low")
    save(review, low, quantity=1)
    high = add_row(review, "small_high", [150, 50, 60, 60])
    save(review, high, quantity=1, scope="loot")
    review.snapshot.pack.generated_at = datetime.now(UTC) - timedelta(days=2)
    result = review.view()["advice"]
    assert len(result["swaps"]) == 1
    assert any("24" in warning for warning in result["warnings"])
    review.session["accept_stale"] = True
    assert len(review.view()["advice"]["swaps"]) == 1


def test_rescan_merge_preserves_saved_edits_and_repeated_items(review):
    row = add_row(review, "small_low")
    proposals = [{"rect": [52, 52, 80, 60], "scope": "backpack",
                  "candidates": ["small_high"], "quantity": 1, "ocr": "test"},
                 {"rect": [120, 52, 60, 60], "scope": "backpack",
                  "candidates": ["small_high"], "quantity": 1, "ocr": "test"}]
    merged = review.merge_proposals([row], proposals, {row["key"]: row})
    assert merged[0]["definition_id"] == "small_low"
    assert len(merged) == 2
    refreshed = review.merge_proposals([row], proposals, {})
    assert refreshed[0]["definition_id"] == "small_high"
    assert refreshed[0]["key"] == row["key"]
    assert row["definition_id"] == "small_low"  # atomic input preservation


def test_local_agent_uses_langchain_and_never_calls_model_by_default(review):
    pytest.importorskip("langchain_core")
    from delta_loot_assistant.agent_workflow import ReviewAgent

    def forbidden(*_args):
        raise AssertionError("No model call authorized")

    agent = ReviewAgent(review.snapshot, generator=forbidden)
    result = agent.run(review.view(), "整枪配件为什么不能按枪体价格丢弃？")
    assert result["orchestrator"] == "langchain_lcel"
    assert result["llm_used"] is False
    assert "rule:weapon" in [doc["id"] for doc in result["evidence"]]
    assert [step["tool"] for step in result["trace"]] == [
        "retrieve_knowledge", "compare_inventory"]


def test_local_agent_model_failure_falls_back_without_retry(review):
    from delta_loot_assistant.agent_workflow import ReviewAgent

    calls = []

    def failure(*args):
        calls.append(args)
        raise TimeoutError()

    result = ReviewAgent(review.snapshot, generator=failure).run(
        review.view(), "背包哪些物品值得拿？", use_llm=True, model="local-test")
    assert len(calls) == 1
    assert result["warning"] and not result["llm_used"]
    assert result["advice"]["status"] == "reference_only"


def test_agent_rejects_bad_question_and_stale_revision(review):
    with pytest.raises(ReviewError):
        review.ask_agent("test-session", {"revision": 99, "question": "背包"})
    with pytest.raises(ReviewError):
        review.ask_agent("test-session", {"revision": 0, "question": "x" * 501})


def test_agent_http_route_is_read_only_and_csrf_guarded(http_server, review):
    pytest.importorskip("langchain_core")
    payload = json.dumps({"session_id": "test-session", "revision": 0,
                          "question": "整枪配件怎么估价", "use_llm": False}).encode()
    url = f"http://127.0.0.1:{http_server.server_port}/api/assistant"
    with pytest.raises(HTTPError) as error:
        urlopen(Request(url, data=payload, headers={"Content-Type": "application/json"}))
    assert error.value.code == 403
    request = Request(url, data=payload, headers={"Content-Type": "application/json",
                                                 "X-Review-Token": http_server.csrf})
    with urlopen(request) as response:
        result = json.load(response)
    assert result["orchestrator"] == "langchain_lcel"
    assert review.session["revision"] == 0
    assert not result["llm_used"]


def test_second_server_cannot_share_the_active_port(http_server, review):
    with pytest.raises(OSError):
        ReviewServer(("127.0.0.1", http_server.server_port), review)


def test_ammo_corrected_once_and_no_double_count(review):
    row = add_row(review)
    save(review, row)
    save(review, row, quantity=30)
    assert review.view()["ranking"]["total"] == 3000
    assert len(review.session["rows"]) == 1


def test_missing_price_not_zero(review):
    row = add_row(review, "large")
    result = save(review, row)
    assert result["rows"][0]["total"] is None
    assert result["ranking"]["pending"] == 1


def test_manual_zero_is_explicit_and_valid(review):
    row = add_row(review, "large")
    assert save(review, row, manual_total=0)["rows"][0]["eligible"]


def test_gun_body_not_a_complete_weapon_price(review):
    row = add_row(review, "weapon")
    assert not save(review, row)["rows"][0]["eligible"]
    result = save(review, row, manual_total=12000)
    assert result["rows"][0]["total"] == 12000
    assert result["rows"][0]["eligible"]


@pytest.mark.parametrize("field,value", [("quantity", 0), ("quantity", True), ("quantity", 1.5),
                                        ("cells", -1), ("manual_total", -10),
                                        ("manual_unit_price", "100"), ("confirmed", "true")])
def test_invalid_edits_are_atomic(review, field, value):
    row = add_row(review)
    before = dict(row)
    with pytest.raises(ReviewError):
        save(review, row, **{field: value})
    assert row == before
    assert review.session["revision"] == 0


def test_stale_revision_prevents_lost_edit(review):
    row = add_row(review)
    payload = correction(review, row)
    save(review, row)
    with pytest.raises(ReviewError):
        review.correct("test-session", row["key"], payload)


def test_stale_prices_need_ack_and_coverage(review):
    review.snapshot.pack.generated_at -= timedelta(days=2)
    row = add_row(review)
    save(review, row)
    settings = {"revision": 1, "scope": "backpack", "coverage_confirmed": True,
                "accept_stale": False}
    assert not review.settings("test-session", settings)["ranking"]["ready"]
    settings.update(revision=2, accept_stale=True)
    assert review.settings("test-session", settings)["ranking"]["ready"]
    assert not save(review, row, quantity=20)["ranking"]["ready"]


def test_sorting_by_per_cell_and_scope(review):
    first = add_row(review, "small_high")
    second = add_row(review, "small_low", [150, 50, 60, 60])
    save(review, first, quantity=1, cells=4)
    save(review, second, quantity=1)
    assert review.view()["ranking"]["keys"] == [first["key"], second["key"]]
    save(review, first, quantity=1, scope="loot")
    assert review.view()["ranking"]["keys"] == [second["key"]]


def test_exclusions_and_unassigned_rows(review):
    row = add_row(review)
    assert save(review, row, excluded=True)["ranking"]["selected"] == 0
    result = save(review, row, scope="unassigned")
    assert result["ranking"]["pending"] == 1
    assert not result["ranking"]["ready"]


def test_correction_restore_never_auto_confirms(review):
    row = add_row(review)
    save(review, row, quantity=7)
    row["quantity"] = 1
    review._finish_analysis("test-session", [row])
    assert row["quantity"] == 7
    assert row["recalled"] and not row["confirmed"]


def test_crop_storage_opt_in_only(review):
    row = add_row(review)
    save(review, row)
    with sqlite3.connect(review.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM confirmed_crops").fetchone()[0] == 0
    save(review, row, contribute=True)
    with sqlite3.connect(review.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM confirmed_crops").fetchone()[0] == 1


def test_manual_rect_non_1080p_and_bounds(review):
    result, key = review.add("test-session", {"revision": 0, "rect": [10, 20, 80, 60]})
    assert result["rows"][0]["key"] == key
    with pytest.raises(ReviewError):
        validate_rect([630, 400, 50, 50], (640, 480))


def test_upload_sizes_and_formats():
    image = Image.new("RGB", (1280, 720))
    output = io.BytesIO()
    image.save(output, "PNG")
    assert decode_upload(output.getvalue()).shape == (720, 1280, 3)
    with pytest.raises(ReviewError):
        decode_upload(b"not an image")


def test_adaptive_recognition_tracks_moved_labels(catalog):
    class Ocr:
        def __init__(self, x):
            self.x = x

        def recognize_lines(self, _image):
            return [OcrLine("高价值小物品", .99, (self.x, 100, 80, 20))]

    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    first = propose_items(image, catalog, Ocr(100))
    second = propose_items(image, catalog, Ocr(700))
    assert first[0]["candidates"][0] == second[0]["candidates"][0] == "small_high"
    assert first[0]["rect"][0] == 100
    assert second[0]["rect"][0] == 700
    assert first[0]["scope"] == "unassigned"


def test_weapon_short_name_not_mistaken_for_attachment(catalog):
    catalog.items["asval"] = ItemDefinition("asval", "AS Val突击步枪", [], "weapon", 5, 2)
    catalog.items["stock"] = ItemDefinition("stock", "AS Val枪托尾盖", [], "attachment", 1, 1)

    class Ocr:
        def recognize_lines(self, _image):
            return [OcrLine("AS Val", .99, (100, 200, 70, 20)),
                    OcrLine("9x39mm", .99, (100, 222, 70, 20))]

    rows = propose_items(np.zeros((720, 1280, 3), dtype=np.uint8), catalog, Ocr())
    assert len(rows) == 1
    assert rows[0]["candidates"][0] == "asval"


def test_telemetry_is_not_a_cpu_collectible(catalog):
    catalog.items["cpu"] = ItemDefinition("cpu", "CPU", [], "collectible", 1, 1)

    class Ocr:
        def recognize_lines(self, _image):
            return [OcrLine("CPU", .99, (200, 40, 30, 10)),
                    OcrLine("GPU 65°C", .99, (280, 40, 80, 10))]

    assert propose_items(np.zeros((720, 1280, 3), dtype=np.uint8), catalog, Ocr()) == []


def test_caliber_plus_subtype_can_identify_ammunition(catalog):
    catalog.items["bp"] = ItemDefinition("bp", "9x39mm BP", [], "ammo", 1, 1)

    class Ocr:
        def recognize_lines(self, _image):
            return [OcrLine("9x39mm", .99, (100, 200, 70, 15)),
                    OcrLine("BP", .99, (100, 216, 20, 15))]

    rows = propose_items(np.zeros((720, 1280, 3), dtype=np.uint8), catalog, Ocr())
    assert rows[0]["candidates"][0] == "bp"


@pytest.fixture
def http_server(review):
    server = ReviewServer(("127.0.0.1", 0), review)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def test_cloud_consent_and_revision_before_billable_call(review, monkeypatch):
    calls = []

    def generate(*_args):
        calls.append(1)
        return {"summary": "测试", "evidence_ids": ["rule:price"]}

    monkeypatch.setattr("delta_loot_assistant.deepseek_provider.deepseek_generate", generate)
    monkeypatch.setattr(review.llm_settings.tokens, "load", lambda: "test-only")
    review.llm_settings.save({"provider": "deepseek", "model": "deepseek-v4-flash"})
    data = {"revision": 0, "question": "价格来源", "use_llm": True}
    with pytest.raises(ReviewError, match="需允许"):
        review.ask_agent("test-session", data)
    with pytest.raises(ReviewError):
        review.ask_agent("test-session", {**data, "revision": 99, "allow_cloud": True})
    assert not calls
    assert not review.ask_agent("test-session", {**data, "use_llm": False})["llm_used"]
    assert not calls
    result = review.ask_agent("test-session", {**data, "allow_cloud": True})
    assert result["mode"] == "deepseek_rag"
    assert calls == [1]


def test_model_settings_http_csrf_and_safe_public_response(http_server):
    url = f"http://127.0.0.1:{http_server.server_port}/api/llm-settings"
    body = json.dumps({"provider": "deepseek", "model": "deepseek-v4-flash"}).encode()
    with pytest.raises(HTTPError) as error:
        urlopen(Request(url, data=body))
    assert error.value.code == 403
    with urlopen(Request(url, data=body, headers={"X-Review-Token": http_server.csrf,
                                                "Content-Type": "application/json"})) as reply:
        assert json.load(reply)["provider"] == "deepseek"
    with urlopen(url) as reply:
        assert set(json.load(reply)) == {"provider", "model", "key_configured"}


def test_automatic_comparison_preserves_human_provenance(review):
    row = add_row(review, "small_low")
    valued = review.view()["rows"][0]
    assert valued["comparable"]
    assert not row["confirmed"]
    assert valued["acceptance"] == "automatic_assumption"


def test_http_local_assets_and_security(http_server):
    url = f"http://127.0.0.1:{http_server.server_port}"
    with urlopen(url) as response:
        assert response.status == 200
        assert response.headers["X-Frame-Options"] == "DENY"
    with urlopen(url + "/api/status") as response:
        assert json.load(response)["app"] == "delta-loot-local-web"
    for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}):
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url + "/api/status", headers=headers))
        assert error.value.code == 403
    with pytest.raises(HTTPError) as error:
        urlopen(Request(url + "/api/correct", data=b"{}", method="POST"))
    assert error.value.code == 403
    with pytest.raises(HTTPError) as error:
        urlopen(url + "/../../orzice-token.dpapi")
    assert error.value.code == 404


def test_http_correct_roundtrip(http_server, review):
    row = add_row(review)
    data = {**correction(review, row), "session_id": "test-session", "key": row["key"]}
    request = Request(f"http://127.0.0.1:{http_server.server_port}/api/correct",
                      data=json.dumps(data).encode(), method="POST",
                      headers={"Content-Type": "application/json",
                               "X-Review-Token": http_server.csrf})
    with urlopen(request) as response:
        result = json.load(response)
    assert result["session"]["ranking"]["total"] == 6000


def test_missing_snapshot_not_created(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(ReviewError):
        Snapshot(missing, tmp_path)
    assert not missing.exists()


def test_crop_rescan_checks_four_rotations_without_confirming(review, monkeypatch):
    from delta_loot_assistant import web_review

    seen = []

    class Ocr:
        def recognize(self, crop):
            seen.append(crop.shape[:2])
            return "弹药"

    class Matcher:
        def match(self, *_args):
            return []

    row = add_row(review, rect=[30, 30, 90, 40])
    review.crop_ocr, review.crop_matcher = Ocr(), Matcher()
    monkeypatch.setattr(web_review, "RapidOcrEngine", lambda: Ocr())
    before = dict(row)
    suggestions = review.suggest_crop("test-session", row["key"])
    assert suggestions[0]["id"] == "ammo"
    assert seen == [(40, 90), (90, 40), (40, 90), (90, 40)]
    assert row == before
    assert review.store.load("test-hash") == {}
