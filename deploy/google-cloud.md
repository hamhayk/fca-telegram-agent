# Run the agent free on Google Cloud

Google Cloud's free tier includes one small always-on server (e2-micro) at no cost. You need a Google account and a bank card for verification. You won't be charged if you stay on the settings below.

## 1. Create the server
1. Go to https://console.cloud.google.com and sign in. Accept the terms and start the free trial (card verification).
2. In the top search box, type **VM instances**, open it, and click **Enable** if asked.
3. Click **Create instance** and set:
   - **Name:** `fca-agent`
   - **Region:** `us-central1` (Iowa), `us-west1` or `us-east1`. Only these are free.
   - **Machine type:** `e2-micro`
   - **Boot disk:** Debian 12, *Standard persistent disk*, 10 GB
4. Click **Create**. Wait for the green check.

## 2. Upload the bot
1. On the `fca-agent` row, click **SSH**. A terminal window opens in the browser.
2. In that window, click **Upload file** (top right) and choose `telegram-agent.zip`.
3. Paste these lines and press Enter:
   ```bash
   sudo apt-get install -y unzip && unzip -o telegram-agent.zip -d telegram-agent && cd telegram-agent && bash deploy/install-linux.sh
   ```
4. When asked, paste your bot token and your Telegram id (368674520).

## 3. Check it
Send `/start` to your bot. It should reply with the admin help.

| To do this | Paste in the SSH window |
|---|---|
| See the logs | `sudo journalctl -u fca-agent -f` |
| Restart | `sudo systemctl restart fca-agent` |
| Stop | `sudo systemctl stop fca-agent` |
