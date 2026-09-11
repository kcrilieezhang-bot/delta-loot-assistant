"""Local, read-only comparison; never saves screenshots or accesses the price API."""
import argparse
import json
from time import perf_counter

from delta_loot_assistant.adaptive_recognition import propose_items
from delta_loot_assistant.paths import user_data_dir
from delta_loot_assistant.recognition import RapidOcrEngine
from delta_loot_assistant.web_review import Snapshot, decode_upload

if __name__ == "__main__":
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--enhanced", action="store_true")
    args = parser.parse_args()
    root = user_data_dir()
    snapshot = Snapshot(root / "assistant.sqlite3", root / "templates")
    frame = decode_upload(args.image.read_bytes())
    started = perf_counter()
    rows = propose_items(frame, snapshot.catalog, RapidOcrEngine(), enhanced=args.enhanced)
    print(json.dumps({"seconds": round(perf_counter() - started, 2), "count": len(rows),
                      "items": [{"name": snapshot.item(r["candidates"][0])["name"]
                                 if r["candidates"] else "未知", **r} for r in rows]},
                     ensure_ascii=False))
