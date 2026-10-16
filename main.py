import asyncio
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import torch
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from TTS.api import TTS


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

BOT_TOKEN = "8634925900:AAH_lpHaM0nxd3IaUMAwaaxxjTZdzXqddlw"  # এখানে নিজের নতুন Telegram bot token বসাও
WORK_DIR = Path(os.getenv("WORK_DIR", "./data"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
XTTS_MODEL = os.getenv(
    "XTTS_MODEL",
    "tts_models/multilingual/multi-dataset/xtts_v2",
)
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "en").strip()

# Safety: target voices are only accepted after the user explicitly
# confirms that they have permission to clone/use that voice.
CONSENT_TEXT = (
    "I confirm that I have permission from the speaker to use this "
    "voice sample for voice conversion."
)

MAX_AUDIO_MB = int(os.getenv("MAX_AUDIO_MB", "25"))
MAX_AUDIO_SECONDS = int(os.getenv("MAX_AUDIO_SECONDS", "180"))

if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_NEW_BOT_TOKEN_HERE":
    raise RuntimeError("main.py-এর BOT_TOKEN লাইনে নিজের নতুন bot token বসাও।")

WORK_DIR.mkdir(parents=True, exist_ok=True)
TARGET_DIR = WORK_DIR / "targets"
MAIN_DIR = WORK_DIR / "main"
OUT_DIR = WORK_DIR / "output"
for d in (TARGET_DIR, MAIN_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("voice-clone-bot")

router = Router()

# In-memory state. For production, replace this with Redis/PostgreSQL.
sessions = {}
jobs = set()

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
log.info("Using device: %s", DEVICE)

# Load AI models once at startup.
log.info("Loading Whisper model: %s", WHISPER_MODEL)
whisper = WhisperModel(
    WHISPER_MODEL,
    device=DEVICE,
    compute_type="float16" if DEVICE == "cuda" else "int8",
)

log.info("Loading XTTS model: %s", XTTS_MODEL)
tts = TTS(XTTS_MODEL).to(DEVICE)
log.info("Models loaded.")


# ============================================================
# UI
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎯 Target Voice",
                    callback_data="target_voice",
                ),
                InlineKeyboardButton(
                    text="🎙 Main Voice",
                    callback_data="main_voice",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Convert",
                    callback_data="convert",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Clear",
                    callback_data="clear",
                ),
                InlineKeyboardButton(
                    text="ℹ️ Help",
                    callback_data="help",
                ),
            ],
        ]
    )


def consent_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ I have permission",
                    callback_data="consent_yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="consent_no",
                )
            ],
        ]
    )


def session(user_id: int):
    if user_id not in sessions:
        sessions[user_id] = {
            "target": None,
            "main": None,
            "language": DEFAULT_LANGUAGE,
            "waiting": None,
            "consent": False,
        }
    return sessions[user_id]


# ============================================================
# FILE / AUDIO HELPERS
# ============================================================

def safe_ext(name: str, fallback=".bin") -> str:
    ext = Path(name or "").suffix.lower()
    if not ext or len(ext) > 8:
        return fallback
    if not re.match(r"^\.[a-z0-9]+$", ext):
        return fallback
    return ext


async def download_telegram_file(
    bot: Bot,
    message: Message,
    destination: Path,
) -> Path:
    """
    Downloads voice/audio/document sent to Telegram.
    """
    file_id = None
    original_name = ""

    if message.voice:
        file_id = message.voice.file_id
        original_name = "telegram_voice.ogg"
    elif message.audio:
        file_id = message.audio.file_id
        original_name = message.audio.file_name or "telegram_audio.mp3"
    elif message.document:
        file_id = message.document.file_id
        original_name = message.document.file_name or "telegram_document.bin"
    else:
        raise ValueError("No supported audio attachment found.")

    if message.voice and message.voice.file_size:
        if message.voice.file_size > MAX_AUDIO_MB * 1024 * 1024:
            raise ValueError("Voice file is too large.")
    if message.audio and message.audio.file_size:
        if message.audio.file_size > MAX_AUDIO_MB * 1024 * 1024:
            raise ValueError("Audio file is too large.")
    if message.document and message.document.file_size:
        if message.document.file_size > MAX_AUDIO_MB * 1024 * 1024:
            raise ValueError("Document is too large.")

    tg_file = await bot.get_file(file_id)
    ext = safe_ext(original_name, ".bin")
    destination = destination.with_suffix(ext)
    await bot.download_file(tg_file.file_path, destination=destination)

    return destination


