from email.mime.text import MIMEText
import json
from pathlib import Path
import subprocess
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

    def test_listener_keeps_listening_after_message_arrives(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        service = receiver_core.OutlookReceiverService([account], poll_interval=0.01)
        entered_second_poll = threading.Event()
        release_second_poll = threading.Event()
        call_count = {"value": 0}

        def fake_poll(_account, stop_event):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return {
                    "code": "123456",
                    "subject": "Your OpenAI code",
                    "from": "account-security@openai.com",
                    "folder": "INBOX",
                    "received_at": "2026-03-29 19:30:00",
                }
            entered_second_poll.set()
            release_second_poll.wait(timeout=1)
            stop_event.set()
            return None

        service.start(0, poller=fake_poll)
        self.assertTrue(entered_second_poll.wait(timeout=1))
        status = service.status()
        release_second_poll.set()
        service.stop()

        self.assertEqual(status["selected_account"], "alpha@example.com")
        self.assertEqual(status["latest_code"], "123456")
        self.assertEqual(status["state"], "listening")
        self.assertEqual(status["folder"], "INBOX")

    def test_listener_tracks_mail_event_metadata(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        service = receiver_core.OutlookReceiverService([account], poll_interval=0.01)
        entered_second_poll = threading.Event()
        release_second_poll = threading.Event()
        call_count = {"value": 0}

        def fake_poll(_account, stop_event):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return {
                    "code": "123456",
                    "subject": "Your OpenAI code",
                    "from": "account-security@openai.com",
                    "folder": "INBOX",
                    "received_at": "2026-03-29 19:30:00",
                    "message_key": "INBOX:77",
                }
            entered_second_poll.set()
            release_second_poll.wait(timeout=1)
            stop_event.set()
            return None

        service.start(0, poller=fake_poll)
        self.assertTrue(entered_second_poll.wait(timeout=1))
        status = service.status()
        release_second_poll.set()
        service.stop()

        self.assertEqual(status["latest_message_key"], "INBOX:77")
        self.assertEqual(status["mail_event_id"], 1)
        self.assertGreaterEqual(status["status_event_id"], 2)

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
        self.assertEqual(result["folder"], "INBOX")

    def test_stop_marks_listener_stopped_after_receiving_a_message(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        service = receiver_core.OutlookReceiverService([account], poll_interval=0.01)
        entered_second_poll = threading.Event()
        release_second_poll = threading.Event()
        call_count = {"value": 0}

        def fake_poll(_account, stop_event):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return {
                    "code": "654321",
                    "subject": "Another OpenAI code",
                    "from": "account-security@openai.com",
                    "folder": "INBOX",
                    "received_at": "2026-03-29 19:31:00",
                }
            entered_second_poll.set()
            release_second_poll.wait(timeout=1)
            return None

        service.start(0, poller=fake_poll)
        self.assertTrue(entered_second_poll.wait(timeout=1))
        status = service.stop()
        release_second_poll.set()

        self.assertEqual(status["state"], "stopped")
        self.assertEqual(status["latest_code"], "654321")

    def test_stale_old_listener_does_not_override_new_selection(self):
        account_a = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw-a",
            client_id="cid-a",
            refresh_token="rt-a",
        )
        account_b = receiver_core.OutlookAccount(
            email="beta@example.com",
            password="pw-b",
            client_id="cid-b",
            refresh_token="rt-b",
        )
        service = receiver_core.OutlookReceiverService([account_a, account_b], poll_interval=0.01)
        old_started = threading.Event()
        old_release = threading.Event()
        new_started = threading.Event()
        new_release = threading.Event()

        def old_poll(_account, _stop_event):
            old_started.set()
            old_release.wait(timeout=4)
            return None

        def new_poll(_account, stop_event):
            new_started.set()
            new_release.wait(timeout=2)
            stop_event.set()
            return None

        service.start(0, poller=old_poll)
        self.assertTrue(old_started.wait(timeout=1))

        # Switch to another account while old poller is still blocked.
        service.start(1, poller=new_poll)
        self.assertTrue(new_started.wait(timeout=1))

        # Let the stale old listener finish after the new one is already active.
        old_release.set()
        time.sleep(0.2)
        status = service.status()

        self.assertEqual(status["selected_index"], 1)
        self.assertEqual(status["selected_account"], "beta@example.com")
        self.assertEqual(status["state"], "listening")

        new_release.set()
        service.stop()

    def test_start_same_account_while_already_listening_is_a_noop(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        service = receiver_core.OutlookReceiverService([account], poll_interval=0.01)
        entered_first = threading.Event()
        release_first = threading.Event()
        second_called = {"value": False}

        def first_poll(_account, _stop_event):
            entered_first.set()
            release_first.wait(timeout=1)
            return None

        def second_poll(_account, _stop_event):
            second_called["value"] = True
            return None

        started = service.start(0, poller=first_poll)
        self.assertTrue(entered_first.wait(timeout=1))
        repeated = service.start(0, poller=second_poll)
        release_first.set()
        service.stop()

        self.assertEqual(started["state"], "listening")
        self.assertEqual(repeated["state"], "listening")
        self.assertFalse(second_called["value"])

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
        self.assertEqual(result["folder"], "INBOX")

    def test_extract_result_accepts_generic_six_digit_mail(self):
        raw_email = _message_bytes(
            "no-reply@example.com",
            "Verification code",
            "Your sign-in code is 445566.",
        )

        result = receiver_core._extract_result_from_message("INBOX", b"10", raw_email)

        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "445566")
        self.assertEqual(result["from"], "no-reply@example.com")

    def test_extract_result_accepts_generic_verification_link_mail(self):
        raw_email = _message_bytes(
            "accounts@example.com",
            "Confirm your email",
            "Open https://example.com/verify?code=link-token-123 to continue.",
        )

        result = receiver_core._extract_result_from_message("INBOX", b"11", raw_email)

        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "link-token-123")
        self.assertEqual(result["from"], "accounts@example.com")


class WebUiAppTests(unittest.TestCase):
    def test_resolve_accounts_file_defaults_to_local_project_file(self):
        from app import BASE_DIR, resolve_accounts_file

        resolved = resolve_accounts_file()

        self.assertEqual(resolved, BASE_DIR / "outlook_accounts.txt")

    def test_load_accounts_from_root_reads_all_outlook_txt_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            primary = root / "outlook_accounts.txt"
            primary.write_text(
                "alpha@example.com----pw1----cid1----rt1\n",
                encoding="utf-8",
            )
            extra = root / "team_outlook_extra.txt"
            extra.write_text(
                "beta@example.com----pw2----cid2----rt2\n",
                encoding="utf-8",
            )
            ignored = root / "notes.txt"
            ignored.write_text(
                "gamma@example.com----pw3----cid3----rt3\n",
                encoding="utf-8",
            )

            from app import load_accounts_from_root

            accounts, files = load_accounts_from_root(primary)

            self.assertEqual([path.name for path in files], ["outlook_accounts.txt", "team_outlook_extra.txt"])
            self.assertEqual([account.email for account in accounts], ["alpha@example.com", "beta@example.com"])

    def test_api_reload_accounts_reads_new_outlook_txt_files_from_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            primary = root / "outlook_accounts.txt"
            primary.write_text(
                "alpha@example.com----pw1----cid1----rt1\n",
                encoding="utf-8",
            )
            groups_file = root / "account_groups.json"

            from app import WebUiApp, load_accounts_from_root

            accounts, files = load_accounts_from_root(primary)
            service = receiver_core.OutlookReceiverService(accounts)
            webui = WebUiApp(service, accounts_file=primary, groups_file=groups_file, accounts_files=files)

            (root / "new_outlook_pool.txt").write_text(
                "beta@example.com----pw2----cid2----rt2\n",
                encoding="utf-8",
            )

            payload = webui.api_reload_accounts()

            self.assertEqual(payload["count"], 2)
            self.assertEqual([Path(path).name for path in payload["accounts_files"]], ["new_outlook_pool.txt", "outlook_accounts.txt"])
            self.assertEqual([account["email"] for account in payload["accounts"]], ["beta@example.com", "alpha@example.com"])

    def test_resolve_groups_file_defaults_to_accounts_sibling_file(self):
        from app import resolve_groups_file

        resolved = resolve_groups_file(Path("E:/tmp/outlook_accounts.txt"))

        self.assertEqual(resolved, Path("E:/tmp/account_groups.json"))

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


    def test_http_handler_groups_accounts_in_batches_of_five(self):
        accounts = [
            receiver_core.OutlookAccount(
                email=f"user{index}@example.com",
                password=f"pw{index}",
                client_id=f"cid{index}",
                refresh_token=f"rt{index}",
            )
            for index in range(11)
        ]
        service = receiver_core.OutlookReceiverService(accounts)

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"

            from app import WebUiApp

            payload = WebUiApp(service, accounts_file=accounts_file, groups_file=groups_file).api_accounts()

        self.assertEqual([group["group_index"] for group in payload["account_groups"]], [1, 2, 3])
        self.assertEqual([group["label"] for group in payload["account_groups"]], ["第 1 组", "第 2 组", "第 3 组"])
        self.assertEqual([len(group["accounts"]) for group in payload["account_groups"]], [5, 5, 1])
        self.assertEqual(
            [account["id"] for account in payload["account_groups"][1]["accounts"]],
            [6, 7, 8, 9, 10],
        )

    def test_api_accounts_creates_default_persisted_groups_with_mother_child_tags(self):
        accounts = [
            receiver_core.OutlookAccount(
                email=f"user{index}@example.com",
                password=f"pw{index}",
                client_id=f"cid{index}",
                refresh_token=f"rt{index}",
            )
            for index in range(6)
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"

            from app import WebUiApp

            payload = WebUiApp(
                receiver_core.OutlookReceiverService(accounts),
                accounts_file=accounts_file,
                groups_file=groups_file,
            ).api_accounts()

            self.assertTrue(groups_file.is_file())
            self.assertEqual(payload["groups_file"], str(groups_file))
            self.assertEqual(payload["group_count"], 2)
            self.assertEqual(
                [account["tag"] for account in payload["account_groups"][0]["accounts"]],
                ["unmarked", "unmarked", "unmarked", "unmarked", "unmarked"],
            )
            self.assertEqual(payload["account_groups"][1]["accounts"][0]["tag"], "unmarked")

    def test_api_accounts_uses_saved_group_config_for_order_name_and_tags(self):
        accounts = [
            receiver_core.OutlookAccount(
                email="alpha@example.com",
                password="pw1",
                client_id="cid1",
                refresh_token="rt1",
            ),
            receiver_core.OutlookAccount(
                email="beta@example.com",
                password="pw2",
                client_id="cid2",
                refresh_token="rt2",
            ),
            receiver_core.OutlookAccount(
                email="gamma@example.com",
                password="pw3",
                client_id="cid3",
                refresh_token="rt3",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"
            groups_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "custom_tags": ["plus"],
                        "groups": [
                            {
                                "id": "vip",
                                "name": "VIP 组",
                                "accounts": [
                                    {"email": "gamma@example.com", "tag": "mother", "note": "主账号"},
                                    {
                                        "email": "alpha@example.com",
                                        "tag": "plus",
                                        "web_usage": "busy",
                                        "note": "",
                                    },
                                ],
                            },
                            {
                                "id": "spare",
                                "name": "备用组",
                                "accounts": [
                                    {
                                        "email": "beta@example.com",
                                        "tag": "banned",
                                        "web_usage": "free",
                                        "note": "已封",
                                    },
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            from app import WebUiApp

            payload = WebUiApp(
                receiver_core.OutlookReceiverService(accounts),
                accounts_file=accounts_file,
                groups_file=groups_file,
            ).api_accounts()

            self.assertEqual([group["id"] for group in payload["account_groups"]], ["vip", "spare"])
            self.assertEqual(payload["custom_tags"], ["plus"])
            self.assertEqual(payload["account_groups"][0]["name"], "VIP 组")
            self.assertEqual(
                [account["email"] for account in payload["account_groups"][0]["accounts"]],
                ["gamma@example.com", "alpha@example.com"],
            )
            self.assertEqual(payload["account_groups"][0]["accounts"][0]["tag"], "mother")
            self.assertEqual(payload["account_groups"][0]["accounts"][0]["note"], "主账号")
            self.assertEqual(payload["account_groups"][0]["accounts"][1]["tag"], "plus")
            self.assertEqual(payload["account_groups"][0]["accounts"][1]["web_usage"], "busy")
            self.assertEqual(payload["account_groups"][1]["accounts"][0]["tag"], "banned")
            self.assertEqual(payload["account_groups"][1]["accounts"][0]["web_usage"], "free")

    def test_api_accounts_drops_stale_groups_with_missing_accounts(self):
        accounts = [
            receiver_core.OutlookAccount(
                email="alpha@example.com",
                password="pw1",
                client_id="cid1",
                refresh_token="rt1",
            ),
            receiver_core.OutlookAccount(
                email="beta@example.com",
                password="pw2",
                client_id="cid2",
                refresh_token="rt2",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"
            groups_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "groups": [
                            {
                                "id": "stale",
                                "name": "旧分组",
                                "accounts": [
                                    {"email": "missing@example.com", "tag": "mother", "note": ""},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            from app import WebUiApp

            payload = WebUiApp(
                receiver_core.OutlookReceiverService(accounts),
                accounts_file=accounts_file,
                groups_file=groups_file,
            ).api_accounts()

            self.assertEqual([group["id"] for group in payload["account_groups"]], ["group-1"])
            self.assertEqual(
                [account["email"] for account in payload["account_groups"][0]["accounts"]],
                ["alpha@example.com", "beta@example.com"],
            )

    def test_api_accounts_migrates_legacy_default_mother_child_tags_to_unmarked(self):
        accounts = [
            receiver_core.OutlookAccount(
                email=f"user{index}@example.com",
                password=f"pw{index}",
                client_id=f"cid{index}",
                refresh_token=f"rt{index}",
            )
            for index in range(5)
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"
            groups_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "groups": [
                            {
                                "id": "group-1",
                                "name": "第 1 组",
                                "accounts": [
                                    {"email": "user0@example.com", "tag": "mother"},
                                    {"email": "user1@example.com", "tag": "child"},
                                    {"email": "user2@example.com", "tag": "child"},
                                    {"email": "user3@example.com", "tag": "child"},
                                    {"email": "user4@example.com", "tag": "child"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            from app import WebUiApp

            payload = WebUiApp(
                receiver_core.OutlookReceiverService(accounts),
                accounts_file=accounts_file,
                groups_file=groups_file,
            ).api_accounts()

            self.assertEqual(
                [account["tag"] for account in payload["account_groups"][0]["accounts"]],
                ["unmarked", "unmarked", "unmarked", "unmarked", "unmarked"],
            )

    def test_api_save_groups_persists_custom_grouping(self):
        accounts = [
            receiver_core.OutlookAccount(
                email="alpha@example.com",
                password="pw1",
                client_id="cid1",
                refresh_token="rt1",
            ),
            receiver_core.OutlookAccount(
                email="beta@example.com",
                password="pw2",
                client_id="cid2",
                refresh_token="rt2",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"

            from app import WebUiApp

            webui = WebUiApp(
                receiver_core.OutlookReceiverService(accounts),
                accounts_file=accounts_file,
                groups_file=groups_file,
            )
            payload = webui.api_save_groups(
                {
                    "custom_tags": ["plus"],
                    "groups": [
                        {
                            "id": "solo",
                            "name": "单独监听",
                            "accounts": [
                                {"email": "beta@example.com", "tag": "mother", "note": "主接码"},
                            ],
                        },
                        {
                            "id": "holding",
                            "name": "待整理",
                            "accounts": [
                                {
                                    "email": "alpha@example.com",
                                    "tag": "plus",
                                    "web_usage": "busy",
                                    "note": "待整理",
                                },
                            ],
                        },
                    ]
                }
            )

            stored = json.loads(groups_file.read_text(encoding="utf-8"))
            self.assertEqual(stored["custom_tags"], ["plus"])
            self.assertEqual([group["id"] for group in stored["groups"]], ["solo", "holding"])
            self.assertEqual(stored["groups"][0]["accounts"][0]["email"], "beta@example.com")
            self.assertEqual(stored["groups"][0]["accounts"][0]["tag"], "mother")
            self.assertEqual(stored["groups"][0]["accounts"][0]["note"], "主接码")
            self.assertEqual(stored["groups"][1]["accounts"][0]["tag"], "plus")
            self.assertEqual(stored["groups"][1]["accounts"][0]["web_usage"], "busy")
            self.assertEqual(payload["account_groups"][0]["name"], "单独监听")
            self.assertEqual(payload["custom_tags"], ["plus"])
            self.assertEqual(payload["account_groups"][0]["accounts"][0]["email"], "beta@example.com")
            self.assertEqual(payload["account_groups"][1]["accounts"][0]["note"], "待整理")
            self.assertEqual(payload["account_groups"][1]["accounts"][0]["tag"], "plus")
            self.assertEqual(payload["account_groups"][1]["accounts"][0]["web_usage"], "busy")

    def test_api_save_groups_preserves_empty_custom_groups(self):
        accounts = [
            receiver_core.OutlookAccount(
                email="alpha@example.com",
                password="pw1",
                client_id="cid1",
                refresh_token="rt1",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"

            from app import WebUiApp

            webui = WebUiApp(
                receiver_core.OutlookReceiverService(accounts),
                accounts_file=accounts_file,
                groups_file=groups_file,
            )
            payload = webui.api_save_groups(
                {
                    "groups": [
                        {
                            "id": "staging",
                            "name": "待整理",
                            "accounts": [],
                        },
                        {
                            "id": "active",
                            "name": "监听中",
                            "accounts": [
                                {"email": "alpha@example.com", "tag": "unmarked", "note": ""},
                            ],
                        },
                    ]
                }
            )

            self.assertEqual([group["id"] for group in payload["account_groups"]], ["staging", "active"])
            self.assertEqual(payload["account_groups"][0]["name"], "待整理")
            self.assertEqual(payload["account_groups"][0]["count"], 0)

    def test_api_save_groups_stops_active_listener_when_account_becomes_banned(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        service = receiver_core.OutlookReceiverService([account], poll_interval=0.01)
        entered_poll = threading.Event()
        release_poll = threading.Event()

        def blocking_poll(_account, _stop_event):
            entered_poll.set()
            release_poll.wait(timeout=1)
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"

            from app import WebUiApp

            webui = WebUiApp(service, accounts_file=accounts_file, groups_file=groups_file)
            service.start(0, poller=blocking_poll)
            self.assertTrue(entered_poll.wait(timeout=1))

            payload = webui.api_save_groups(
                {
                    "groups": [
                        {
                            "id": "group-1",
                            "name": "第 1 组",
                            "accounts": [
                                {"email": "alpha@example.com", "tag": "banned", "note": ""},
                            ],
                        }
                    ]
                }
            )

            release_poll.set()
            status = service.status()

            self.assertEqual(status["state"], "stopped")
            self.assertEqual(payload["listener_status"]["state"], "stopped")
            self.assertFalse(payload["listener_status"]["is_listening"])

    def test_api_start_rejects_banned_accounts(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"
            groups_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "groups": [
                            {
                                "id": "group-1",
                                "name": "第 1 组",
                                "accounts": [
                                    {"email": "alpha@example.com", "tag": "banned", "note": ""},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            from app import WebUiApp

            webui = WebUiApp(
                receiver_core.OutlookReceiverService([account]),
                accounts_file=accounts_file,
                groups_file=groups_file,
            )

            with self.assertRaisesRegex(RuntimeError, "banned"):
                webui.api_start(1)

    def test_ready_count_excludes_banned_accounts(self):
        accounts = [
            receiver_core.OutlookAccount(
                email="alpha@example.com",
                password="pw1",
                client_id="cid1",
                refresh_token="rt1",
            ),
            receiver_core.OutlookAccount(
                email="beta@example.com",
                password="pw2",
                client_id="cid2",
                refresh_token="rt2",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"
            groups_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "groups": [
                            {
                                "id": "group-1",
                                "name": "第 1 组",
                                "accounts": [
                                    {"email": "alpha@example.com", "tag": "unmarked", "note": ""},
                                    {"email": "beta@example.com", "tag": "banned", "note": ""},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            from app import WebUiApp

            payload = WebUiApp(
                receiver_core.OutlookReceiverService(accounts),
                accounts_file=accounts_file,
                groups_file=groups_file,
            ).api_accounts()

            self.assertEqual(payload["ready_count"], 1)

    def test_public_account_ids_are_one_based(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"

            from app import WebUiApp

            payload = WebUiApp(
                receiver_core.OutlookReceiverService([account]),
                accounts_file=accounts_file,
                groups_file=groups_file,
            ).api_accounts()

            self.assertEqual(payload["accounts"][0]["id"], 1)
            self.assertEqual(payload["account_groups"][0]["accounts"][0]["id"], 1)

    def test_account_payload_exposes_listenable_and_disabled_reason(self):
        accounts = [
            receiver_core.OutlookAccount(
                email="alpha@example.com",
                password="pw1",
                client_id="cid1",
                refresh_token="rt1",
            ),
            receiver_core.OutlookAccount(
                email="beta@example.com",
                password="pw2",
                client_id="cid2",
                refresh_token="rt2",
            ),
            receiver_core.OutlookAccount(email="gamma@example.com", password="pw3"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "outlook_accounts.txt"
            accounts_file.write_text("placeholder", encoding="utf-8")
            groups_file = Path(tmpdir) / "account_groups.json"
            groups_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "groups": [
                            {
                                "id": "group-1",
                                "name": "第 1 组",
                                "accounts": [
                                    {"email": "alpha@example.com", "tag": "unmarked", "note": ""},
                                    {"email": "beta@example.com", "tag": "banned", "note": ""},
                                    {"email": "gamma@example.com", "tag": "unmarked", "note": ""},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            from app import WebUiApp
            payload = WebUiApp(
                receiver_core.OutlookReceiverService(accounts),
                accounts_file=accounts_file,
                groups_file=groups_file,
            ).api_accounts()

            self.assertTrue(payload["accounts"][0]["listenable"])
            self.assertIsNone(payload["accounts"][0]["disabled_reason"])
            self.assertFalse(payload["accounts"][1]["listenable"])
            self.assertEqual(payload["accounts"][1]["disabled_reason"], "banned")
            self.assertFalse(payload["accounts"][2]["listenable"])
            self.assertEqual(payload["accounts"][2]["disabled_reason"], "missing_credentials")

    def test_status_payload_exposes_explicit_button_state_flags(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        service = receiver_core.OutlookReceiverService([account], poll_interval=0.01)

        from app import WebUiApp
        webui = WebUiApp(service)
        idle_payload = webui.api_status()
        self.assertFalse(idle_payload["is_listening"])
        self.assertFalse(idle_payload["can_stop"])
        self.assertIsNone(idle_payload["active_account_id"])

        import threading
        gate = threading.Event()
        started = webui.api_start(1)
        listening_payload = webui.api_status()
        service.stop()

        self.assertEqual(started["state"], "listening")
        self.assertTrue(listening_payload["is_listening"])
        self.assertTrue(listening_payload["can_stop"])
        self.assertEqual(listening_payload["active_account_id"], 1)

    def test_webui_publishes_mail_events_with_public_account_ids(self):
        account = receiver_core.OutlookAccount(
            email="alpha@example.com",
            password="pw",
            client_id="cid",
            refresh_token="rt",
        )
        service = receiver_core.OutlookReceiverService([account], poll_interval=0.01)

        from app import WebUiApp

        webui = WebUiApp(service)
        initial_events = webui.events.wait_for_events(0, timeout=0.01)
        last_event_id = initial_events[-1]["id"] if initial_events else 0
        entered_second_poll = threading.Event()
        release_second_poll = threading.Event()
        call_count = {"value": 0}

        def fake_poll(_account, stop_event):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return {
                    "code": "888888",
                    "subject": "Fresh OpenAI code",
                    "from": "account-security@openai.com",
                    "folder": "INBOX",
                    "received_at": "2026-03-29 19:45:00",
                    "message_key": "INBOX:88",
                }
            entered_second_poll.set()
            release_second_poll.wait(timeout=1)
            stop_event.set()
            return None

        service.start(0, poller=fake_poll)
        self.assertTrue(entered_second_poll.wait(timeout=1))
        events = webui.events.wait_for_events(last_event_id, timeout=1)
        release_second_poll.set()
        service.stop()

        mail_events = [event for event in events if event["event"] == "mail"]
        self.assertTrue(mail_events)
        self.assertEqual(mail_events[-1]["data"]["active_account_id"], 1)
        self.assertEqual(mail_events[-1]["data"]["latest_code"], "888888")
        self.assertEqual(mail_events[-1]["data"]["mail_event_id"], 1)


if __name__ == "__main__":
    unittest.main()

