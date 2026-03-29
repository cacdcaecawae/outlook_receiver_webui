from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import urllib.parse
import webbrowser

from receiver_core import OutlookReceiverService, load_accounts


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def resolve_accounts_file(cli_path: str = "") -> Path:
    if cli_path:
        return Path(cli_path)
    return BASE_DIR / "outlook_accounts.txt"


class WebUiApp:
    def __init__(self, service: OutlookReceiverService, accounts_file: Path | None = None):
        self.service = service
        self.accounts_file = accounts_file or Path("outlook_accounts.txt")

    def api_accounts(self) -> dict:
        accounts = self.service.list_accounts()
        ready_count = sum(1 for account in accounts if account["ready"])
        return {
            "count": len(accounts),
            "ready_count": ready_count,
            "unready_count": len(accounts) - ready_count,
            "accounts_file": str(self.accounts_file),
            "accounts": accounts,
        }

    def api_status(self) -> dict:
        payload = self.service.status()
        payload["accounts_file"] = str(self.accounts_file)
        payload["accounts_count"] = len(self.service.list_accounts())
        return payload

    def api_start(self, account_id: int) -> dict:
        return self.service.start(account_id)

    def api_stop(self) -> dict:
        return self.service.stop()


def _json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = HTTPStatus.OK) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text_response(handler: BaseHTTPRequestHandler, content: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def build_handler(app: WebUiApp):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._serve_static("index.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/api/accounts":
                _json_response(self, app.api_accounts())
                return
            if parsed.path == "/api/status":
                _json_response(self, app.api_status())
                return
            if parsed.path.startswith("/static/"):
                self._serve_static(parsed.path.removeprefix("/static/"))
                return
            _json_response(self, {"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                _json_response(self, {"error": "Invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
                return

            try:
                if parsed.path == "/api/start":
                    account_id = int(payload.get("account_id"))
                    _json_response(self, app.api_start(account_id))
                    return
                if parsed.path == "/api/stop":
                    _json_response(self, app.api_stop())
                    return
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return

            _json_response(self, {"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def _serve_static(self, relative_path: str, content_type: str | None = None):
            path = (STATIC_DIR / relative_path).resolve()
            if STATIC_DIR.resolve() not in path.parents and path != STATIC_DIR.resolve():
                _json_response(self, {"error": "Invalid path"}, status=HTTPStatus.BAD_REQUEST)
                return
            if not path.is_file():
                _json_response(self, {"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            if not content_type:
                if path.suffix == ".js":
                    content_type = "application/javascript; charset=utf-8"
                elif path.suffix == ".css":
                    content_type = "text/css; charset=utf-8"
                else:
                    content_type = "text/plain; charset=utf-8"
            _text_response(self, path.read_bytes(), content_type)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Outlook receiver Web UI")
    parser.add_argument("--accounts-file", default="", help="Path to outlook_accounts.txt")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", default=8765, type=int, help="Port to bind")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    args = parser.parse_args()

    accounts_file = resolve_accounts_file(args.accounts_file)
    accounts = load_accounts(accounts_file)
    service = OutlookReceiverService(accounts)
    app = WebUiApp(service, accounts_file)
    server = ThreadingHTTPServer((args.host, args.port), build_handler(app))
    url = f"http://{args.host}:{args.port}"
    print(f"[WebUI] Listening on {url}")
    print(f"[WebUI] Accounts file: {accounts_file}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        server.server_close()


if __name__ == "__main__":
    main()
