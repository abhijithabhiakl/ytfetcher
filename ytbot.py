#!/usr/bin/env python3

import os
import subprocess
import logging
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest
import shutil

# ─────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_API_URL = os.getenv("BOT_API_URL")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
COOKIES_FILE = os.getenv("COOKIES_FILE")
MAX_HEIGHT = os.getenv("MAX_HEIGHT", "720")
PARALLEL = os.getenv("PARALLEL_DOWNLOADS", "4")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "1900"))
AUTO_CLEANUP = os.getenv("AUTO_CLEANUP", "true").lower() == "true"
LOG_DIR = os.getenv("LOG_DIR", "/home/hexcats/logs/tgbotlogs")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

ALLOWED_USERS = list(map(int, ALLOWED_USERS.split(","))) if ALLOWED_USERS else None

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"{LOG_DIR}/ytbot.log"),
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
# STATE
# ─────────────────────────────────────────────
user_links = {}
user_send_mode = {}   # "video" or "doc"

PYTHON = "/home/hexcats/configs/tgbots/tgenv/bin/python"

# ─────────────────────────────────────────────
# MESSAGE HANDLER
# ─────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return

    if "youtube.com" not in text and "youtu.be" not in text:
        await update.message.reply_text("❌ Send a valid YouTube URL")
        return

    user_links[user_id] = text

    await update.message.reply_text(
        "How should I send the file?\n\n"
        "/video → send as playable video\n"
        "/doc → send as document"
    )

# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────
async def cmd_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_send_mode[update.effective_user.id] = "video"
    await show_format_buttons(update)

async def cmd_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_send_mode[update.effective_user.id] = "doc"
    await show_format_buttons(update)

async def show_format_buttons(update: Update):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 MP4", callback_data="mp4"),
         InlineKeyboardButton("🎧 MP3", callback_data="mp3")],
        [InlineKeyboardButton("🎬 Best", callback_data="best")],
    ])
    await update.message.reply_text("Select format:", reply_markup=keyboard)

# ─────────────────────────────────────────────
# BUTTON HANDLER
# ─────────────────────────────────────────────
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    fmt = query.data

    url = user_links.get(user_id)
    send_mode = user_send_mode.get(user_id)

    if not url or not send_mode:
        await query.edit_message_text("❌ Send link again")
        return

    await query.edit_message_text("⬇️ Downloading...")

    if fmt == "mp4":
        ytdlp_format = f"bv*[ext=mp4][height<={MAX_HEIGHT}]+ba[ext=m4a]/mp4"
        extra = []
    elif fmt == "mp3":
        ytdlp_format = "bestaudio"
        extra = ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        ytdlp_format = "best"
        extra = []

    # Temporary folder per user request
    user_dir = DOWNLOAD_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        PYTHON, "-m", "yt_dlp",
        "-N", PARALLEL,
        "-f", ytdlp_format,
        "-o", str(user_dir / "%(title)s.%(ext)s"),
        *extra,
        url,
    ]
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        cmd += ["--cookies", COOKIES_FILE]

    try:
        subprocess.run(cmd, check=True)

        files = list(user_dir.glob("*"))
        files.sort(key=lambda f: f.stat().st_ctime)

        for file_path in files:
            size_mb = file_path.stat().st_size / (1024 * 1024)
            if size_mb > MAX_FILE_SIZE_MB:
                await query.message.reply_text(f"⚠️ Skipping {file_path.name}, file too large")
                continue

            with open(file_path, "rb") as f:
                if send_mode == "video" and file_path.suffix == ".mp4":
                    await query.message.reply_video(video=f, filename=file_path.name, supports_streaming=True)
                else:
                    await query.message.reply_document(document=f, filename=file_path.name)

            if AUTO_CLEANUP:
                file_path.unlink(missing_ok=True)

    except Exception:
        logging.exception("Download/send failed")
        await query.message.reply_text("❌ Failed")

    finally:
        user_links.pop(user_id, None)
        user_send_mode.pop(user_id, None)
        if AUTO_CLEANUP:
            shutil.rmtree(user_dir, ignore_errors=True)

# ─────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────
app.add_handler(CommandHandler("video", cmd_video))
app.add_handler(CommandHandler("doc", cmd_doc))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(CallbackQueryHandler(handle_button))

logging.info("ytbot started (video/doc + format + playlist support enabled)")
app.run_polling()
