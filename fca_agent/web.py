"""Webhook mode for hosts like Render: Telegram calls us, so a sleeping free service wakes up on each update."""

import hashlib
import logging

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from telegram import Update
from telegram.ext import Application

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

    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")  # for Render's health check and UptimeRobot

    return Starlette(routes=[
        Route("/telegram", telegram, methods=["POST"]),
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
        await server.serve()
        await application.stop()
