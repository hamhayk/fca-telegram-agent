"""Run with:  python -m unittest discover tests"""

import asyncio
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


if __name__ == "__main__":
    unittest.main()