async def download_url(url: str, destination: Path) -> Path:
    """
    Downloads a direct audio URL with basic SSRF protections.
    Only http/https URLs are accepted. Private/local IPs are blocked
    by hostname checks; for a public production deployment, use a
    dedicated media proxy with stronger egress filtering.
    """
    url = url.strip()
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http:// and https:// links are supported.")

    hostname = (parsed.hostname or "").lower()
    blocked_hosts = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "metadata.google.internal",
        "169.254.169.254",
    }
    if hostname in blocked_hosts:
        raise ValueError("This URL is not allowed.")

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=30,
        limits=httpx.Limits(max_connections=4),
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()

            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_AUDIO_MB * 1024 * 1024:
                raise ValueError("Remote file is too large.")

            ext = safe_ext(Path(parsed.path).name, ".bin")
            destination = destination.with_suffix(ext)

            total = 0
            with destination.open("wb") as f:
                async for chunk in response.aiter_bytes(1024 * 64):
                    total += len(chunk)
                    if total > MAX_AUDIO_MB * 1024 * 1024:
                        raise ValueError("Remote file exceeded size limit.")
                    f.write(chunk)

    return destination


def ffmpeg_normalize(input_file: Path, output_file: Path):
    """
    Converts any supported input into mono 24k WAV.
    Requires system ffmpeg.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_file),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "24000",
        "-c:a",
        "pcm_s16le",
        str(output_file),
    ]
    subprocess.run(cmd, check=True)


def ffmpeg_to_telegram_voice(input_wav: Path, output_ogg: Path):
    """
    Telegram-native voice message: OGG/Opus.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_wav),
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        "48k",
        "-vbr",
        "on",
        "-application",
        "voip",
        str(output_ogg),
    ]
    subprocess.run(cmd, check=True)


def audio_duration_seconds(path: Path) -> float:
    import wave

    # WAV is produced by ffmpeg_normalize.
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate or 1)


def transcribe(path: Path):
    segments, info = whisper.transcribe(
        str(path),
        beam_size=5,
        vad_filter=True,
    )
    text_parts = []
    for segment in segments:
        value = segment.text.strip()
        if value:
            text_parts.append(value)

    text = " ".join(text_parts).strip()
    detected = (info.language or DEFAULT_LANGUAGE).lower()

    return text, detected


def choose_xtts_language(detected: str) -> str:
    """
    XTTS-v2 supports a fixed set of languages. Bengali is not among
    the XTTS-v2 language list, so fail clearly instead of silently
    generating the wrong language.
    """
    aliases = {
        "zh": "zh-cn",
        "zh-cn": "zh-cn",
    }
    detected = aliases.get(detected, detected)

    supported = {
        "en", "es", "fr", "de", "it", "pt", "pl", "tr",
        "ru", "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko",
    }

    if detected not in supported:
        raise ValueError(
            f"Detected language '{detected}' is not supported by "
            "XTTS-v2. Set another supported language only if the "
            "actual spoken language matches it."
        )

    return detected


