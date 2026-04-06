from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.parse
import webbrowser

from account_groups import (
    DEFAULT_GROUP_SIZE,
    ensure_group_config,
    materialize_account_groups,
    normalize_submitted_group_config,
    resolve_groups_file,
    write_group_config,
)
from receiver_core import OutlookReceiverService, load_accounts


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def resolve_accounts_file(cli_path: str = "") -> Path:
    if cli_path:
        return Path(cli_path)
    return BASE_DIR / "outlook_accounts.txt"


def discover_accounts_files(accounts_file: Path) -> list[Path]:
    root = accounts_file.parent
    matches = sorted(
        [path for path in root.glob("*.txt") if "outlook" in path.name.lower()],
        key=lambda path: path.name.lower(),
    )
    if accounts_file.is_file() and accounts_file not in matches:
        matches.append(accounts_file)
        matches.sort(key=lambda path: path.name.lower())
    return matches


def load_accounts_from_root(accounts_file: Path) -> tuple[list, list[Path]]:
    files = discover_accounts_files(accounts_file)
    deduped_accounts: dict[str, object] = {}
    for path in files:
        for account in load_accounts(path):
            deduped_accounts.pop(account.email, None)
            deduped_accounts[account.email] = account
    return list(deduped_accounts.values()), files


class WebUiApp:
    def __init__(
        self,
        service: OutlookReceiverService,
        accounts_file: Path | None = None,
        groups_file: Path | None = None,
        accounts_files: list[Path] | None = None,
    ):
        self.service = service
        self.accounts_file = accounts_file or Path("outlook_accounts.txt")
        self.groups_file = groups_file or resolve_groups_file(self.accounts_file)
        self.accounts_root = self.accounts_file.parent
        self.accounts_files = list(accounts_files or discover_accounts_files(self.accounts_file))
        self.events = EventStreamBroker()
        self._event_lock = threading.Lock()
        self._latest_mail_event_id = 0
        self._unsubscribe_status = self.service.subscribe(self._handle_status_event)

    @staticmethod
    def _to_public_account_id(account_id: int | None) -> int | None:
        if account_id is None:
            return None
        return account_id + 1

    @staticmethod
    def _to_internal_account_id(account_id: int) -> int:
        return account_id - 1

    def _public_status_payload(self, payload: dict) -> dict:
        public_payload = dict(payload)
        selected_index = public_payload.get("selected_index")
        if isinstance(selected_index, int):
            public_payload["selected_index"] = self._to_public_account_id(selected_index)
        public_payload["is_listening"] = public_payload.get("state") == "listening"
        public_payload["active_account_id"] = public_payload.get("selected_index")
        public_payload["can_stop"] = public_payload.get("state") == "listening"
        return public_payload

    def _handle_status_event(self, payload: dict) -> None:
        public_payload = self._public_status_payload(payload)
        self.events.publish("status", public_payload)

        mail_event_id = int(public_payload.get("mail_event_id") or 0)
        if not mail_event_id:
            return

        with self._event_lock:
            if mail_event_id == self._latest_mail_event_id:
                return
            self._latest_mail_event_id = mail_event_id

        self.events.publish("mail", public_payload)

    def _materialize_accounts(self) -> tuple[dict, list[dict], list[dict]]:
        accounts = self.service.list_accounts()
        config = ensure_group_config(self.groups_file, accounts, group_size=DEFAULT_GROUP_SIZE)
        account_groups, ordered_accounts = materialize_account_groups(config, accounts)
        return config, account_groups, ordered_accounts

    def _serialize_accounts_payload(self) -> dict:
        config, account_groups, ordered_accounts = self._materialize_accounts()
        for account in ordered_accounts:
            account["id"] = self._to_public_account_id(account["id"])

        ready_count = sum(1 for account in ordered_accounts if account.get("listenable"))
        return {
            "count": len(ordered_accounts),
            "ready_count": ready_count,
            "unready_count": len(ordered_accounts) - ready_count,
            "group_count": len(account_groups),
            "accounts_file": str(self.accounts_file),
            "accounts_root": str(self.accounts_root),
            "accounts_files": [str(path) for path in self.accounts_files],
            "groups_file": str(self.groups_file),
            "accounts": ordered_accounts,
            "account_group_size": DEFAULT_GROUP_SIZE,
            "custom_tags": config.get("custom_tags", []),
            "account_groups": account_groups,
        }

    def api_accounts(self) -> dict:
        return self._serialize_accounts_payload()

    def api_reload_accounts(self) -> dict:
        current_status = self.service.status()
        was_listening = current_status.get("state") == "listening"
        active_email = str(current_status.get("selected_account") or "")

        if was_listening:
            self.service.stop()

        accounts, files = load_accounts_from_root(self.accounts_file)
        self.accounts_files = files
        self.service.set_accounts(accounts)

        if was_listening and active_email:
            _, _, ordered_accounts = self._materialize_accounts()
            active_account = next((account for account in ordered_accounts if account["email"] == active_email), None)
            if active_account and active_account.get("listenable"):
                self.service.start(active_account["id"])

        response = self._serialize_accounts_payload()
        response["listener_status"] = self.api_status()
        return response

    def api_save_groups(self, payload: dict) -> dict:
        accounts = self.service.list_accounts()
        config = normalize_submitted_group_config(payload, accounts)
        write_group_config(self.groups_file, config)

        current_status = self.service.status()
        if current_status.get("state") == "listening":
            _, ordered_accounts = materialize_account_groups(config, accounts)
            active_email = current_status.get("selected_account")
            active_account = next((account for account in ordered_accounts if account["email"] == active_email), None)
            if active_account and not active_account.get("listenable"):
                self.service.stop()
        response = self._serialize_accounts_payload()
        response["listener_status"] = self.api_status()
        return response

    def api_status(self) -> dict:
        payload = self._public_status_payload(self.service.status())
        payload["accounts_file"] = str(self.accounts_file)
        payload["groups_file"] = str(self.groups_file)
        payload["accounts_count"] = len(self.service.list_accounts())
        return payload

    def api_start(self, account_id: int) -> dict:
        internal_account_id = self._to_internal_account_id(account_id)
        _, _, ordered_accounts = self._materialize_accounts()
        target_account = next((account for account in ordered_accounts if account["id"] == internal_account_id), None)
        if target_account is None:
            raise IndexError("Invalid account selection")
        if not target_account.get("listenable"):
            if target_account.get("disabled_reason") == "banned":
                raise RuntimeError("Selected account is banned")
            raise RuntimeError("Selected account is missing client_id or refresh_token")
        return self._public_status_payload(self.service.start(internal_account_id))

    def api_stop(self) -> dict:
        return self._public_status_payload(self.service.stop())

    def stream_events(self, handler: BaseHTTPRequestHandler, last_event_id: int = 0) -> None:
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        current_last_event_id = max(last_event_id, 0)
        while True:
            events = self.events.wait_for_events(current_last_event_id, timeout=15.0)
            try:
                if not events:
                    handler.wfile.write(b": ping\n\n")
                    handler.wfile.flush()
                    continue

                for event in events:
                    payload = json.dumps(event["data"], ensure_ascii=False)
                    body = (
                        f"id: {event['id']}\n"
                        f"event: {event['event']}\n"
                        f"data: {payload}\n\n"
                    ).encode("utf-8")
                    handler.wfile.write(body)
                    handler.wfile.flush()
                    current_last_event_id = event["id"]
            except (BrokenPipeError, ConnectionResetError):
                return


