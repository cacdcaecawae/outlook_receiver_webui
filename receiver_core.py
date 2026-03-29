from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import email as email_lib
from email.header import decode_header
import imaplib
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Optional
import urllib.parse
import urllib.request


OUTLOOK_IMAP_HOST = "outlook.office365.com"
OUTLOOK_IMAP_PORT = 993
OUTLOOK_IMAP_RECENT_LIMIT = 25
DEFAULT_POLL_INTERVAL = 3.0
OPENAI_HINTS = ("openai", "chatgpt", "auth.openai.com", "platform.openai.com")
CODE_REGEX = re.compile(r"(?<!\d)(\d{6})(?!\d)")
LINK_REGEX = re.compile(r'https?://[^\s"\'<>]+(?:verify|confirm|activation|email-verification)[^\s"\'<>]*')


@dataclass(slots=True)
class OutlookAccount:
    email: str
    password: str = ""
    client_id: str = ""
    refresh_token: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.client_id and self.refresh_token)


def load_accounts(path: str | Path) -> list[OutlookAccount]:
    accounts: list[OutlookAccount] = []
    src = Path(path)
    if not src.is_file():
        return accounts

    for raw_line in src.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in (line.split("----") if "----" in line else line.split(":", 3))]
        if len(parts) < 2:
            continue
        account = OutlookAccount(email=parts[0], password=parts[1])
        if len(parts) >= 4:
            account.client_id = parts[2]
            account.refresh_token = parts[3]
        accounts.append(account)
    return accounts


def _decode_mime_str(value: str) -> str:
    decoded: list[str] = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded)


def _get_email_body(message) -> str:
    body_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            body_parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            body_parts.append(payload.decode(charset, errors="replace"))
    return "\n".join(body_parts)


def _looks_like_openai_mail(sender: str, subject: str, body: str) -> bool:
    content = "\n".join([sender, subject, body]).lower()
    return any(hint in content for hint in OPENAI_HINTS)


def _message_key(folder: str, msg_id: bytes) -> str:
    return f"{folder}:{msg_id.decode('ascii', errors='ignore')}"


def _recent_message_refs(imap: imaplib.IMAP4_SSL) -> list[tuple[str, bytes]]:
    refs: list[tuple[str, bytes]] = []
    seen_keys: set[str] = set()
    for folder in ("INBOX", "Junk", "Junk Email"):
        try:
            status, _ = imap.select(folder)
            if status != "OK":
                continue
            status, messages = imap.search(None, "ALL")
            if status != "OK" or not messages or not messages[0]:
                continue
            for msg_id in messages[0].split()[-OUTLOOK_IMAP_RECENT_LIMIT:]:
                key = _message_key(folder, msg_id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                refs.append((folder, msg_id))
        except Exception:
            continue
    return refs


def _request_access_token(account: OutlookAccount) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": account.client_id,
            "grant_type": "refresh_token",
            "refresh_token": account.refresh_token,
            "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
        }
    ).encode()
    request = urllib.request.Request(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read())
    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    if refresh_token and refresh_token != account.refresh_token:
        account.refresh_token = refresh_token
    if not access_token:
        raise RuntimeError("Microsoft token response did not include access_token")
    return access_token


def _extract_result_from_message(folder: str, msg_id: bytes, raw_email: bytes) -> Optional[dict[str, str]]:
    message = email_lib.message_from_bytes(raw_email)
    subject = _decode_mime_str(message.get("Subject", ""))
    sender = _decode_mime_str(message.get("From", ""))
    body = _get_email_body(message)
    if not _looks_like_openai_mail(sender, subject, body):
        return None

    content = "\n".join([sender, subject, body])
    code_match = CODE_REGEX.search(content)
    if not code_match:
        link_match = LINK_REGEX.search(content)
        if link_match:
            code_match = re.search(r"[?&]code=([^&\s]+)", link_match.group(0))
    if not code_match:
        return None

    return {
        "code": code_match.group(1) if code_match.lastindex else code_match.group(0),
        "subject": subject,
        "from": sender,
        "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message_key": _message_key(folder, msg_id),
    }


