# Request to IT: run the Fca Soft Telegram Agent

**Requested by:** Hayk Hambardzumyan
**Purpose:** send approved FCA software updates (APK files) to the 10 branch tablets over Telegram. Nothing is sent until Hayk approves each release.

## What it is
- A small Python program (`run.py`, about 500 lines, source in the `telegram-agent` folder).
- Uses one library: `python-telegram-bot` 21.6 from PyPI.
- Stores its data in a local SQLite file (`data/agent.db`).
- **Network:** outbound HTTPS only, to `api.telegram.org` on port 443. It opens no inbound ports and needs no domain.
- Only Telegram user id 368674520 (Hayk) can approve releases or manage tablets.

## Option A: allow it on Hayk's PC (HQ-SME01)
1. Allow Python 3.12 or newer, with pip access to pypi.org for the one-time install.
2. Allow running from `C:\Users\hayk.hambardzumyan\Desktop\Claude Projek Web\telegram-agent`, either the `windows\*.bat` helpers or `python run.py` directly.
3. Allow outbound HTTPS to `api.telegram.org`.

## Option B: run it on an FCA server (preferred: always on)
- **Docker:** `docker build -t fca-agent .`, then `docker run -d --restart=always --env-file .env -v <data-dir>:/app/data fca-agent`
- **Linux service:** see `deploy/fca-agent.service` (systemd).
- **Windows server:** install Python, run `pip install -r requirements.txt`, and register `python run.py` as a scheduled task or service.
- The `.env` file holds the bot token and the admin id. Hayk will provide it.
- Back up `data/agent.db` with your normal backups, or use the bot's `/backup` command.
