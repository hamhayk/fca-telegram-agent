# Fca Soft Telegram Agent v0.1.0

A Telegram bot that delivers new versions of FCA software (APK files) to the branch tablets, and only after you approve them.

## How it works

1. **Each branch tablet** has its own Telegram account. On the tablet, open the bot and press **Start** (or send `/start Gyumri` to include the branch name).
2. **You get a message** "New tablet wants to register" with **Approve** and **Reject** buttons. Once approved, the tablet receives the latest approved version straight away.
3. **To release a version**, send the APK to the bot as a file. The file name must include the version, for example `FcaSoft-v1.4.2.apk`. Anything you write in the caption goes to the branches as release notes.
4. The bot shows you the release and which branches will get it, with **Approve and send** and **Cancel** buttons. Nothing reaches the tablets until you approve.
5. Each tablet receives the APK with an **Installed** button. When staff tap it, you get "Gyumri installed FcaSoft v1.4.2 (3/10)".

Only the Telegram accounts listed in `ADMIN_IDS` can upload, approve, or use the admin commands. Anyone else who presses Start becomes a *pending* tablet that you must approve.

### Admin commands

| Command | What it does |
|---|---|
| `/tablets` | List tablets (🟢 active, 🟡 waiting for approval) with their ids |
| `/setbranch <id> <name>` | Set or change a tablet's branch name |
| `/remove <id>` | Stop sending to a tablet |
| `/releases` | Recent releases and their state |
| `/status [release id]` | Per-branch delivery: ✅ installed, 📨 sent, ⚠️ failed, ⚪ not sent |
| `/resend <release id>` | Send again to every tablet that has not confirmed the install |
| `/backup` | Sends you the database as `FcaSoftAgent_backup_YYYY-MM-DD_HHmm.db` |
| `/help`, `/about` | Help and the agent's version |

## Setup

### 1. Create the bot
1. In Telegram, open **@BotFather**, send `/newbot`, name it "Fca Soft Telegram Agent" and choose a username (e.g. `FcaSoftAgentBot`).
2. Copy the token it gives you.
3. Optional: `/setcommands` in BotFather to show the command menu.
4. Get your own Telegram user id from **@userinfobot**.

### 2. Run the bot on Render (recommended, no server)
See [deploy/render.md](deploy/render.md). It runs like Charge Log: a free Render web service with its database in its own Cloudflare R2 bucket.

### 2a. Run the bot on a Windows PC
1. Install Python 3.12 or newer from python.org and tick **Add python.exe to PATH**.
2. Keep this folder named `telegram-agent`. Double-click `windows\setup.bat`. Notepad opens `.env`: paste the bot token and your user id, save, and close it.
3. Double-click `windows\start.bat`. Leave the window open; send `/start` to your bot and you should see the admin help. Logs go to `data\agent.log`.
4. To start it automatically whenever you sign in to Windows (hidden, no window), run `windows\autostart-on.bat`. `windows\stop.bat` stops it and `windows\autostart-off.bat` turns autostart off.
5. Set Windows **Sleep** to *Never* while plugged in (Settings → System → Power). When the PC is off or asleep, nothing is lost: approvals and deliveries simply wait until it is back.

### 2b. Run the bot on a server instead
It needs a machine that is always on and can reach `api.telegram.org` (an office server, a small VPS, or a Windows PC that stays on). No open ports or domain are needed; the bot polls Telegram.

```bash
cd telegram-agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
# edit .env: BOT_TOKEN and ADMIN_IDS
python run.py
```

To keep it running on a Linux server, use `deploy/fca-agent.service` (systemd) or Docker:

```bash
docker build -t fca-agent .
docker run -d --restart=always --env-file .env -v $PWD/data:/app/data --name fca-agent fca-agent
```

### 3. Prepare each tablet
1. Install Telegram and sign in with that branch's Telegram account.
2. Open the bot (search its username) and press **Start**, or send `/start <branch name>`.
3. Approve it in your chat with the bot.
4. Android: allow Telegram to install apps (**Settings → Apps → Telegram → Install unknown apps → Allow**). Without this the APK will not open.
5. Keep Telegram notifications on for the bot chat so staff see new versions.

## Notes and limits
- A bot cannot message an account that never pressed Start, so every tablet must do step 3.2 once. If a tablet blocks the bot or logs out, delivery shows ⚠️ failed in `/status`; fix the tablet and use `/resend`.
- APKs up to 2 GB work. The bot never downloads the file; it forwards Telegram's own copy to each tablet.
- Telegram cannot install the APK silently. Staff open the file and confirm installation; the **Installed** button is their confirmation to you.
- Data lives in `data/agent.db` (SQLite). Use `/backup` regularly.
- Version of the agent itself is in `fca_agent/__init__.py`.

## Tests
```bash
python -m unittest discover tests
```
The tests run the full flow (register, upload, approve, failed delivery, install confirm, resend) against a fake Telegram.
