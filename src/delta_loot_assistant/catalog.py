from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .models import ContainerDefinition, ItemDefinition


@dataclass(slots=True)
class Catalog:
    schema_version: int
    season: str
    items: dict[str, ItemDefinition]
    containers: dict[str, ContainerDefinition]

    @classmethod
    def load(cls, path: str | Path) -> Catalog:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        items = [ItemDefinition.from_dict(item) for item in raw.get("items", [])]
        containers = [
            ContainerDefinition.from_dict(container)
            for container in raw.get("containers", [])
        ]
        return cls(
            schema_version=int(raw.get("schema_version", 1)),
            season=str(raw.get("season", "unknown")),
            items={item.id: item for item in items},
            containers={container.id: container for container in containers},
        )

    def find_item(self, text: str) -> ItemDefinition | None:
        normalized = text.strip().lower()
        for item in self.items.values():
            names = [item.name, *item.aliases]
            if any(normalized == name.strip().lower() for name in names):
                return item
        return None

    def search(self, text: str, limit: int = 10) -> list[ItemDefinition]:
        normalized = text.strip().lower()
        if not normalized:
            return list(self.items.values())[:limit]
        card_query = normalized in {"扑克", "扑克牌", "纸牌", "卡牌", "阿萨拉牌"}
        ranked: list[tuple[int, ItemDefinition]] = []
        for item in self.items.values():
            names = [item.name, *item.aliases]
            score = 0
            if card_query and item.name.startswith(("扑克牌-", "阿萨拉牌-")):
                score = 70
            for name in names:
                lowered = name.lower()
                if lowered == normalized:
                    score = max(score, 100)
                elif normalized in lowered:
                    score = max(score, 80 - abs(len(lowered) - len(normalized)))
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [item for _, item in ranked[:limit]]

    def match_text(
        self,
        text: str,
        *,
        limit: int = 5,
        minimum_score: float = 0.52,
    ) -> list[tuple[ItemDefinition, float]]:
        """Fuzzy-match noisy local OCR text against names and aliases."""

        query = _normalize_ocr_text(text)
        if len(query) < 2 or query.isdecimal():
            return []
        ranked: list[tuple[float, ItemDefinition]] = []
        alias_counts = Counter(
            _normalize_ocr_text(alias)
            for item in self.items.values() for alias in item.aliases
        )
        for item in self.items.values():
            best = 0.0
            unique_aliases = [
                alias for alias in item.aliases
                if alias_counts[_normalize_ocr_text(alias)] == 1
            ]
            for raw_name in (item.name, *unique_aliases):
                name = _normalize_ocr_text(raw_name)
                if not name:
                    continue
                if query == name:
                    score = 1.0
                elif query in name:
                    score = 0.72 + 0.28 * len(query) / len(name)
                elif name in query:
                    score = 0.74 + 0.26 * len(name) / len(query)
                else:
                    score = SequenceMatcher(None, query, name).ratio()
                best = max(best, score)
            if best >= minimum_score:
                ranked.append((best, item))
        ranked.sort(key=lambda pair: (-pair[0], len(pair[1].name), pair[1].name))
        return [(item, score) for score, item in ranked[:limit]]


def _normalize_ocr_text(text: str) -> str:
    normalized = text.casefold().replace("×", "x")
    normalized = re.sub(r"(?<=m)b(?=\d)", "8", normalized)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)
