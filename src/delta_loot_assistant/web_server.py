"""Loopback-only web UI. No paid API routes, Qt windows, or game hotkeys."""
from __future__ import annotations

import argparse
import json
import secrets
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .paths import user_data_dir
from .web_review import MAX_UPLOAD, CorrectionStore, ReviewError, ReviewService, Snapshot

ASSETS = Path(__file__).parent / "web_assets"


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, service):
        if address[0] != "127.0.0.1":
            raise ValueError("This personal server must bind to loopback only")
        super().__init__(address, Handler)
        self.service = service
        self.csrf = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    server: ReviewServer

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_args):
        # No paths, uploaded filenames, image content or credentials in request logs.
        pass

    def allowed(self, write=False):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in hosts}:
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        if write and not secrets.compare_digest(
            self.headers.get("X-Review-Token", ""), self.server.csrf
        ):
            return False
        return True

    def respond(self, data, status=200, content_type="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' blob:; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                         "form-action 'self'; object-src 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.allowed():
            return self.respond({"error": "仅允许本机同源访问"}, 403)
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        service = self.server.service
        try:
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.css": ("app.css", "text/css; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8")}
            if parts.path in assets:
                file, content_type = assets[parts.path]
                return self.respond((ASSETS / file).read_bytes(), content_type=content_type)
            if parts.path == "/api/status":
                return self.respond({"app": "delta-loot-local-web", "csrf": self.server.csrf,
                                     "snapshot": service.snapshot.metadata()})
            if parts.path == "/api/session":
                return self.respond({"session": service.view()})
            if parts.path == "/api/llm-settings":
                return self.respond(service.llm_settings.read())
            if parts.path == "/api/catalog":
                text = query.get("q", [""])[0][:120]
                items = service.snapshot.catalog.search(text, 20)
                return self.respond({"items": [service.snapshot.item(item.id) for item in items]})
            if parts.path == "/api/item":
                return self.respond(service.snapshot.item(query.get("id", [""])[0]))
            if parts.path == "/api/icon":
                png = service.snapshot.icon(query.get("id", [""])[0])
                if png is None:
                    return self.respond({"error": "没有缓存图标"}, 404)
                return self.respond(png, content_type="image/png")
            if parts.path == "/api/image":
                png = service.image_bytes(query.get("session", [""])[0],
                                          query.get("key", [None])[0])
                return self.respond(png, content_type="image/png")
            return self.respond({"error": "不存在的地址"}, 404)
        except ReviewError as exc:
            return self.respond({"error": str(exc)}, 400)
        except Exception:
            return self.respond({"error": "本地读取失败，请重启网页版后重试"}, 500)

    def do_POST(self):
        if not self.allowed(write=True):
            return self.respond({"error": "请求验证失败，请从本机页面重试"}, 403)
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ReviewError("不支持分块上传")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD:
                return self.respond({"error": "请求为空或超过 15 MB"}, 413)
            parts = urlsplit(self.path)
            if parts.path != "/api/upload" and length > 65536:
                return self.respond({"error": "纠错内容过大"}, 413)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ReviewError("上传未完成")
            service = self.server.service
            if parts.path == "/api/upload":
                profile = parse_qs(parts.query).get("profile", ["auto"])[0]
                session_id = service.upload(raw, profile)
                return self.respond({"session_id": session_id}, 202)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ReviewError("纠错请求必须使用 JSON")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ReviewError("请求格式无效")
            session_id = data.get("session_id", "")
            if parts.path == "/api/correct":
                return self.respond({"session": service.correct(session_id, data.get("key"), data)})
            if parts.path == "/api/recognize-crop":
                return self.respond({"items": service.suggest_crop(session_id, data.get("key"))})
            if parts.path == "/api/assistant":
                return self.respond(service.ask_agent(session_id, data))
            if parts.path == "/api/llm-settings":
                try:
                    return self.respond(service.llm_settings.save(data))
                except ValueError as exc:
                    raise ReviewError(str(exc)) from None
            if parts.path == "/api/rescan":
                return self.respond({"session_id": service.rescan(session_id, data)}, 202)
            if parts.path == "/api/add":
                session, key = service.add(session_id, data)
                return self.respond({"session": session, "key": key})
            if parts.path == "/api/settings":
                return self.respond({"session": service.settings(session_id, data)})
            return self.respond({"error": "不存在的操作"}, 404)
        except (ReviewError, ValueError, TypeError) as exc:
            message = str(exc) if isinstance(exc, ReviewError) else "输入格式错误，请核对后重试"
            return self.respond({"error": message}, 400)
        except Exception:
            return self.respond({"error": "本地保存失败；本次修改未确认，请重试"}, 500)


def main():
    parser = argparse.ArgumentParser(description="三角洲本机网页估价助手")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    root = user_data_dir()
    snapshot = Snapshot(root / "assistant.sqlite3", root / "templates")
    service = ReviewService(snapshot, CorrectionStore(root / "web-review" / "corrections.sqlite3"))
    try:
        server = ReviewServer(("127.0.0.1", args.port), service)
    except OSError:
        # Verify identity before reusing an occupied local port.
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/status", timeout=2) as r:
                existing = json.load(r)
            if existing.get("app") != "delta-loot-local-web":
                raise RuntimeError("该端口已被其他程序占用")
            if args.open:
                webbrowser.open(f"http://127.0.0.1:{args.port}")
        finally:
            service.close()
        return
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Delta Loot local web: {url}", flush=True)
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()
