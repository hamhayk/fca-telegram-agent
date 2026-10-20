"""Webhook mode for hosts like Render: Telegram calls us, so a sleeping free service wakes up on each update."""

import hashlib
import hmac
import logging
import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from telegram import Update
from telegram.ext import Application

from .drive import start_drive_watcher

log = logging.getLogger(__name__)


def webhook_secret(bot_token: str) -> str:
    """Stable secret derived from the token, so Telegram's calls can be told apart from strangers'."""
    return hashlib.sha256(("webhook:" + bot_token).encode()).hexdigest()[:32]


def build_web_app(application: Application, bot_token: str) -> Starlette:
    secret = webhook_secret(bot_token)

    async def telegram(request: Request) -> Response:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            return Response(status_code=403)
        await application.update_queue.put(Update.de_json(await request.json(), application.bot))
        return Response()

    async def upload(request: Request) -> Response:
        """A build script posts the APK here; the bot then asks the admin to approve it."""
        upload_key = os.environ.get("UPLOAD_KEY", "")
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not upload_key or not hmac.compare_digest(given, upload_key):
            return JSONResponse({"ok": False, "error": "wrong or missing upload key"}, status_code=401)
        form = await request.form()
        upload_file = form.get("file")
        if upload_file is None or not hasattr(upload_file, "read"):
            return JSONResponse({"ok": False, "error": "send the APK as the 'file' form field"}, status_code=400)
        file_name = str(form.get("name") or upload_file.filename or "")
        notes = str(form.get("notes") or "").strip()
        try:
            release_id = await application.bot_data["agent"].upload_release(
                application.bot, await upload_file.read(), file_name, notes
            )
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        log.info("Build upload %s became release #%s", file_name, release_id)
        return JSONResponse({"ok": True, "release": release_id, "message": "Sent to Telegram for approval"})

    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")  # for Render's health check and UptimeRobot

    return Starlette(routes=[
        Route("/telegram", telegram, methods=["POST"]),
        Route("/upload", upload, methods=["POST"]),
        Route("/", health),
        Route("/healthz", health),
    ])


async def run_webhook(application: Application, bot_token: str, public_url: str, port: int) -> None:
    url = public_url.rstrip("/") + "/telegram"
    server = uvicorn.Server(uvicorn.Config(build_web_app(application, bot_token), host="0.0.0.0", port=port, log_level="warning"))
    async with application:
        await application.bot.set_webhook(url, secret_token=webhook_secret(bot_token), allowed_updates=["message", "callback_query"])
        log.info("Webhook set to %s", url)
        await application.start()
        start_drive_watcher(application)
        await server.serve()
        await application.stop()
