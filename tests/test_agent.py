"""Run with:  python -m unittest discover tests"""

import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram.error import Forbidden

from fca_agent.bot import Agent
from fca_agent.config import Config
from fca_agent.db import Store, parse_apk_name

ADMIN = 1000


class ParseTest(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(parse_apk_name("FcaSoft-v1.4.2.apk"), ("FcaSoft", "1.4.2"))
        self.assertEqual(parse_apk_name("fcasoft_2.0.10.apk"), ("fcasoft", "2.0.10"))
        self.assertEqual(parse_apk_name("Loans-v1.0.0-beta.1.apk"), ("Loans", "1.0.0-beta.1"))
        self.assertIsNone(parse_apk_name("FcaSoft.apk"))
        self.assertIsNone(parse_apk_name("FcaSoft-v1.4.2.zip"))


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "agent.db")
        self.agent = Agent(Config("x", frozenset({ADMIN}), self.tmp / "agent.db", self.tmp / "backups"), self.store)
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()
        self.bot.send_document = AsyncMock()
        self.ctx = SimpleNamespace(bot=self.bot, args=[])

    def update(self, user_id, *, data=None, document=None, caption=None):
        msg = SimpleNamespace(reply_html=AsyncMock(), reply_text=AsyncMock(), reply_document=AsyncMock(),
                              document=document, caption=caption)
        query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(),
                                edit_message_reply_markup=AsyncMock())
        return SimpleNamespace(effective_user=SimpleNamespace(id=user_id, username=f"u{user_id}", full_name=f"Tablet {user_id}"),
                               effective_chat=SimpleNamespace(id=user_id), message=msg, callback_query=query)

    def run_(self, coro):
        return asyncio.run(coro)

    def register(self, uid, branch):
        self.ctx.args = [branch]
        self.run_(self.agent.start(self.update(uid), self.ctx))
        tablet = self.store.tablet_by_chat(uid)
        self.run_(self.agent.on_tablet_decision(self.update(ADMIN, data=f"tab:ok:{tablet['id']}"), self.ctx))
        return tablet["id"]

    def test_full_release_flow(self):
        t1 = self.register(11, "Gyumri")
        t2 = self.register(12, "Vanadzor")
        self.assertEqual(len(self.store.tablets("active")), 2)

        # a tablet cannot publish
        u = self.update(11, document=SimpleNamespace(file_name="FcaSoft-v1.0.0.apk", file_id="F", file_size=1))
        self.run_(self.agent.on_document(u, self.ctx))
        self.assertEqual(self.store.releases(), [])

        # a file without a version is refused
        u = self.update(ADMIN, document=SimpleNamespace(file_name="FcaSoft.apk", file_id="F", file_size=1))
        self.run_(self.agent.on_document(u, self.ctx))
        self.assertEqual(self.store.releases(), [])

        # admin uploads; nothing is sent until approval
        u = self.update(ADMIN, document=SimpleNamespace(file_name="FcaSoft-v1.4.2.apk", file_id="FILE42", file_size=5_000_000),
                        caption="Fixes loan calculator")
        self.run_(self.agent.on_document(u, self.ctx))
        rel = self.store.releases()[0]
        self.assertEqual((rel["version"], rel["status"]), ("1.4.2", "pending"))
        self.bot.send_document.assert_not_called()

        # one tablet blocked the bot: delivery records the failure
        self.bot.send_document.side_effect = [None, Forbidden("bot was blocked by the user")]
        self.run_(self.agent.on_release_decision(self.update(ADMIN, data=f"rel:ok:{rel['id']}"), self.ctx))
        self.assertEqual(self.bot.send_document.call_count, 2)
        status = {r["tablet_id"]: r["status"] for r in self.store.delivery_report(rel["id"])}
        self.assertEqual(status, {t1: "sent", t2: "failed"})

        # a second approval click does nothing
        self.run_(self.agent.on_release_decision(self.update(ADMIN, data=f"rel:ok:{rel['id']}"), self.ctx))
        self.assertEqual(self.bot.send_document.call_count, 2)

        # tablet confirms install, admin is told
        self.run_(self.agent.on_installed(self.update(11, data=f"ins:{rel['id']}"), self.ctx))
        self.assertEqual({r["tablet_id"]: r["status"] for r in self.store.delivery_report(rel["id"])}[t1], "installed")

        # resend only goes to the tablet that has not installed
        self.bot.send_document.side_effect = None
        self.ctx.args = [str(rel["id"])]
        self.run_(self.agent.cmd_resend(self.update(ADMIN), self.ctx))
        self.assertEqual(self.bot.send_document.call_args.args[0], 12)
        status = {r["tablet_id"]: r["status"] for r in self.store.delivery_report(rel["id"])}
        self.assertEqual(status, {t1: "installed", t2: "sent"})

        # a tablet approved later receives the latest release right away
        before = self.bot.send_document.call_count
        self.register(13, "Kapan")
        self.assertEqual(self.bot.send_document.call_count, before + 1)

    def test_cancel_sends_nothing(self):
        self.register(11, "Gyumri")
        u = self.update(ADMIN, document=SimpleNamespace(file_name="FcaSoft-v2.0.0.apk", file_id="F", file_size=1))
        self.run_(self.agent.on_document(u, self.ctx))
        rel = self.store.releases()[0]
        self.run_(self.agent.on_release_decision(self.update(ADMIN, data=f"rel:no:{rel['id']}"), self.ctx))
        self.assertEqual(self.store.release(rel["id"])["status"], "cancelled")
        self.bot.send_document.assert_not_called()

    def test_backup_name(self):
        path = self.store.backup(self.tmp / "backups")
        self.assertRegex(path.name, r"^FcaSoftAgent_backup_\d{4}-\d{2}-\d{2}_\d{4}\.db$")
        self.assertEqual(sqlite3.connect(path).execute("SELECT count(*) FROM tablets").fetchone()[0], 0)