async def save_input(
    bot: Bot,
    message: Message,
    folder: Path,
    prefix: str,
) -> Path:
    uid = uuid.uuid4().hex
    raw = folder / f"{prefix}_{uid}_raw"
    normalized = folder / f"{prefix}_{uid}.wav"

    if message.voice or message.audio or message.document:
        downloaded = await download_telegram_file(bot, message, raw)
    else:
        raise ValueError("Send a voice message, audio file, or document.")

    try:
        ffmpeg_normalize(downloaded, normalized)
    finally:
        downloaded.unlink(missing_ok=True)

    duration = audio_duration_seconds(normalized)
    if duration > MAX_AUDIO_SECONDS:
        normalized.unlink(missing_ok=True)
        raise ValueError(
            f"Audio is too long. Maximum allowed is {MAX_AUDIO_SECONDS} seconds."
        )

    return normalized


# ============================================================
# BOT COMMANDS
# ============================================================

@router.message(CommandStart())
async def start(message: Message):
    session(message.from_user.id)
    await message.answer(
        "🎙 Voice Clone Bot\n\n"
        "এই bot শুধু সেই voice-এর জন্য ব্যবহার করো যার মালিকের "
        "স্পষ্ট অনুমতি আছে।\n\n"
        "1️⃣ Target Voice সেট করো\n"
        "2️⃣ Main Voice পাঠাও\n"
        "3️⃣ Convert চাপো\n\n"
        "Target voice সেট করার আগে permission confirmation লাগবে।",
        reply_markup=main_keyboard(),
    )


@router.message(Command("language"))
async def language_cmd(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "Usage: /language en\n\n"
            "XTTS-v2 supported examples: en, es, fr, de, it, pt, "
            "pl, tr, ru, nl, cs, ar, zh-cn, ja, hu, ko"
        )
        return

    lang = parts[1].strip().lower()
    supported = {
        "en", "es", "fr", "de", "it", "pt", "pl", "tr",
        "ru", "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko",
    }
    if lang not in supported:
        await message.answer("এই language XTTS-v2-তে supported নয়.")
        return

    session(message.from_user.id)["language"] = lang
    await message.answer(f"✅ Language set to: {lang}")


@router.callback_query(F.data == "help")
async def help_cb(call: CallbackQuery):
    await call.answer()
    await call.message.answer(
        "ℹ️ How it works\n\n"
        "🎯 Target Voice — যার অনুমোদিত voice sample ব্যবহার করবে\n"
        "🎙 Main Voice — যে কথা target voice-এ তৈরি হবে\n"
        "🔄 Convert — প্রথমে Main Voice transcribe, তারপর Target Voice-এ synthesize\n\n"
        "⚠️ 100% identical clone guarantee করা যায় না।\n"
        "⚠️ XTTS-v2 সব language support করে না।\n"
        "⚠️ Target speaker-এর permission ছাড়া voice cloning করো না."
    )


@router.callback_query(F.data == "clear")
async def clear_cb(call: CallbackQuery):
    await call.answer("Cleared")
    s = session(call.from_user.id)

    for key in ("target", "main"):
        p = s.get(key)
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
        s[key] = None

    s["waiting"] = None
    s["consent"] = False
    await call.message.answer("🗑 Target/Main voice cleared.", reply_markup=main_keyboard())


@router.callback_query(F.data == "target_voice")
async def target_cb(call: CallbackQuery):
    await call.answer()
    session(call.from_user.id)["waiting"] = "target"
    await call.message.answer(
        "🎯 Target Voice\n\n"
        "প্রথমে permission confirm করো। তারপর voice/audio পাঠাও "
        "অথবা একটি direct audio URL পাঠাও.",
        reply_markup=consent_keyboard(),
    )


@router.callback_query(F.data == "consent_yes")
async def consent_yes(call: CallbackQuery):
    await call.answer("Permission confirmed")
    s = session(call.from_user.id)
    s["consent"] = True
    s["waiting"] = "target"

    await call.message.answer(
        f"✅ {CONSENT_TEXT}\n\n"
        "এখন Target Voice হিসেবে voice/audio পাঠাও, অথবা direct audio URL পাঠাও."
    )


