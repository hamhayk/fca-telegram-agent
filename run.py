"""Start the Fca Soft Telegram Agent:  python run.py

With PUBLIC_URL (or Render's RENDER_EXTERNAL_URL) set, it runs as a web service that Telegram calls (webhook).
Otherwise it asks Telegram for updates itself (polling), which suits a PC or a VM.
"""

import asyncio
import logging
import os

from fca_agent import APP_NAME, VERSION
from fca_agent.bot import build_application
from fca_agent.config import load_config
from fca_agent.db import Store

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

if __name__ == "__main__":
    config = load_config()
    store = Store(config.db_path)
    logging.info("%s v%s starting, admins=%s", APP_NAME, VERSION, sorted(config.admin_ids))
    if not config.admin_ids:
        logging.warning("ADMIN_IDS is empty: send /start to the bot to see your id, then add it to .env")
    application = build_application(config, store)
    public_url = os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if public_url:
        from fca_agent.web import run_webhook

        asyncio.run(run_webhook(application, config.bot_token, public_url, int(os.environ.get("PORT", "8080"))))
    else:
        application.run_polling(allowed_updates=["message", "callback_query"])