class WebhookTest(unittest.TestCase):
    def test_routes(self):
        from starlette.testclient import TestClient
        from fca_agent.web import build_web_app, webhook_secret

        queue = asyncio.Queue()
        app = SimpleNamespace(update_queue=queue, bot=None)
        client = TestClient(build_web_app(app, "123:ABC"))
        self.assertEqual(client.get("/healthz").text, "ok")
        update = {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": {"id": 5, "type": "private"}, "text": "/start"}}
        self.assertEqual(client.post("/telegram", json=update).status_code, 403)
        ok = client.post("/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret("123:ABC")})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(queue.qsize(), 1)


class UploadTest(unittest.TestCase):
    def setUp(self):
        from starlette.testclient import TestClient
        from fca_agent.web import build_web_app

        tmp = Path(tempfile.mkdtemp())
        self.store = Store(tmp / "agent.db")
        self.agent = Agent(Config("123:ABC", frozenset({ADMIN}), tmp / "agent.db", tmp), self.store)
        self.bot = MagicMock()
        self.bot.send_document = AsyncMock(return_value=SimpleNamespace(
            document=SimpleNamespace(file_id="UPLOADED", file_size=3)))
        self.bot.send_message = AsyncMock()
        app = SimpleNamespace(bot=self.bot, bot_data={"agent": self.agent}, update_queue=asyncio.Queue())
        self.client = TestClient(build_web_app(app, "123:ABC"))
        os.environ["UPLOAD_KEY"] = "s3cret"

    def post(self, name, key="s3cret", notes="Build from CI"):
        return self.client.post("/upload", headers={"Authorization": f"Bearer {key}"},
                                files={"file": ("app-release.apk", b"APK")}, data={"name": name, "notes": notes})

    def test_upload_asks_admin_and_waits(self):
        self.assertEqual(self.post("FcaSoft-v1.5.0.apk", key="wrong").status_code, 401)
        self.assertEqual(self.post("app-release.apk").status_code, 400)  # no version in the name

        r = self.post("FcaSoft-v1.5.0.apk")
        self.assertEqual(r.status_code, 200, r.text)
        rel = self.store.release(r.json()["release"])
        self.assertEqual((rel["app_name"], rel["version"], rel["file_id"], rel["status"], rel["notes"]),
                         ("FcaSoft", "1.5.0", "UPLOADED", "pending", "Build from CI"))
        # the APK went to the admin chat only, followed by the approve/cancel question
        self.assertEqual(self.bot.send_document.call_args.args[0], ADMIN)
        self.assertIn("rel:ok:", str(self.bot.send_message.call_args.kwargs["reply_markup"].to_dict()))

        # once approved, the same version can't be pushed again
        self.store.decide_release(rel["id"], "approved")
        r = self.post("FcaSoft-v1.5.0.apk")
        self.assertEqual(r.status_code, 400)
        self.assertIn("already sent", r.json()["error"])


class DriveTest(unittest.TestCase):
    def test_new_apk_in_folder_is_proposed_once(self):
        import json
        import httpx
        import rsa
        from unittest.mock import patch
        from fca_agent.drive import DriveWatcher

        tmp = Path(tempfile.mkdtemp())
        store = Store(tmp / "agent.db")
        agent = Agent(Config("123:ABC", frozenset({ADMIN}), tmp / "agent.db", tmp), store)
        key = rsa.newkeys(1024)[1].save_pkcs1().decode()
        sa = json.dumps({"client_email": "bot@x.iam.gserviceaccount.com", "private_key": key, "private_key_id": "k",
                         "token_uri": "https://oauth2.googleapis.com/token"})
        watcher = DriveWatcher(agent, "FOLDER", sa)
        files = [{"id": "old", "name": "FcaSoft-v1.0.0.apk", "size": "3", "md5Checksum": "a"}]

        def handler(request: httpx.Request):
            if request.url.host == "oauth2.googleapis.com":
                return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
            assert request.headers["Authorization"] == "Bearer T"
            if request.url.params.get("alt") == "media":
                return httpx.Response(200, content=b"APK")
            return httpx.Response(200, json={"files": files})

        real_client = httpx.AsyncClient
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_document = AsyncMock(return_value=SimpleNamespace(document=SimpleNamespace(file_id="F", file_size=3)))
        with patch("fca_agent.drive.httpx.AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler))):
            self.assertEqual(asyncio.run(watcher.check_once(bot)), 0)  # first look: existing files are old
            files.append({"id": "new", "name": "FcaSoft-v1.1.0.apk", "size": "3", "md5Checksum": "b"})
            files.append({"id": "bad", "name": "FcaSoft-latest.apk", "size": "3", "md5Checksum": "c"})
            self.assertEqual(asyncio.run(watcher.check_once(bot)), 1)
            self.assertEqual(asyncio.run(watcher.check_once(bot)), 0)  # not asked twice

        releases = store.releases()
        self.assertEqual([(r["version"], r["status"]) for r in releases], [("1.1.0", "pending")])
        texts = " ".join(str(c.args[1]) for c in bot.send_message.call_args_list)
        self.assertIn("FcaSoft-latest.apk", texts)  # the admin is told why that one was skipped


if __name__ == "__main__":
    unittest.main()