class EventStreamBroker:
    def __init__(self, max_events: int = 128):
        self._max_events = max_events
        self._condition = threading.Condition()
        self._events: list[dict] = []
        self._next_id = 1

    def publish(self, event: str, data: dict) -> dict:
        with self._condition:
            payload = {
                "id": self._next_id,
                "event": event,
                "data": dict(data),
            }
            self._next_id += 1
            self._events.append(payload)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]
            self._condition.notify_all()
            return {
                "id": payload["id"],
                "event": payload["event"],
                "data": dict(payload["data"]),
            }

    def wait_for_events(self, last_event_id: int, timeout: float = 15.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                pending = [
                    {
                        "id": event["id"],
                        "event": event["event"],
                        "data": dict(event["data"]),
                    }
                    for event in self._events
                    if event["id"] > last_event_id
                ]
                if pending:
                    return pending

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)

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
            if parsed.path == "/api/events":
                self._stream_events()
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
                if parsed.path == "/api/reload-accounts":
                    _json_response(self, app.api_reload_accounts())
                    return
                if parsed.path == "/api/groups":
                    _json_response(self, app.api_save_groups(payload))
                    return
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return

            _json_response(self, {"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def _stream_events(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            header_value = self.headers.get("Last-Event-ID", "")
            raw_last_event_id = header_value or (query.get("last_event_id", ["0"])[0])
            try:
                last_event_id = int(raw_last_event_id)
            except (TypeError, ValueError):
                last_event_id = 0
            app.stream_events(self, last_event_id=last_event_id)

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
    parser.add_argument("--groups-file", default="", help="Path to account_groups.json")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", default=8765, type=int, help="Port to bind")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    args = parser.parse_args()

    accounts_file = resolve_accounts_file(args.accounts_file)
    groups_file = resolve_groups_file(accounts_file, args.groups_file)
    accounts, account_files = load_accounts_from_root(accounts_file)
    service = OutlookReceiverService(accounts)
    app = WebUiApp(service, accounts_file, groups_file=groups_file, accounts_files=account_files)
    server = ThreadingHTTPServer((args.host, args.port), build_handler(app))
    url = f"http://{args.host}:{args.port}"
    print(f"[WebUI] Listening on {url}")
    print(f"[WebUI] Accounts file: {accounts_file}")
    print(f"[WebUI] Accounts root: {accounts_file.parent}")
    print(f"[WebUI] Groups file: {groups_file}")
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








