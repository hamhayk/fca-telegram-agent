"""Telegram handlers for the Fca Soft Telegram Agent."""

import asyncio
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import APP_NAME, VERSION
from .config import Config
from .db import Store, parse_apk_name

log = logging.getLogger(__name__)

ADMIN_HELP = f"""<b>Fca Soft Telegram Agent v{VERSION}</b>

<b>Release a new version</b>
Put the APK in the Drive folder (I check every minute), or send it to me as a file. The name must carry the version, e.g. <code>FcaSoft-v1.4.2.apk</code>.
Anything you write in the caption is sent to the branches as release notes.
I will ask you to approve before anything reaches the tablets.

<b>Tablets</b>
/tablets – list registered tablets
/setbranch &lt;id&gt; &lt;branch name&gt; – name a tablet's branch
/remove &lt;id&gt; – stop sending to a tablet

<b>Releases</b>
/releases – recent releases
/status [release id] – who received and installed it (latest if no id)
/resend &lt;release id&gt; – send again to tablets that have not installed it

/check – look in the Drive folder for a new APK now
/backup – send me a copy of the database
/about – version"""

TABLET_HELP = f"""<b>Fca Soft Telegram Agent v{VERSION}</b>

New versions of FCA software approved for your branch will arrive in this chat.
Open the file to install it, then tap <b>Installed</b> under the message."""


MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # Telegram Bot API limit for files a bot uploads itself


def already_released_text(app_name: str, version: str) -> str:
    return f"<b>{esc(app_name)} v{esc(version)}</b> was already sent to the tablets. Raise the version and send the new file."


def esc(value) -> str:
    return html.escape(str(value or ""))


def tablet_label(t) -> str:
    return f"#{t['id']} {esc(t['branch'] or 'no branch')} ({esc(t['full_name'])})"


