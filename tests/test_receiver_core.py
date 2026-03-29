from pathlib import Path
import tempfile
import time
import unittest

import receiver_core


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


