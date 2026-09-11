from __future__ import annotations

import json

import cv2
import numpy as np

from delta_loot_assistant.icon_cache import IconRecord, IconTemplateCache


def test_icon_cache_downloads_each_url_once_and_builds_manifest(tmp_path) -> None:
    image = np.full((16, 16, 3), 127, dtype=np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    calls: list[str] = []

    def fetcher(url: str) -> bytes:
        calls.append(url)
        return encoded.tobytes()

    cache = IconTemplateCache(tmp_path, fetcher=fetcher, workers=1)
    result = cache.build(
        [
            IconRecord("one", "https://example.invalid/shared.png"),
            IconRecord("two", "https://example.invalid/shared.png"),
        ]
    )

    assert result.item_count == 2
    assert result.unique_url_count == 1
    assert result.downloaded_count == 1
    assert result.failed_count == 0
    assert calls == ["https://example.invalid/shared.png"]
    manifest = json.loads((tmp_path / "icon-index.json").read_text(encoding="utf-8"))
    assert set(manifest["items"]) == {"one", "two"}