class Agent:
    def __init__(self, config: Config, store: Store):
        self.config = config
        self.store = store

    def is_admin(self, update: Update) -> bool:
        return update.effective_user is not None and update.effective_user.id in self.config.admin_ids

    async def notify_admins(self, context: ContextTypes.DEFAULT_TYPE, text: str, markup=None) -> None:
        await self.notify_admins_bot(context.bot, text, markup)

    async def notify_admins_bot(self, bot, text: str, markup=None) -> None:
        for admin_id in self.config.admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode=ParseMode.HTML, reply_markup=markup)
            except TelegramError as e:
                log.warning("Could not reach admin %s: %s", admin_id, e)

    # ---- /start and registration ----------------------------------------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.config.admin_ids:  # first run: tell the owner their id instead of registering anyone
            await update.message.reply_html(
                f"Your Telegram id is <code>{update.effective_user.id}</code>.\n\n"
                "Put it after <code>ADMIN_IDS=</code> in the <code>.env</code> file, save, and restart the agent."
            )
            return
        if self.is_admin(update):
            await update.message.reply_html(ADMIN_HELP)
            return
        user = update.effective_user
        branch = " ".join(context.args).strip() or None
        tablet, created = self.store.register_tablet(update.effective_chat.id, user.username, user.full_name, branch)
        if tablet["status"] == "active":
            await update.message.reply_html(f"This tablet is registered for branch <b>{esc(tablet['branch'])}</b>.\n\n{TABLET_HELP}")
            return
        await update.message.reply_text("Registration request sent. You will get a message here once it is approved.")
        if created:
            buttons = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve tablet", callback_data=f"tab:ok:{tablet['id']}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"tab:no:{tablet['id']}"),
            ]])
            await self.notify_admins(
                context,
                f"📱 New tablet wants to register\n{tablet_label(tablet)}"
                f"{' @' + esc(user.username) if user.username else ''}\n\n"
                f"After approving, set its branch with /setbranch {tablet['id']} &lt;name&gt; if it is missing.",
                buttons,
            )

    async def on_tablet_decision(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not self.is_admin(update):
            await query.answer("Only the administrator can do this.", show_alert=True)
            return
        _, action, tablet_id = query.data.split(":")
        tablet = self.store.tablet(int(tablet_id))
        if not tablet or tablet["status"] != "pending":
            await query.answer("Already handled.")
            await query.edit_message_reply_markup(None)
            return
        if action == "ok":
            self.store.set_tablet_status(tablet["id"], "active")
            await query.edit_message_text(f"✅ Approved {tablet_label(tablet)}", parse_mode=ParseMode.HTML)
            await self._safe_send(context, tablet["chat_id"], f"✅ This tablet is approved.\n\n{TABLET_HELP}")
            latest = self.store.latest_release()
            if latest:  # bring a new tablet up to date straight away
                await self.deliver(context, latest, [self.store.tablet(tablet["id"])])
        else:
            self.store.set_tablet_status(tablet["id"], "removed")
            await query.edit_message_text(f"❌ Rejected {tablet_label(tablet)}", parse_mode=ParseMode.HTML)
            await self._safe_send(context, tablet["chat_id"], "Your registration was not approved.")
        await query.answer()

    # ---- new release upload and approval -------------------------------
    async def on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_admin(update):
            await update.message.reply_text("Only the administrator can publish new versions.")
            return
        doc = update.message.document
        parsed = parse_apk_name(doc.file_name or "")
        if not parsed:
            await update.message.reply_html(
                "I need an <b>.apk</b> whose file name includes the version, "
                "for example <code>FcaSoft-v1.4.2.apk</code>. Please rename it and send again."
            )
            return
        app_name, version = parsed
        if self.store.approved_release_exists(app_name, version):
            await update.message.reply_html(already_released_text(app_name, version))
            return
        notes = (update.message.caption or "").strip()
        release_id = self.store.add_release(
            app_name, version, doc.file_id, doc.file_name, doc.file_size, notes, update.effective_user.id
        )
        text, buttons = self.release_prompt(release_id)
        await update.message.reply_html(text, reply_markup=buttons)

    def release_prompt(self, release_id: int):
        """The 'approve this release?' message and its buttons."""
        release = self.store.release(release_id)
        active = self.store.tablets("active")
        size_mb = (release["file_size"] or 0) / 1_048_576
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Approve and send to {len(active)} tablets", callback_data=f"rel:ok:{release_id}"),
        ], [
            InlineKeyboardButton("❌ Cancel", callback_data=f"rel:no:{release_id}"),
        ]])
        text = (
            f"📦 <b>{esc(release['app_name'])} v{esc(release['version'])}</b> ({size_mb:.1f} MB), release #{release_id}\n"
            f"Notes: {esc(release['notes']) or '<i>none</i>'}\n\n"
            f"Branches: {', '.join(esc(t['branch'] or '#' + str(t['id'])) for t in active) or '<i>no active tablets yet</i>'}\n\n"
            "Approve to send it now?"
        )
        return text, buttons

    async def upload_release(self, bot, data: bytes, file_name: str, notes: str, caption: str = "🏗 New build received") -> int:
        """A build pushed an APK: put it on Telegram via the admin chat and ask for approval.

        Raises ValueError with a readable reason when the file can't become a release.
        """
        parsed = parse_apk_name(file_name)
        if not parsed:
            raise ValueError(f"'{file_name}' is not an .apk with a version in its name, e.g. FcaSoft-v1.4.2.apk")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"{len(data) / 1_048_576:.0f} MB is over Telegram's 50 MB limit for bots; send this one by hand")
        app_name, version = parsed
        if self.store.approved_release_exists(app_name, version):
            raise ValueError(f"{app_name} v{version} was already sent to the tablets; raise the version and build again")
        if not self.config.admin_ids:
            raise ValueError("ADMIN_IDS is not set, so nobody could approve it")
        first_admin, *other_admins = sorted(self.config.admin_ids)
        # Uploading to the admin chat both shows Hayk the file and gives us a file_id to forward later
        message = await bot.send_document(first_admin, data, filename=file_name, caption=caption)
        doc = message.document
        release_id = self.store.add_release(app_name, version, doc.file_id, file_name, doc.file_size, notes, first_admin)
        text, buttons = self.release_prompt(release_id)
        for admin_id in [first_admin, *other_admins]:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.HTML, reply_markup=buttons)
        return release_id

    async def on_release_decision(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not self.is_admin(update):
            await query.answer("Only the administrator can do this.", show_alert=True)
            return
        _, action, release_id = query.data.split(":")
        release_id = int(release_id)
        status = "approved" if action == "ok" else "cancelled"
        if not self.store.decide_release(release_id, status):
            await query.answer("This release was already handled.")
            await query.edit_message_reply_markup(None)
            return
        await query.answer()
        release = self.store.release(release_id)
        title = f"<b>{esc(release['app_name'])} v{esc(release['version'])}</b>"
        if status == "cancelled":
            await query.edit_message_text(f"❌ {title} cancelled. Nothing was sent.", parse_mode=ParseMode.HTML)
            return
        await query.edit_message_text(f"⏳ {title} approved, sending…", parse_mode=ParseMode.HTML)
        ok, failed = await self.deliver(context, release, self.store.tablets("active"))
        text = f"✅ {title} approved and sent to {ok} tablet(s)."
        if failed:
            text += "\n\n⚠️ Not delivered:\n" + "\n".join(f"• {tablet_label(t)}: {esc(err)}" for t, err in failed)
            text += f"\n\nFix the tablet and use /resend {release_id}."
        text += f"\n\nTrack installs with /status {release_id}."
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)

    async def deliver(self, context: ContextTypes.DEFAULT_TYPE, release, tablets):
        """Send the release file to each tablet. Returns (sent_count, [(tablet, error), ...])."""
        caption = f"🆕 <b>{esc(release['app_name'])} v{esc(release['version'])}</b>"
        if release["notes"]:
            caption += f"\n\n{esc(release['notes'])}"
        caption += "\n\nOpen the file to install it, then tap <b>Installed</b>."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Installed", callback_data=f"ins:{release['id']}")]])
        sent, failed = 0, []
        for tablet in tablets:
            try:
                # file_id re-uses the file already on Telegram's servers: no download or size limit on our side
                await context.bot.send_document(
                    tablet["chat_id"], release["file_id"], caption=caption[:1024],
                    parse_mode=ParseMode.HTML, reply_markup=markup,
                )
                self.store.record_delivery(release["id"], tablet["id"], True)
                sent += 1
            except TelegramError as e:
                log.warning("Delivery of release %s to tablet %s failed: %s", release["id"], tablet["id"], e)
                self.store.record_delivery(release["id"], tablet["id"], False, str(e))
                failed.append((tablet, str(e)))
            await asyncio.sleep(0.1)  # stay well under Telegram's rate limits
        return sent, failed

    async def on_installed(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        release_id = int(query.data.split(":")[1])
        tablet = self.store.tablet_by_chat(update.effective_chat.id)
        if not tablet or not self.store.confirm_installed(release_id, tablet["id"]):
            await query.answer("Already recorded. Thank you!")
            return
        await query.answer("Thank you! Installation recorded.")
        await query.edit_message_reply_markup(None)
        release = self.store.release(release_id)
        report = self.store.delivery_report(release_id)
        done = sum(1 for r in report if r["status"] == "installed")
        await self.notify_admins(
            context,
            f"📲 {esc(tablet['branch'] or tablet['full_name'])} installed "
            f"{esc(release['app_name'])} v{esc(release['version'])} ({done}/{len(report)})",
        )

    # ---- admin commands -------------------------------------------------
    async def cmd_tablets(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_admin(update):
            return
        rows = self.store.tablets()
        if not rows:
            await update.message.reply_text("No tablets yet. Ask each branch tablet to open the bot and press Start.")
            return
        icons = {"active": "🟢", "pending": "🟡"}
        lines = [f"{icons.get(t['status'], '⚪')} {tablet_label(t)}" for t in rows]
        await update.message.reply_html("<b>Tablets</b>\n" + "\n".join(lines))

    async def cmd_setbranch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_admin(update):
            return
        if len(context.args) < 2 or not context.args[0].isdigit():
            await update.message.reply_text("Usage: /setbranch <tablet id> <branch name>")
            return
        branch = " ".join(context.args[1:])
        if self.store.set_branch(int(context.args[0]), branch):
            await update.message.reply_text(f"Tablet #{context.args[0]} is now '{branch}'.")
        else:
            await update.message.reply_text("No tablet with that id. See /tablets.")

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_admin(update):
            return
        if len(context.args) != 1 or not context.args[0].isdigit() or not self.store.tablet(int(context.args[0])):
            await update.message.reply_text("Usage: /remove <tablet id> (see /tablets)")
            return
        self.store.set_tablet_status(int(context.args[0]), "removed")
        await update.message.reply_text(f"Tablet #{context.args[0]} removed. It can register again with Start.")

    async def cmd_releases(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_admin(update):
            return
        rows = self.store.releases()
        if not rows:
            await update.message.reply_text("No releases yet. Send me an APK to start one.")
            return
        icons = {"approved": "✅", "pending": "⏳", "cancelled": "❌"}
        lines = [f"{icons[r['status']]} #{r['id']} {esc(r['app_name'])} v{esc(r['version'])} · {r['uploaded_at'][:16].replace('T', ' ')}" for r in rows]
        await update.message.reply_html("<b>Recent releases</b>\n" + "\n".join(lines))

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_admin(update):
            return
        release = self.store.release(int(context.args[0])) if context.args and context.args[0].isdigit() else self.store.latest_release()
        if not release:
            await update.message.reply_text("No such release. See /releases.")
            return
        report = self.store.delivery_report(release["id"])
        icons = {"installed": "✅", "sent": "📨", "failed": "⚠️", "not sent": "⚪"}
        lines = [f"{icons[r['status']]} {esc(r['branch'] or r['full_name'])}: {r['status']}" for r in report]
        done = sum(1 for r in report if r["status"] == "installed")
        await update.message.reply_html(
            f"<b>{esc(release['app_name'])} v{esc(release['version'])}</b> (#{release['id']}, {release['status']})\n"
            f"Installed on {done} of {len(report)} tablets\n\n" + "\n".join(lines)
        )

    async def cmd_resend(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_admin(update):
            return
        release = self.store.release(int(context.args[0])) if context.args and context.args[0].isdigit() else None
        if not release or release["status"] != "approved":
            await update.message.reply_text("Usage: /resend <release id> (approved releases only, see /releases)")
            return
        pending_ids = {r["tablet_id"] for r in self.store.delivery_report(release["id"]) if r["status"] != "installed"}
        targets = [t for t in self.store.tablets("active") if t["id"] in pending_ids]
        if not targets:
            await update.message.reply_text("Every active tablet has already installed it.")
            return
        ok, failed = await self.deliver(context, release, targets)
        await update.message.reply_text(f"Re-sent to {ok} tablet(s); {len(failed)} failed. See /status {release['id']}.")

    async def cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_admin(update):
            return
        watcher = context.bot_data.get("drive")
        if not watcher:
            await update.message.reply_text("No Drive folder is set up. See deploy/drive-folder.md.")
            return
        try:
            found = await watcher.check_once(context.bot)
        except Exception as e:  # report Drive problems to the admin instead of failing silently
            log.exception("Manual Drive check failed")
            await update.message.reply_text(f"Could not read the Drive folder: {e}")
            return
        if not found:
            await update.message.reply_text("No new APK in the Drive folder.")

    async def cmd_backup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_admin(update):
            return
        path = self.store.backup(self.config.backup_dir)
        with path.open("rb") as f:
            await update.message.reply_document(f, filename=path.name, caption=f"{APP_NAME} v{VERSION} database backup")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_html(ADMIN_HELP if self.is_admin(update) else TABLET_HELP)

    async def _safe_send(self, context, chat_id: int, text: str) -> None:
        try:
            await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except TelegramError as e:
            log.warning("Could not message %s: %s", chat_id, e)


def build_application(config: Config, store: Store) -> Application:
    agent = Agent(config, store)
    app = Application.builder().token(config.bot_token).build()
    app.bot_data["agent"] = agent  # the web upload endpoint reaches the agent through here
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", agent.start, filters=private))
    app.add_handler(CommandHandler(["help", "about"], agent.cmd_help, filters=private))
    app.add_handler(CommandHandler("tablets", agent.cmd_tablets, filters=private))
    app.add_handler(CommandHandler("setbranch", agent.cmd_setbranch, filters=private))
    app.add_handler(CommandHandler("remove", agent.cmd_remove, filters=private))
    app.add_handler(CommandHandler("releases", agent.cmd_releases, filters=private))
    app.add_handler(CommandHandler("status", agent.cmd_status, filters=private))
    app.add_handler(CommandHandler("resend", agent.cmd_resend, filters=private))
    app.add_handler(CommandHandler("backup", agent.cmd_backup, filters=private))
    app.add_handler(CommandHandler("check", agent.cmd_check, filters=private))
    app.add_handler(MessageHandler(private & filters.Document.ALL, agent.on_document))
    app.add_handler(CallbackQueryHandler(agent.on_tablet_decision, pattern=r"^tab:(ok|no):\d+$"))
    app.add_handler(CallbackQueryHandler(agent.on_release_decision, pattern=r"^rel:(ok|no):\d+$"))
    app.add_handler(CallbackQueryHandler(agent.on_installed, pattern=r"^ins:\d+$"))
    return app
