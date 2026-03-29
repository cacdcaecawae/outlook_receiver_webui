from email.mime.text import MIMEText
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import receiver_core


def _message_bytes(sender: str, subject: str, body: str) -> bytes:
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["Subject"] = subject
    return msg.as_bytes()


class _FakeImapState:
    def __init__(self, search_handler, messages):
        self.search_handler = search_handler
        self.messages = messages
        self.connect_attempt = 0

    def factory(self, host, port):
        imap = _FakeImap(self, self.connect_attempt)
        self.connect_attempt += 1
        return imap


class _FakeImap:
    def __init__(self, state: _FakeImapState, attempt: int):
        self._state = state
        self._attempt = attempt
        self._folder = None

    def authenticate(self, mechanism, authcallback):
        return "OK", [b""]

    def select(self, folder):
        self._folder = folder
        return "OK", [b""]

    def search(self, charset, criteria):
        return self._state.search_handler(self._attempt, self._folder, criteria)

    def fetch(self, msg_id, parts):
        if isinstance(msg_id, str):
            msg_id = msg_id.encode()
        payload = self._state.messages.get((self._folder, msg_id))
        if payload is None:
            return "NO", []
        return "OK", [(None, payload)]

    def logout(self):
        return "BYE", [b""]


class ReceiverCoreTests(unittest.TestCase):
    def test_load_accounts_reads_dash_delimited_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "outlook_accounts.txt"
            path.write_text(
                "# comment\n"
                "alpha@example.com----pw1----cid1----rt1\n"
                "beta@example.com:pw2:cid2:rt2\n",
                encoding="utf-8",
            )

            accounts = receiver_core.load_accounts(path)

        self.assertEqual(len(accounts), 2)
        self.assertEqual(accounts[0].email, "alpha@example.com")
        self.assertEqual(accounts[1].client_id, "cid2")

    def test_listener_state_updates_when_message_arrives(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        service = receiver_core.OutlookReceiverService([account], poll_interval=0.01)

        def fake_poll(_account, stop_event):
            return {
                "code": "123456",
                "subject": "Your OpenAI code",
                "from": "account-security@openai.com",
                "received_at": "2026-03-29 19:30:00",
            }

        service.start(0, poller=fake_poll)
        time.sleep(0.05)
        status = service.status()
        service.stop()

        self.assertEqual(status["selected_account"], "alpha@example.com")
        self.assertEqual(status["latest_code"], "123456")
        self.assertEqual(status["state"], "received")

    def test_poll_outlook_account_ignores_existing_junk_mail_at_startup(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        old_junk = _message_bytes(
            "account-security@openai.com",
            "Old OpenAI code",
            "Use 111111 to continue.",
        )
        new_inbox = _message_bytes(
            "account-security@openai.com",
            "Fresh OpenAI code",
            "Use 222222 to continue.",
        )
        state = _FakeImapState(
            search_handler=lambda attempt, folder, criteria: (
                ("OK", [b"50 60"])
                if attempt >= 1 and folder == "INBOX" and criteria == "ALL"
                else ("OK", [b"30"])
                if folder == "Junk" and criteria == "ALL"
                else ("OK", [b""])
            ),
            messages={
                ("Junk", b"30"): old_junk,
                ("INBOX", b"60"): new_inbox,
            },
        )

        with (
            patch.object(receiver_core, "_request_access_token", return_value="access-token"),
            patch.object(receiver_core.imaplib, "IMAP4_SSL", side_effect=state.factory),
        ):
            result = receiver_core.poll_outlook_account(account, threading.Event(), poll_interval=0.01)

        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "222222")
        self.assertEqual(result["subject"], "Fresh OpenAI code")

    def test_poll_outlook_account_prefers_inbox_before_junk_for_new_mail(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        inbox_mail = _message_bytes(
            "account-security@openai.com",
            "Inbox OpenAI code",
            "Use 333333 to continue.",
        )
        junk_mail = _message_bytes(
            "account-security@openai.com",
            "Junk OpenAI code",
            "Use 444444 to continue.",
        )
        state = _FakeImapState(
            search_handler=lambda attempt, folder, criteria: (
                ("OK", [b"90"])
                if attempt >= 1 and folder == "INBOX" and criteria == "ALL"
                else ("OK", [b"80"])
                if attempt >= 1 and folder == "Junk" and criteria == "ALL"
                else ("OK", [b""])
            ),
            messages={
                ("INBOX", b"90"): inbox_mail,
                ("Junk", b"80"): junk_mail,
            },
        )

        with (
            patch.object(receiver_core, "_request_access_token", return_value="access-token"),
            patch.object(receiver_core.imaplib, "IMAP4_SSL", side_effect=state.factory),
        ):
            result = receiver_core.poll_outlook_account(account, threading.Event(), poll_interval=0.01)

        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "333333")
        self.assertEqual(result["subject"], "Inbox OpenAI code")


class WebUiAppTests(unittest.TestCase):
    def test_resolve_accounts_file_defaults_to_local_project_file(self):
        from app import BASE_DIR, resolve_accounts_file

        resolved = resolve_accounts_file()

        self.assertEqual(resolved, BASE_DIR / "outlook_accounts.txt")

    def test_http_handler_returns_account_list_json(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        unavailable = receiver_core.OutlookAccount(email="beta@example.com", password="pw")
        service = receiver_core.OutlookReceiverService([account, unavailable])

        from app import WebUiApp

        webui = WebUiApp(service)
        payload = webui.api_accounts()

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["ready_count"], 1)
        self.assertEqual(payload["unready_count"], 1)
        self.assertEqual(payload["accounts"][0]["email"], "alpha@example.com")
        self.assertEqual(payload["accounts"][0]["password"], "pw")


if __name__ == "__main__":
    unittest.main()
