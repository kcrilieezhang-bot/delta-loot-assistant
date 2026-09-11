from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import requests


@dataclass(slots=True, frozen=True)
class IconRecord:
    definition_id: str
    image_url: str


@dataclass(slots=True, frozen=True)
class IconCacheResult:
    item_count: int
    unique_url_count: int
    downloaded_count: int
    failed_count: int


class IconTemplateCache:
    """Builds a local, untracked reference icon cache from catalog image URLs."""

    MANIFEST_NAME = "icon-index.json"

    def __init__(
        self,
        root: str | Path,
        *,
        fetcher: Callable[[str], bytes] | None = None,
        workers: int = 4,
    ):
        self.root = Path(root)
        self.icon_root = self.root / "provider-icons"
        self.manifest_path = self.root / self.MANIFEST_NAME
        self.fetcher = fetcher or self._download
        self.workers = max(1, min(8, workers))

    def build(self, records: Iterable[IconRecord]) -> IconCacheResult:
        normalized = [
            record
            for record in records
            if record.definition_id.strip() and record.image_url.strip()
        ]
        urls = sorted({record.image_url for record in normalized})
        self.icon_root.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        failed = 0

        missing_urls = [url for url in urls if not self._icon_path(url).exists()]
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self._fetch_and_store, url): url for url in missing_urls}
            for future in as_completed(futures):
                try:
                    if future.result():
                        downloaded += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

        items: dict[str, str] = {}
        for record in normalized:
            path = self._icon_path(record.image_url)
            if path.exists():
                items[record.definition_id] = path.relative_to(self.root).as_posix()
        payload = {
            "schema_version": 1,
            "items": items,
        }
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.manifest_path)
        return IconCacheResult(
            item_count=len(items),
            unique_url_count=len(urls),
            downloaded_count=downloaded,
            failed_count=failed,
        )

    def _fetch_and_store(self, url: str) -> bool:
        payload = self.fetcher(url)
        encoded = np.frombuffer(payload, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
        if image is None or image.size == 0:
            return False
        destination = self._icon_path(url)
        temporary = destination.with_suffix(".tmp.png")
        if not cv2.imwrite(str(temporary), image):
            return False
        temporary.replace(destination)
        return True

    def _icon_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.icon_root / f"{digest}.png"

    @staticmethod
    def _download(url: str) -> bytes:
        response = requests.get(
            url,
            headers={"User-Agent": "DeltaLootAssistant/0.1"},
            timeout=(5, 30),
        )
        response.raise_for_status()
        return response.content
