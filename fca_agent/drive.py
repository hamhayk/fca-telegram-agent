"""Watches a Google Drive folder: each new APK dropped there becomes a release waiting for approval."""

import asyncio
import json
import logging
import time

import httpx
from google.auth import crypt, jwt
from telegram.constants import ParseMode

from .bot import MAX_UPLOAD_BYTES, Agent, esc

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
CHECK_EVERY = 60  # seconds


class DriveWatcher:
    def __init__(self, agent: Agent, folder_id: str, service_account_json: str):
        self.agent = agent
        self.store = agent.store
        self.folder_id = folder_id
        self.info = json.loads(service_account_json)
        self.signer = crypt.RSASigner.from_service_account_info(self.info)
        self._token, self._token_expires = None, 0.0
        self._lock = asyncio.Lock()
        self.store.conn.execute(
            "CREATE TABLE IF NOT EXISTS drive_seen ("
            " file_id TEXT NOT NULL, md5 TEXT NOT NULL, name TEXT, seen_at TEXT NOT NULL, result TEXT,"
            " PRIMARY KEY (file_id, md5))"
        )

    @property
    def email(self) -> str:
        return self.info.get("client_email", "")

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        """Service-account login: a signed JWT traded for a one-hour access token."""
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        now = int(time.time())
        token_uri = self.info.get("token_uri", "https://oauth2.googleapis.com/token")
        assertion = jwt.encode(self.signer, {"iss": self.email, "scope": SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600})
        r = await client.post(token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
        if r.is_error:
            # Google names the exact reason (invalid_grant, invalid_jwt, ...) in the body
            raise RuntimeError(f"Drive login failed for {self.email}: {r.status_code} {r.text}")
        self._token, self._token_expires = r.json()["access_token"], now + r.json().get("expires_in", 3600)
        return self._token

    async def list_files(self, client: httpx.AsyncClient) -> list[dict]:
        headers = {"Authorization": f"Bearer {await self._access_token(client)}"}
        r = await client.get(FILES_URL, headers=headers, params={
            "q": f"'{self.folder_id}' in parents and trashed = false",
            "fields": "files(id,name,size,md5Checksum,modifiedTime,description)",
            "orderBy": "modifiedTime",
            "pageSize": 100,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        })
        r.raise_for_status()
        return [f for f in r.json().get("files", []) if f.get("name", "").lower().endswith(".apk")]

    async def download(self, client: httpx.AsyncClient, file_id: str) -> bytes:
        headers = {"Authorization": f"Bearer {await self._access_token(client)}"}
        r = await client.get(f"{FILES_URL}/{file_id}", headers=headers, params={"alt": "media", "supportsAllDrives": "true"})
        r.raise_for_status()
        return r.content

    def _seen(self, f: dict) -> bool:
        return self.store.conn.execute(
            "SELECT 1 FROM drive_seen WHERE file_id=? AND md5=?", (f["id"], f.get("md5Checksum", ""))
        ).fetchone() is not None

    def _mark(self, f: dict, result: str) -> None:
        with self.store.conn:
            self.store.conn.execute(
                "INSERT OR REPLACE INTO drive_seen (file_id, md5, name, seen_at, result) VALUES (?,?,?,datetime('now'),?)",
                (f["id"], f.get("md5Checksum", ""), f["name"], result),
            )

    async def check_once(self, bot) -> int:
        """Look at the folder once. Returns how many new APKs were sent for approval."""
        async with self._lock, httpx.AsyncClient(timeout=120) as client:
            files = await self.list_files(client)
            first_run = self.store.conn.execute("SELECT 1 FROM drive_seen LIMIT 1").fetchone() is None
            if first_run:
                # Files already in the folder when watching starts are history, not new releases
                for f in files:
                    self._mark(f, "present at start")
                self._mark({"id": "_start", "name": "_start"}, "watching started")
                await self.agent.notify_admins_bot(
                    bot, f"📂 Watching the Drive folder now. {len(files)} APK(s) already there are treated as old. "
                         "Put the next version in the folder and I'll ask you about it."
                )
                return 0
            proposed = 0
            for f in files:
                if self._seen(f):
                    continue
                size = int(f.get("size") or 0)
                try:
                    if size > MAX_UPLOAD_BYTES:
                        raise ValueError(f"{size / 1_048_576:.0f} MB is over Telegram's 50 MB limit for bots; send this one by hand")
                    data = await self.download(client, f["id"])
                    await self.agent.upload_release(bot, data, f["name"], (f.get("description") or "").strip(), caption=f"📂 New APK in the Drive folder: {f['name']}")
                    self._mark(f, "proposed")
                    proposed += 1
                except ValueError as e:
                    self._mark(f, f"refused: {e}")
                    await self.agent.notify_admins_bot(bot, f"📂 <b>{esc(f['name'])}</b> in the Drive folder was not sent for approval: {esc(e)}")
            return proposed

    async def run_forever(self, bot) -> None:
        log.info("Watching Drive folder %s every %ss as %s", self.folder_id, CHECK_EVERY, self.email)
        while True:
            try:
                await self.check_once(bot)
            except Exception:  # keep watching through network or Drive hiccups
                log.exception("Drive folder check failed")
            await asyncio.sleep(CHECK_EVERY)


def start_drive_watcher(application) -> None:
    """Start the background folder check if Drive is configured (called once the bot is running)."""
    watcher = application.bot_data.get("drive")
    if watcher:
        application.bot_data["drive_task"] = asyncio.get_running_loop().create_task(watcher.run_forever(application.bot))