@router.callback_query(F.data == "consent_no")
async def consent_no(call: CallbackQuery):
    await call.answer("Cancelled")
    s = session(call.from_user.id)
    s["consent"] = False
    s["waiting"] = None
    await call.message.answer("❌ Target Voice setup cancelled.", reply_markup=main_keyboard())


@router.callback_query(F.data == "main_voice")
async def main_cb(call: CallbackQuery):
    await call.answer()
    s = session(call.from_user.id)
    s["waiting"] = "main"
    await call.message.answer(
        "🎙 Main Voice\n\n"
        "যে কথাগুলো target voice-এ convert করতে চাও, "
        "সেই voice message/audio পাঠাও অথবা direct audio URL পাঠাও."
    )


@router.callback_query(F.data == "convert")
async def convert_cb(call: CallbackQuery, bot: Bot):
    await call.answer()

    s = session(call.from_user.id)
    if not s.get("consent"):
        await call.message.answer(
            "❌ আগে Target Voice-এর permission confirmation দিতে হবে."
        )
        return

    if not s.get("target"):
        await call.message.answer("❌ আগে Target Voice সেট করো.")
        return

    if not s.get("main"):
        await call.message.answer("❌ আগে Main Voice সেট করো.")
        return

    if call.from_user.id in jobs:
        await call.message.answer("⏳ তোমার আগের conversion এখনো চলছে.")
        return

    jobs.add(call.from_user.id)

    status = await call.message.answer("🔄 Processing...")

    try:
        main_wav = Path(s["main"])
        target_wav = Path(s["target"])

        await status.edit_text("🔄 1/3 Main Voice transcribing...")

        text, detected_language = await asyncio.to_thread(
            transcribe,
            main_wav,
        )

        if not text:
            raise ValueError("Main Voice থেকে usable speech পাওয়া যায়নি.")

        # Respect manually selected language if user set one.
        language = s.get("language") or detected_language
        language = choose_xtts_language(language)

        await status.edit_text(
            "🔄 2/3 Target Voice synthesis হচ্ছে...\n\n"
            f"Detected/selected language: {language}"
        )

        output_id = uuid.uuid4().hex
        output_wav = OUT_DIR / f"{call.from_user.id}_{output_id}.wav"
        output_ogg = OUT_DIR / f"{call.from_user.id}_{output_id}.ogg"

        await asyncio.to_thread(
            tts.tts_to_file,
            text=text,
            speaker_wav=str(target_wav),
            language=language,
            file_path=str(output_wav),
            split_sentences=True,
        )

        await status.edit_text("🔄 3/3 Telegram voice তৈরি হচ্ছে...")

        await asyncio.to_thread(
            ffmpeg_to_telegram_voice,
            output_wav,
            output_ogg,
        )

        await status.delete()

        # Send a fresh Telegram voice message, not a Telegram "forward".
        # This avoids carrying source-message metadata.
        await bot.send_voice(
            chat_id=call.message.chat.id,
            voice=FSInputFile(output_ogg),
        )

        await bot.send_document(
            chat_id=call.message.chat.id,
            document=FSInputFile(output_ogg),
            caption=(
                "⬇️ Downloadable OGG/Opus\n"
                "Generated by the bot. Use only with permission."
            ),
        )

        output_wav.unlink(missing_ok=True)
        output_ogg.unlink(missing_ok=True)

    except Exception as exc:
        log.exception("Conversion failed")
        try:
            await status.edit_text(
                "❌ Conversion failed\n\n"
                f"{str(exc)[:1200]}"
            )
        except Exception:
            pass
    finally:
        jobs.discard(call.from_user.id)


# ============================================================
# AUDIO / URL INPUT
# ============================================================

