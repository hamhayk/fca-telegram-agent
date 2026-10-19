# Run the agent on Render (free, no server of your own)

The agent runs on Render the same way Charge Log does: a free Docker web service, with its database streamed by Litestream to its **own** Cloudflare R2 bucket, kept separate from Charge Log. Telegram calls the service whenever something happens (a webhook), which wakes it up if Render put it to sleep.

## 1. Make a separate storage bucket (free)
1. In Cloudflare, open **R2 → Create bucket**, name it `fca-telegram-agent`.
2. **R2 → Manage API tokens → Create API token**: permission *Object Read & Write*, limited to the `fca-telegram-agent` bucket only.
3. Keep these for step 2: the *Access Key ID*, the *Secret Access Key* and the endpoint `https://<account id>.r2.cloudflarestorage.com`.

The R2 free tier (10 GB) is far more than the agent needs: its database is a few hundred KB, since the APKs stay on Telegram.

## 2. Create the service
1. In Render: **New → Blueprint**, then pick the `hamhayk/fca-telegram-agent` repo (branch `main`).
2. Fill in the values it asks for:
   - `BOT_TOKEN`: the token from @BotFather
   - `ADMIN_IDS`: `368674520`
   - `LITESTREAM_*`: from the separate bucket below.
3. Click **Apply**. Wait for the deploy to show **Live**.

## 3. Check it
Send `/start` to your bot in Telegram. It should reply with the admin help. The first reply after Render has been asleep can take up to a minute.

## 4. Keep it awake (recommended)
In UptimeRobot, add an HTTP monitor for `https://<your-service>.onrender.com/healthz` every 5 minutes, like Charge Log. Then replies are instant and Render doesn't sleep.

## Notes
- The bot sets its own webhook from Render's address on each start; nothing to configure in Telegram.
- Without the `LITESTREAM_*` values, registered tablets and releases are lost whenever Render restarts the service.
- Logs: the service's **Logs** tab in Render.
