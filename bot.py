import os
import re
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

BOT_TOKEN  = os.getenv("BOT_TOKEN")
OWNER_ID   = int(os.getenv("OWNER_ID", "0"))
PORT       = int(os.getenv("PORT", "10000"))
PUBLIC_URL = os.getenv("RENDER_EXTERNAL_URL", "")

MAX_CHARS  = 15000   # max input length
CHUNK_SIZE = 800     # per-TTS-call chunk size

VOICES = {
    "sreymom": ("km-KH-SreymomNeural", "ស្រីមុំ · Female"),
    "piseth":  ("km-KH-PisethNeural",  "ពិសិដ្ឋ · Male"),
}

prefs = {}
DEFAULTS = {"voice": "sreymom", "rate": 0, "pitch": 0}


def get_prefs(uid): return prefs.setdefault(uid, dict(DEFAULTS))
def is_owner(uid):  return uid == OWNER_ID
def rate_str(p):    return f"{'+' if p >= 0 else ''}{p}%"
def pitch_str(h):   return f"{'+' if h >= 0 else ''}{h}Hz"


# ─── audio pipeline ─────────────────────────────────────────

def split_text(text: str, max_len: int = CHUNK_SIZE):
    """Split text into sentence-ish chunks, each ≤ max_len chars."""
    parts = re.split(r'(?<=[។៕៚!?\.\n])\s*', text)
    chunks, cur = [], ""
    for p in parts:
        if not p.strip():
            continue
        if len(cur) + len(p) + 1 <= max_len:
            cur = (cur + " " + p).strip()
        else:
            if cur:
                chunks.append(cur)
            while len(p) > max_len:
                chunks.append(p[:max_len])
                p = p[max_len:]
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


async def synth_one(text: str, voice: str, rate: int, pitch: int, out_path: Path):
    c = edge_tts.Communicate(
        text, voice, rate=rate_str(rate), pitch=pitch_str(pitch)
    )
    with open(out_path, "wb") as f:
        async for chunk in c.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])


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
        raise RuntimeError("ffmpeg opus encode failed")


async def concat_mp3(mp3s: list[Path], out: Path, workdir: Path):
    listfile = workdir / "list.txt"
    with open(listfile, "w", encoding="utf-8") as f:
        for m in mp3s:
            f.write(f"file '{m.name}'\n")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(listfile), "-c", "copy", str(out),
        cwd=str(workdir),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg concat failed")


async def synthesize(text: str, voice: str, rate: int, pitch: int) -> bytes:
    """Full pipeline: split → per-chunk TTS → concat → opus. Returns OGG bytes."""
    chunks = split_text(text)
    if not chunks:
        raise RuntimeError("no chunks produced")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mp3s = []

        # sequential to stay gentle on the endpoint
        for i, ch in enumerate(chunks):
            mp3 = tmp / f"{i:04d}.mp3"
            await synth_one(ch, voice, rate, pitch, mp3)
            if mp3.stat().st_size == 0:
                raise RuntimeError(f"empty audio chunk {i}")
            mp3s.append(mp3)

        merged = tmp / "merged.mp3"
        if len(mp3s) == 1:
            merged = mp3s[0]
        else:
            await concat_mp3(mp3s, merged, tmp)

        ogg = tmp / "out.ogg"
        await mp3_to_ogg(merged, ogg)
        return ogg.read_bytes()


# ─── commands ───────────────────────────────────────────────

async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id):
        return
    await u.message.reply_text(
        "សូមស្វាគមន៍។ ផ្ញើអក្សរខ្មែរមក — ខ្ញុំឆ្លើយតបជាសម្លេង។\n\n"
        f"អតិបរមា {MAX_CHARS} តួអក្សរ/សារ។ អក្សរវែងបំបែកស្វ័យប្រវត្តិ។\n\n"
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
    kb = [[InlineKeyboardButton(
        rate_str(v) + (" ✓" if v == p["rate"] else ""),
        callback_data=f"r:{v}"
    ) for v in vals]]
    await u.message.reply_text(
        f"ល្បឿន: {rate_str(p['rate'])}", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_pitch(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    p = get_prefs(u.effective_user.id)
    vals = [-20, -10, 0, 10, 20]
    kb = [[InlineKeyboardButton(
        pitch_str(v) + (" ✓" if v == p["pitch"] else ""),
        callback_data=f"p:{v}"
    ) for v in vals]]
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
    if not text:
        return
    if len(text) > MAX_CHARS:
        await u.message.reply_text(
            f"អក្សរវែងពេក ({len(text)} តួអក្សរ)។ អតិបរមា {MAX_CHARS}។"
        )
        return

    p = get_prefs(u.effective_user.id)
    voice = VOICES[p["voice"]][0]

    await u.message.chat.send_action(ChatAction.RECORD_VOICE)

    try:
        audio = await synthesize(text, voice, p["rate"], p["pitch"])
    except Exception as e:
        await u.message.reply_text(f"បរាជ័យ: {e}")
        return

    await u.message.reply_voice(voice=audio)


# ─── entrypoint ─────────────────────────────────────────────

def main():
    if not BOT_TOKEN or not OWNER_ID:
        raise SystemExit("BOT_TOKEN or OWNER_ID missing")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("rate",  cmd_rate))
    app.add_handler(CommandHandler("pitch", cmd_pitch))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    if PUBLIC_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{PUBLIC_URL}/{BOT_TOKEN}",
            drop_pending_updates=True,
        )
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
