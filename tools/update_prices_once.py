"""Explicit maintenance sync; one request per endpoint, no retries or icon downloads."""
import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta

from delta_loot_assistant.orzice import (
    OrziceCatalogRepository,
    OrziceClient,
    OrziceDailySyncService,
    SyncDeferredError,
)
from delta_loot_assistant.paths import user_data_dir
from delta_loot_assistant.pricing import LocalPricePackProvider
from delta_loot_assistant.secrets import DpapiTokenStore
from delta_loot_assistant.web_review import Snapshot


def audit(root):
    snapshot = Snapshot(root / "assistant.sqlite3", root / "templates")
    groups = {}
    for prefix in ("扑克牌-", "阿萨拉牌-"):
        items = [snapshot.item(d.id) for d in snapshot.catalog.items.values()
                 if d.name.startswith(prefix)]
        groups[prefix] = {"items": len(items),
                          "priced": sum(i["unit_price"] is not None for i in items),
                          "examples": [{"name": i["name"], "price": i["unit_price"]}
                                       for i in items[:3]]}
    return {"metadata": snapshot.metadata(), "cards": groups}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorized-within-day", action="store_true")
    args = parser.parse_args()
    root = user_data_dir()
    before = audit(root)
    if not args.execute:
        print(json.dumps(before, ensure_ascii=False))
        raise SystemExit(0)
    repository = OrziceCatalogRepository(root / "assistant.sqlite3")
    now = datetime.now(UTC)
    last = repository.last_synced_at()
    if last and now < last + timedelta(days=1) and not args.authorized_within_day:
        print("24-hour protection: no API calls sent.")
        raise SystemExit(2)
    token = DpapiTokenStore(root / "orzice-token.dpapi").load()
    if not token:
        print("No saved pricing credential. No API calls sent.")
        raise SystemExit(2)
    backup_dir = root / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"before-price-sync-{stamp}.sqlite3"
    with sqlite3.connect(root / "assistant.sqlite3") as source, sqlite3.connect(backup) as target:
        source.backup(target)
    report_path = root / "reports" / f"price-sync-{stamp}.json"
    report_path.parent.mkdir(exist_ok=True)
    report = {"started_at": now.isoformat(), "status": "started", "before": before,
              "requested_within_day_exception": args.authorized_within_day, "requests": []}

    def checkpoint():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    class CountedClient(OrziceClient):
        def _get(self, endpoint):
            if endpoint in report["requests"]:
                raise RuntimeError("Duplicate paid endpoint blocked")
            report["requests"].append(endpoint)
            checkpoint()
            return super()._get(endpoint)

    checkpoint()
    service = OrziceDailySyncService(
        CountedClient(token), repository, LocalPricePackProvider(root / "assistant.sqlite3"),
        minimum_interval=timedelta(0) if args.authorized_within_day else timedelta(days=1))
    try:
        result = service.sync()
        report.update(status="success", after=audit(root),
                      finished_at=datetime.now(UTC).isoformat())
    except SyncDeferredError:
        report.update(status="deferred")
    except Exception as exc:
        # Never emit raw provider messages/URLs or credential-bearing exception chains.
        report.update(status="failed", error_type=type(exc).__name__,
                      message="更新失败，未自动重试；旧库备份保留。")
    checkpoint()
    print(json.dumps(report, ensure_ascii=False))
