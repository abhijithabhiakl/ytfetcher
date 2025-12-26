#!/usr/bin/env python3

import os
import asyncio
import signal
import logging
from pathlib import Path
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest

# ─────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_API_URL = os.getenv("BOT_API_URL")
BASE_DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
COOKIES_FILE = os.getenv("COOKIES_FILE")
PARALLEL = os.getenv("PARALLEL_DOWNLOADS", "4")
AUTO_CLEANUP = os.getenv("AUTO_CLEANUP", "true").lower() == "true"
LOG_DIR = Path(os.getenv("LOG_DIR", "/home/hexcats/logs/tgbotlogs"))

PYTHON = "/home/hexcats/configs/tgbots/tgenv/bin/python"

BASE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "ytbot.log"),
        logging.StreamHandler(),
    ],
)

# ─────────────────────────────────────────────
# TELEGRAM CLIENT
# ─────────────────────────────────────────────
request = HTTPXRequest(
    read_timeout=None,
    write_timeout=None,
    connect_timeout=30,
)

app = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .request(request)
    .base_url(BOT_API_URL)
    .build()
)

# ─────────────────────────────────────────────
# STATE (per user)
# ─────────────────────────────────────────────
user_links = {}
user_send_mode = {}
user_max_height = {}
user_delivery_mode = {}
running_tasks = {}

# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 *YouTube Downloader Bot*\n\n"
        "• Videos & playlists\n"
        "• MP4 / MP3 / Best\n"
        "• Save to server or send to Telegram\n"
        "• /cancel anytime\n\n"
        "Just send a YouTube link.",
        parse_mode="Markdown",
    )

# ─────────────────────────────────────────────
# /cancel
# ─────────────────────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    proc = running_tasks.get(user_id)

    if not proc:
        await update.message.reply_text("❌ No active download")
        return

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    running_tasks.pop(user_id, None)
    await update.message.reply_text("🛑 Download cancelled")

# ─────────────────────────────────────────────
# MESSAGE: LINK
# ─────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if "youtube.com" not in text and "youtu.be" not in text:
        await update.message.reply_text("❌ Send a valid YouTube link")
        return

    user_links[user_id] = text

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Send to Telegram", callback_data="deliver_send"),
            InlineKeyboardButton("💾 Save to Server", callback_data="deliver_save"),
        ]
    ])

    await update.message.reply_text(
        "What should I do after downloading?",
        reply_markup=keyboard,
    )

# ─────────────────────────────────────────────
# DELIVERY MODE
# ─────────────────────────────────────────────
async def handle_delivery_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_delivery_mode[user_id] = query.data.replace("deliver_", "")

    await query.edit_message_text(
        "How should I send the file?\n\n"
        "/video → playable video\n"
        "/doc → document"
    )

# ─────────────────────────────────────────────
# SEND MODE
# ─────────────────────────────────────────────
async def cmd_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_send_mode[update.effective_user.id] = "video"
    await ask_quality(update)

async def cmd_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_send_mode[update.effective_user.id] = "doc"
    await ask_quality(update)

async def ask_quality(update: Update):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("360p", callback_data="q_360"),
            InlineKeyboardButton("720p", callback_data="q_720"),
            InlineKeyboardButton("1080p", callback_data="q_1080"),
        ]
    ])
    await update.message.reply_text("Select max quality:", reply_markup=keyboard)

# ─────────────────────────────────────────────
# FORMAT BUTTONS
# ─────────────────────────────────────────────
async def show_format_buttons(query):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 MP4", callback_data="mp4"),
            InlineKeyboardButton("🎧 MP3", callback_data="mp3"),
        ],
        [InlineKeyboardButton("⭐ Best", callback_data="best")],
    ])
    await query.edit_message_text("Select format:", reply_markup=keyboard)

# ─────────────────────────────────────────────
# DOWNLOAD WORKER
# ─────────────────────────────────────────────
async def run_download(process, user_id, query, user_dir, send_mode, delivery):
    await process.wait()

    files = [f for f in user_dir.rglob("*") if f.is_file()]

    for file_path in files:
        logging.info("USER %s downloaded %s", user_id, file_path)

        if delivery == "send":
            with open(file_path, "rb") as f:
                if send_mode == "video" and file_path.suffix == ".mp4":
                    await query.message.reply_video(f, supports_streaming=True)
                else:
                    await query.message.reply_document(f)

        if AUTO_CLEANUP:
            file_path.unlink(missing_ok=True)

    if AUTO_CLEANUP:
        try:
            user_dir.rmdir()
        except OSError:
            pass

    running_tasks.pop(user_id, None)
    user_links.pop(user_id, None)
    user_send_mode.pop(user_id, None)
    user_delivery_mode.pop(user_id, None)
    user_max_height.pop(user_id, None)

# ─────────────────────────────────────────────
# BUTTON HANDLER
# ─────────────────────────────────────────────
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data.startswith("q_"):
        user_max_height[user_id] = query.data.split("_")[1]
        await show_format_buttons(query)
        return

    if user_id not in user_links:
        await query.edit_message_text("❌ Session expired. Send link again.")
        return

    url = user_links[user_id]
    send_mode = user_send_mode[user_id]
    delivery = user_delivery_mode[user_id]
    max_h = user_max_height[user_id]

    await query.edit_message_text("⬇️ Downloading...")

    user_dir = BASE_DOWNLOAD_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    if query.data == "mp4":
        ytdlp_format = f"bv*[ext=mp4][height<={max_h}]+ba[ext=m4a]/mp4"
        extra = []
    elif query.data == "mp3":
        ytdlp_format = "bestaudio"
        extra = ["--extract-audio", "--audio-format", "mp3"]
    else:
        ytdlp_format = "best"
        extra = []

    outtmpl = user_dir / "%(playlist_title)s/%(title)s.%(ext)s"

    cmd = [
        PYTHON, "-m", "yt_dlp",
        "-N", PARALLEL,
        "-f", ytdlp_format,
        "-o", str(outtmpl),
        "--yes-playlist",
        *extra,
        url,
    ]

    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        cmd += ["--cookies", COOKIES_FILE]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        start_new_session=True
    )

    running_tasks[user_id] = process

    asyncio.create_task(
        run_download(process, user_id, query, user_dir, send_mode, delivery)
    )

# ─────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("cancel", cmd_cancel))
app.add_handler(CommandHandler("video", cmd_video))
app.add_handler(CommandHandler("doc", cmd_doc))
app.add_handler(CallbackQueryHandler(handle_delivery_choice, pattern="^deliver_"))
app.add_handler(CallbackQueryHandler(handle_button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

logging.info("ytbot started — multi-user safe, cancellable")
app.run_polling()