def poll_outlook_account(account: OutlookAccount, stop_event: threading.Event, poll_interval: float = DEFAULT_POLL_INTERVAL) -> Optional[dict[str, str]]:
    if not account.ready:
        raise RuntimeError("Selected account is missing client_id or refresh_token")

    seen_keys: set[str] = set()
    access_token = _request_access_token(account)

    while not stop_event.is_set():
        try:
            imap = imaplib.IMAP4_SSL(OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT)
            auth = f"user={account.email}\x01auth=Bearer {access_token}\x01\x01"
            imap.authenticate("XOAUTH2", lambda _: auth.encode())
            message_refs = _recent_message_refs(imap)
            for folder, msg_id in reversed(message_refs):
                message_key = _message_key(folder, msg_id)
                if message_key in seen_keys:
                    continue
                seen_keys.add(message_key)
                status, _ = imap.select(folder)
                if status != "OK":
                    continue
                status, msg_data = imap.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw_email = msg_data[0][1]
                if not raw_email:
                    continue
                result = _extract_result_from_message(folder, msg_id, raw_email)
                if result:
                    imap.logout()
                    return result
            imap.logout()
        except imaplib.IMAP4.error:
            access_token = _request_access_token(account)
        except Exception:
            pass
        stop_event.wait(poll_interval)

    return None


class OutlookReceiverService:
    def __init__(self, accounts: list[OutlookAccount], poll_interval: float = DEFAULT_POLL_INTERVAL):
        self._accounts = accounts
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._status: dict[str, Any] = {
            "state": "idle",
            "selected_index": None,
            "selected_account": "",
            "latest_code": "",
            "subject": "",
            "from": "",
            "received_at": "",
            "error": "",
        }

    def list_accounts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": index,
                "email": account.email,
                "ready": account.ready,
            }
            for index, account in enumerate(self._accounts)
        ]

    def start(self, account_index: int, poller: Optional[Callable[[OutlookAccount, threading.Event], Optional[dict[str, str]]]] = None) -> dict[str, Any]:
        if account_index < 0 or account_index >= len(self._accounts):
            raise IndexError("Invalid account selection")
        self.stop()
        account = self._accounts[account_index]
        if not account.ready:
            raise RuntimeError("Selected account is missing client_id or refresh_token")

        self._stop_event = threading.Event()
        with self._lock:
            self._status.update(
                {
                    "state": "listening",
                    "selected_index": account_index,
                    "selected_account": account.email,
                    "latest_code": "",
                    "subject": "",
                    "from": "",
                    "received_at": "",
                    "error": "",
                }
            )

        effective_poller = poller or (lambda selected, stop_event: poll_outlook_account(selected, stop_event, self._poll_interval))
        self._thread = threading.Thread(
            target=self._run_listener,
            args=(account, effective_poller, self._stop_event),
            daemon=True,
        )
        self._thread.start()
        return self.status()

    def _run_listener(
        self,
        account: OutlookAccount,
        poller: Callable[[OutlookAccount, threading.Event], Optional[dict[str, str]]],
        stop_event: threading.Event,
    ) -> None:
        try:
            result = poller(account, stop_event)
            with self._lock:
                if stop_event.is_set():
                    self._status["state"] = "stopped"
                    return
                if result:
                    self._status.update(
                        {
                            "state": "received",
                            "latest_code": result.get("code", ""),
                            "subject": result.get("subject", ""),
                            "from": result.get("from", ""),
                            "received_at": result.get("received_at", ""),
                            "error": "",
                        }
                    )
                else:
                    self._status["state"] = "idle"
        except Exception as exc:
            with self._lock:
                self._status["state"] = "error"
                self._status["error"] = str(exc)

    def stop(self) -> dict[str, Any]:
        thread = self._thread
        if thread and thread.is_alive():
            self._stop_event.set()
            thread.join(timeout=1.5)
        self._thread = None
        with self._lock:
            if self._status["state"] == "listening":
                self._status["state"] = "stopped"
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)
