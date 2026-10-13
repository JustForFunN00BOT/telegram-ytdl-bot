import os
import re
import shutil
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)
import yt_dlp

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT-YOUR-TOKEN-HERE")
DOWNLOAD_DIR = Path("./downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

AUTO_DELETE_SECONDS = 600
TG_VIDEO_LIMIT = 50 * 1024 * 1024
RATE_LIMIT = 5
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot")

URL_RE = re.compile(r"^https?://", re.I)


def detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "instagram.com" in u:                    return "instagram"
    if "tiktok.com" in u:                       return "tiktok"
    if "twitter.com" in u or "x.com" in u:      return "twitter"
    if "facebook.com" in u or "fb.watch" in u:  return "facebook"
    return "unknown"


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


_last_req: dict[int, datetime] = {}


def rate_ok(user_id: int) -> bool:
    now = datetime.now()
    last = _last_req.get(user_id)
    if last and (now - last).total_seconds() < RATE_LIMIT:
        return False
    _last_req[user_id] = now
    return True


def cleanup(path: Path):
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        log.warning("cleanup failed: %s", e)


def cookies_arg() -> dict:
    if COOKIES_FILE and Path(COOKIES_FILE).is_file():
        return {"cookiefile": COOKIES_FILE}
    return {}


def ydl_opts(outtmpl: str, fmt: str, audio_only: bool = False) -> dict:
    opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        **cookies_arg(),
    }
    if audio_only:
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }],
        })
    else:
        opts["format"] = fmt
    return opts


async def fetch_info(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, **cookies_arg()}
    def _go():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    return await asyncio.get_event_loop().run_in_executor(None, _go)


async def do_download(url: str, fmt: str, audio_only: bool, outtmpl: str) -> Path:
    opts = ydl_opts(outtmpl, fmt, audio_only)
    def _go():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return Path(ydl.prepare_filename(info))
    return await asyncio.get_event_loop().run_in_executor(None, _go)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 **ربات دانلودر**\n\n"
        "لینک YouTube / Instagram / TikTok / Twitter / Facebook بفرست.",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 **راهنما**\n\n"
        "1️⃣ لینک بفرست\n2️⃣ کیفیت انتخاب کن\n3️⃣ صبر کن",
        parse_mode="Markdown",
    )


async def on_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not URL_RE.match(url):
        return
    user_id = update.effective_user.id
    if not rate_ok(user_id):
        await update.message.reply_text("⏳ یکم صبر کن.")
        return
    platform = detect_platform(url)
    if platform == "unknown":
        await update.message.reply_text("❌ این لینک پشتیبانی نمی‌شه.")
        return
    msg = await update.message.reply_text("🔎 در حال بررسی...")
    try:
        info = await fetch_info(url)
    except Exception as e:
        log.exception("fetch_info failed")
        await msg.edit_text(f"❌ خطا: `{e}`", parse_mode="Markdown")
        return
    title = info.get("title", "video")
    duration = info.get("duration", 0) or 0
    if duration and duration > 1800:
        await msg.edit_text("⚠️ بیشتر از ۳۰ دقیقه‌ست.")
        return
    formats = info.get("formats", [])
    videos = sorted(
        [f for f in formats if f.get("vcodec") != "none" and f.get("height")],
        key=lambda f: f.get("height", 0), reverse=True,
    )
    heights = []
    for f in videos:
        h = f.get("height")
        if h and h not in heights and h <= 1080:
            heights.append(h)
        if len(heights) >= 4:
            break
    ctx.user_data["url"] = url
    ctx.user_data["title"] = title
    keyboard = []
    for h in heights:
        keyboard.append([InlineKeyboardButton(f"🎬 {h}p", callback_data=f"v|{h}")])
    keyboard.append([InlineKeyboardButton("🎵 MP3 (320kbps)", callback_data="a|mp3")])
    keyboard.append([InlineKeyboardButton("❌ لغو", callback_data="x|cancel")])
    await msg.edit_text(
        f"📹 **{title}**\n🌐 `{platform}`\n⏱ {duration // 60}:{duration % 60:02d}\n\nکیفیت رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data.startswith("x|"):
        await q.edit_message_text("❌ لغو شد.")
        return
    url = ctx.user_data.get("url")
    if not url:
        await q.edit_message_text("❌ منقضی شد، دوباره لینک بفرست.")
        return
    await q.edit_message_text("⬇️ در حال دانلود...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outtmpl = str(DOWNLOAD_DIR / f"{timestamp}_%(title).50B.%(ext)s")
    try:
        if data.startswith("a|"):
            path = await do_download(url, "", True, outtmpl)
            path = path.with_suffix(".mp3")
        else:
            _, h = data.split("|")
            path = await do_download(url, f"bestvideo[height<={h}]+bestaudio/best[height<={h}]", False, outtmpl)
    except Exception as e:
        log.exception("download failed")
        await q.edit_message_text(f"❌ خطا در دانلود: `{e}`", parse_mode="Markdown")
        return
    if not path or not path.exists():
        await q.edit_message_text("❌ فایل ساخته نشد.")
        return
    size = path.stat().st_size
    caption = f"📥 {path.stem}\n💾 {human_size(size)}"
    try:
        if data.startswith("a|"):
            await q.message.reply_audio(audio=open(path, "rb"), title=path.stem, caption=caption)
        elif size <= TG_VIDEO_LIMIT:
            await q.message.reply_video(video=open(path, "rb"), caption=caption, supports_streaming=True)
        else:
            await q.message.reply_document(document=open(path, "rb"), caption=caption)
        await q.edit_message_text("✅ ارسال شد!")
        if AUTO_DELETE_SECONDS > 0:
            chat_id = q.message.chat_id
            mid = q.message.message_id
            async def _cleanup_later():
                await asyncio.sleep(AUTO_DELETE_SECONDS)
                cleanup(path)
                try:
                    await ctx.bot.delete_message(chat_id, mid)
                except Exception:
                    pass
            ctx.application.create_task(_cleanup_later())
    except Exception as e:
        log.exception("send failed")
        await q.edit_message_text(f"❌ خطا در ارسال: `{e}`", parse_mode="Markdown")
        cleanup(path)


async def main():
    if BOT_TOKEN == "PUT-YOUR-TOKEN-HERE":
        raise RuntimeError("BOT_TOKEN رو تنظیم کن.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_url))
    app.add_handler(CallbackQueryHandler(on_callback))
    log.info("Bot started")
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped")
