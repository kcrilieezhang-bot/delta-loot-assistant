"""Developer maintenance: retain current screenshot/session in RAM during a restart.

Capture first, then wait for the operator to stop the old server. Never kills a
process, writes a screenshot, fetches prices or listens outside loopback.
"""
import json
import time
from urllib.request import ProxyHandler, build_opener

from delta_loot_assistant.paths import user_data_dir
from delta_loot_assistant.web_review import CorrectionStore, ReviewService, Snapshot, decode_upload
from delta_loot_assistant.web_server import ReviewServer

if __name__ == "__main__":
    opener = build_opener(ProxyHandler({}))
    with opener.open("http://127.0.0.1:18765/api/session", timeout=5) as response:
        session = json.load(response)["session"]
    if not session or session["status"] != "ready":
        raise SystemExit("Wait until the current scan is ready before restarting")
    with opener.open("http://127.0.0.1:18765/api/image?session=" + session["id"],
                     timeout=5) as response:
        image = decode_upload(response.read())
    root = user_data_dir()
    service = ReviewService(Snapshot(root / "assistant.sqlite3", root / "templates"),
                            CorrectionStore(root / "web-review" / "corrections.sqlite3"))
    service.image, service.session = image, session
    print(f"Captured {len(session['rows'])} rows, revision {session['revision']}, in RAM.",
          flush=True)
    server = None
    for _ in range(90):
        try:
            server = ReviewServer(("127.0.0.1", 18765), service)
            break
        except OSError:
            time.sleep(1)
    if server is None:
        raise SystemExit("Old server still running; no session changes were made")
    print("http://127.0.0.1:18765/ (session preserved)", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service.close()
