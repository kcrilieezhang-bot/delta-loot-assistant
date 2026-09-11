import io
import json
import random
from urllib.error import HTTPError

import pytest

from delta_loot_assistant.advisor import choose_swaps
from delta_loot_assistant.catalog import Catalog
from delta_loot_assistant.deepseek_provider import ModelRequestError, deepseek_generate
from delta_loot_assistant.llm_settings import DeepSeekTokenStore, LlmSettings
from delta_loot_assistant.models import ItemDefinition
from delta_loot_assistant.secrets import DpapiTokenStore


class FakeTokens:
    def __init__(self, path):
        self.path, self.saved = path, []

    def save(self, key):
        self.saved.append(key)
        self.path.write_bytes(b"fake encrypted payload")


def test_model_settings_do_not_store_or_return_plain_key(tmp_path):
    tokens = FakeTokens(tmp_path / "test.dpapi")
    settings = LlmSettings(tmp_path, tokens)
    assert settings.read()["provider"] == "disabled"
    key = "sk-" + "testing" * 4
    result = settings.save({"provider": "deepseek", "model": "deepseek-v4-flash",
                            "api_key": key})
    assert result["key_configured"]
    assert key not in settings.path.read_text()
    assert "api_key" not in result
    settings.save({"provider": "deepseek", "model": "deepseek-v4-pro", "api_key": ""})
    assert tokens.saved == [key]
    with pytest.raises(ValueError):
        settings.save({"provider": "deepseek", "model": "https://evil.example"})
    assert settings.read()["model"] == "deepseek-v4-pro"
    assert DeepSeekTokenStore.ENVIRONMENT_VARIABLE != DpapiTokenStore.ENVIRONMENT_VARIABLE


EVIDENCE = [{"id": "rule:test", "title": "测试规则", "text": "未知价格不能当零元。",
             "source": "测试", "image": "must-not-leave-device"}]


def test_generic_cards_search_covers_both_catalog_groups():
    items = [ItemDefinition("p", "扑克牌-Q", [], "valuable", 1, 1),
             ItemDefinition("a", "阿萨拉牌-黑桃5", [], "valuable", 1, 1)]
    catalog = Catalog(1, "test", {item.id: item for item in items}, {})
    for query in ("扑克", "扑克牌", "纸牌", "卡牌"):
        assert {item.id for item in catalog.search(query)} == {"p", "a"}


def response(content, finish="stop", usage=None):
    return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish,
                       "message": {"content": json.dumps(content)}}],
                       "usage": usage or {"total_tokens": 12}}).encode())


def test_deepseek_single_bounded_json_call(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert request.full_url == "https://api.deepseek.com/chat/completions"
            assert timeout == 45
            data = json.loads(request.data)
            assert data["max_tokens"] == 900
            assert data["thinking"]["type"] == "disabled"
            assert data["response_format"]["type"] == "json_object"
            assert b"must-not-leave-device" not in request.data
            return response({"summary": "缺价不按零元处理。", "evidence_ids": ["rule:test"]})

    monkeypatch.setattr("delta_loot_assistant.deepseek_provider.build_opener",
                        lambda *_args: Opener())
    result = deepseek_generate("怎么处理？", EVIDENCE, "deepseek-v4-flash", "test-only")
    assert result["usage"]["total_tokens"] == 12
    assert len(calls) == 1


@pytest.mark.parametrize("bad", [
    {"summary": "猜测", "evidence_ids": ["invented"]},
    {"summary": "", "evidence_ids": ["rule:test"]},
    {"summary": "无引用", "evidence_ids": []},
])
def test_model_rejects_unreferenced_output(monkeypatch, bad):
    class Opener:
        def open(self, *_args, **_kwargs):
            return response(bad)
    monkeypatch.setattr("delta_loot_assistant.deepseek_provider.build_opener",
                        lambda *_args: Opener())
    with pytest.raises(ModelRequestError):
        deepseek_generate("测试", EVIDENCE, "deepseek-v4-flash", "test-only")


def test_api_error_redacted_and_never_retried(monkeypatch):
    calls = []

    class Opener:
        def open(self, *_args, **_kwargs):
            calls.append(1)
            raise HTTPError("https://secret.example", 401, "secret-value", {}, None)
    monkeypatch.setattr("delta_loot_assistant.deepseek_provider.build_opener",
                        lambda *_args: Opener())
    with pytest.raises(ModelRequestError) as error:
        deepseek_generate("测试", EVIDENCE, "deepseek-v4-flash", "test-only")
    assert "secret" not in str(error.value)
    assert "未自动重试" in str(error.value)
    assert calls == [1]


def test_matching_matches_small_brute_force_and_never_reuses_items():
    rng = random.Random(19)
    for _ in range(30):
        ground = [{"key": f"g{i}", "total": rng.randint(1, 100),
                   "cells": rng.randint(1, 4), "comparable": True} for i in range(4)]
        low = [{"key": f"b{i}", "total": rng.randint(1, 100),
                "cells": rng.randint(1, 4)} for i in range(3)]

        def brute(index, used, ground=ground, low=low):
            if index == len(ground):
                return 0
            incoming = ground[index]
            best = brute(index + 1, used)
            for j, outgoing in enumerate(low):
                gain = incoming["total"] - outgoing["total"]
                if j not in used and incoming["cells"] <= outgoing["cells"] and gain > 0:
                    best = max(best, gain + brute(index + 1, used | {j}))
            return best

        swaps, status = choose_swaps(ground, low)
        assert status in {"optimal_one_for_one_model", "no_positive_pairs"}
        assert sum(p["gain"] for p in swaps) == brute(0, set())
        assert len({p["take_key"] for p in swaps}) == len(swaps)
        assert len({p["replace_key"] for p in swaps}) == len(swaps)