async def handle_url(message: Message, bot: Bot):
    s = session(message.from_user.id)
    waiting = s.get("waiting")

    if waiting not in ("target", "main"):
        return False

    if waiting == "target" and not s.get("consent"):
        await message.answer("❌ আগে permission confirmation দিতে হবে.")
        return True

    url = message.text.strip()

    folder = TARGET_DIR if waiting == "target" else MAIN_DIR
    prefix = "target" if waiting == "target" else "main"

    msg = await message.answer("⬇️ Downloading audio link...")

    try:
        uid = uuid.uuid4().hex
        raw = folder / f"{prefix}_{uid}_url"
        normalized = folder / f"{prefix}_{uid}.wav"

        downloaded = await download_url(url, raw)
        try:
            await asyncio.to_thread(ffmpeg_normalize, downloaded, normalized)
        finally:
            downloaded.unlink(missing_ok=True)

        duration = audio_duration_seconds(normalized)
        if duration > MAX_AUDIO_SECONDS:
            normalized.unlink(missing_ok=True)
            raise ValueError(
                f"Audio is too long. Maximum is {MAX_AUDIO_SECONDS} seconds."
            )

        old = s.get(waiting)
        if old:
            Path(old).unlink(missing_ok=True)

        s[waiting] = str(normalized)
        s["waiting"] = None

        await msg.edit_text(
            f"✅ {'Target' if waiting == 'target' else 'Main'} Voice saved.\n\n"
            "এখন " + ("Main Voice সেট করো এবং Convert চাপো." if waiting == "target"
                        else "Convert চাপো.")
        )

    except Exception as exc:
        await msg.edit_text(f"❌ Link processing failed:\n{str(exc)[:1000]}")

    return True


@router.message(F.text)
async def text_handler(message: Message, bot: Bot):
    text = message.text.strip()

    # URL input
    if re.match(r"^https?://", text, re.I):
        handled = await handle_url(message, bot)
        if handled:
            return

    if text.startswith("/"):
        return

    s = session(message.from_user.id)
    waiting = s.get("waiting")

    if waiting in ("target", "main"):
        await message.answer(
            "🎙 Voice/audio পাঠাও অথবা direct audio URL দাও."
        )


@router.message(F.voice | F.audio | F.document)
async def audio_handler(message: Message, bot: Bot):
    s = session(message.from_user.id)
    waiting = s.get("waiting")

    if waiting not in ("target", "main"):
        await message.answer(
            "আগে 🎯 Target Voice অথবা 🎙 Main Voice button চাপো.",
            reply_markup=main_keyboard(),
        )
        return

    if waiting == "target" and not s.get("consent"):
        await message.answer(
            "❌ Target Voice ব্যবহার করার আগে permission confirmation প্রয়োজন."
        )
        return

    label = "Target" if waiting == "target" else "Main"
    folder = TARGET_DIR if waiting == "target" else MAIN_DIR

    status = await message.answer(f"⬇️ Saving {label} Voice...")

    try:
        path = await save_input(
            bot,
            message,
            folder,
            "target" if waiting == "target" else "main",
        )

        old = s.get(waiting)
        if old:
            Path(old).unlink(missing_ok=True)

        s[waiting] = str(path)
        s["waiting"] = None

        await status.edit_text(
            f"✅ {label} Voice saved.\n\n"
            + (
                "এখন 🎙 Main Voice সেট করে 🔄 Convert চাপো."
                if waiting == "target"
                else "এখন 🔄 Convert চাপো."
            )
        )

    except Exception as exc:
        await status.edit_text(f"❌ Could not process audio:\n{str(exc)[:1000]}")


# ============================================================
# CLEANUP
# ============================================================

async def cleanup_loop():
    while True:
        try:
            # Delete files older than 1 hour.
            import time

            now = time.time()
            for folder in (TARGET_DIR, MAIN_DIR, OUT_DIR):
                for path in folder.glob("*"):
                    try:
                        if now - path.stat().st_mtime > 3600:
                            path.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            log.exception("Cleanup failed")

        await asyncio.sleep(600)


# ============================================================
# MAIN
# ============================================================

async def main():
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    cleanup_task = asyncio.create_task(cleanup_loop())

    try:
        log.info("Bot starting...")
        await dp.start_polling(bot)
    finally:
        cleanup_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
