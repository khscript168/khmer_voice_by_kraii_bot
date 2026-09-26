import os
import asyncio
import tempfile
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
import edge_tts

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID  = int(os.getenv("OWNER_ID", "0"))
PORT      = int(os.getenv("PORT", "10000"))
PUBLIC_URL = os.getenv("RENDER_EXTERNAL_URL", "")

VOICES = {
    "sreymom": ("km-KH-SreymomNeural", "ស្រីមុំ · Female"),
    "piseth":  ("km-KH-PisethNeural",  "ពិសិដ្ឋ · Male"),
}

prefs = {}
DEFAULTS = {"voice": "sreymom", "rate": 0, "pitch": 0}


def get_prefs(uid): return prefs.setdefault(uid, dict(DEFAULTS))
def is_owner(uid): return uid == OWNER_ID
def rate_str(p):  return f"{'+' if p >= 0 else ''}{p}%"
def pitch_str(h): return f"{'+' if h >= 0 else ''}{h}Hz"


async def mp3_to_ogg(mp3: Path, ogg: Path):
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(mp3),
        "-c:a", "libopus", "-b:a", "48k", "-ar", "48000",
        "-ac", "1", "-application", "voip", str(ogg),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed")


async def synthesize(text, voice, rate, pitch):
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = Path(tmp) / "a.mp3"
        ogg = Path(tmp) / "a.ogg"
        c = edge_tts.Communicate(text, voice, rate=rate_str(rate), pitch=pitch_str(pitch))
        with open(mp3, "wb") as f:
            async for chunk in c.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
        if mp3.stat().st_size == 0:
            raise RuntimeError("empty audio")
        await mp3_to_ogg(mp3, ogg)
        return ogg.read_bytes()


async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    await u.message.reply_text(
        "សូមស្វាគមន៍។ ផ្ញើអក្សរខ្មែរមក — ខ្ញុំឆ្លើយតបជាសម្លេង។\n\n"
        "/voice — ជ្រើសសម្លេង\n"
        "/rate — ល្បឿន\n"
        "/pitch — សំនៀង\n"
        "/reset — ត្រឡប់លំនាំដើម"
    )


async def cmd_voice(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    kb = [[InlineKeyboardButton(lbl, callback_data=f"v:{k}")]
          for k, (_, lbl) in VOICES.items()]
    await u.message.reply_text("ជ្រើសសម្លេង:", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_rate(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    p = get_prefs(u.effective_user.id)
    vals = [-30, -15, 0, 15, 30]
    kb = [[InlineKeyboardButton(rate_str(v) + (" ✓" if v == p["rate"] else ""),
           callback_data=f"r:{v}") for v in vals]]
    await u.message.reply_text(
        f"ល្បឿន: {rate_str(p['rate'])}", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_pitch(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    p = get_prefs(u.effective_user.id)
    vals = [-20, -10, 0, 10, 20]
    kb = [[InlineKeyboardButton(pitch_str(v) + (" ✓" if v == p["pitch"] else ""),
           callback_data=f"p:{v}") for v in vals]]
    await u.message.reply_text(
        f"សំនៀង: {pitch_str(p['pitch'])}", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_reset(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    prefs[u.effective_user.id] = dict(DEFAULTS)
    await u.message.reply_text("ត្រឡប់ទៅលំនាំដើមហើយ។")


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    if not is_owner(q.from_user.id):
        await q.answer(); return
    await q.answer()
    p = get_prefs(q.from_user.id)
    k, _, v = q.data.partition(":")
    if k == "v":
        p["voice"] = v
        await q.edit_message_text(f"សម្លេង: {VOICES[v][1]}")
    elif k == "r":
        p["rate"] = int(v)
        await q.edit_message_text(f"ល្បឿន: {rate_str(p['rate'])}")
    elif k == "p":
        p["pitch"] = int(v)
        await q.edit_message_text(f"សំនៀង: {pitch_str(p['pitch'])}")


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    text = (u.message.text or "").strip()
    if not text: return
    if len(text) > 2000:
        await u.message.reply_text("អក្សរវែងពេក — បំបែកជាកំណាត់តូចៗ។")
        return
    p = get_prefs(u.effective_user.id)
    voice = VOICES[p["voice"]][0]
    await u.message.chat.send_action(ChatAction.RECORD_VOICE)
    try:
        audio = await synthesize(text, voice, p["rate"], p["pitch"])
    except Exception as e:
        await u.message.reply_text(f"បរាជ័យ: {e}")
        return
    await u.message.reply_voice(voice=audio, caption=text[:100])


def main():
    if not BOT_TOKEN or not OWNER_ID:
        raise SystemExit("BOT_TOKEN or OWNER_ID missing")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("rate", cmd_rate))
    app.add_handler(CommandHandler("pitch", cmd_pitch))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    if PUBLIC_URL:
        # Webhook mode (production on Render)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{PUBLIC_URL}/{BOT_TOKEN}",
            drop_pending_updates=True,
        )
    else:
        # Polling mode (local dev)
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
