# Drop new versions in a Google Drive folder

Put a new APK in one Google Drive folder. The bot checks it every minute, posts the APK in your chat and asks **Approve and send** or **Cancel**. Nothing reaches the tablets until you tap Approve.

**Where to save new versions:** Google Drive → **My Drive → FCA Soft Releases**.
With the Google Drive app on your PC, the same folder is `G:\My Drive\FCA Soft Releases`. The drive letter can differ; look for "Google Drive" in File Explorer.

**File name:** app name, then version: `FcaSoft-v1.5.0.apk`. A name without a version is skipped and the bot tells you why. A version that was already sent to the tablets is skipped too. Files up to 50 MB (Telegram's limit for bots).

**Release notes (optional):** in Drive, right-click the APK → **File information → Details**, and type the notes in **Description** before the bot picks it up. They go to the branches with the file.

## One-time setup

### 1. Make the folder
1. Open https://drive.google.com → **New → New folder** → `FCA Soft Releases`.
2. Open the folder. The address bar shows `https://drive.google.com/drive/folders/<FOLDER ID>`. Copy the part after `folders/`.

### 2. Give the bot a Google login of its own (service account)
In https://console.cloud.google.com (the account you made earlier):
1. Top bar → project picker → **New project** → name it `fca-telegram-agent` → **Create**, and select it.
2. Search box → **Google Drive API** → **Enable**.
3. Search box → **Service accounts** → **Create service account** → name `fca-agent-drive` → **Create and continue** → skip the roles → **Done**.
4. Click the new service account → **Keys** → **Add key → Create new key → JSON → Create**. A `.json` file downloads. Keep it private.
5. Copy the service account's email (it ends in `.iam.gserviceaccount.com`).

### 3. Share the folder with the bot
In Google Drive, right-click **FCA Soft Releases → Share**, paste that email, choose **Viewer**, untick *Notify people*, **Share**.

### 4. Tell Render
Render → **fca-telegram-agent** → **Environment** → **Add environment variable** twice:
- `DRIVE_FOLDER_ID` = the folder ID from step 1
- `GOOGLE_SERVICE_ACCOUNT_JSON` = open the downloaded `.json` file in Notepad, copy **everything**, paste it as the value

**Save, rebuild and deploy.** When it's live the bot messages you "Watching the Drive folder now". APKs already in the folder at that moment count as old.

## Good to know
- Render's free service sleeps after 15 minutes without visitors, and a sleeping bot can't check the folder. Add an UptimeRobot monitor on `https://<your service>.onrender.com/healthz` every 5 minutes so it stays awake, or send `/check` to the bot, which wakes it and looks right away.
- Replacing a file in the folder with a new upload of the same name counts as new, but a version already sent to the tablets is still refused. Raise the version instead.
- The bot only reads the folder; it never changes or deletes files there.
