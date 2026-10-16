import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile
from aiogram.enums import ChatAction

# Security: do not paste an exposed token here.
# Set BOT_TOKEN as an environment variable using a NEW token from @BotFather.
BOT_TOKEN = os.getenv("BOT_TOKEN", "8843354006:AAHRCiMELS7nMW1WxAooRopbIDjEoInuGu4")

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def safe_name(name: str) -> str:
    name = Path(name or "audio_file").name
    return "".join(c for c in name if c.isalnum() or c in "._- ")[:100] or "audio_file"


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "🎧 Voice/Audio Download Bot\n\n"
        "Channel post থেকে voice/audio forward করুন, অথবা audio/voice file পাঠান।\n"
        "আমি সেটি file হিসেবে download করে আপনাকে ফেরত দেব।\n\n"
        "⚠️ শুধু link দিলে সব channel post সরাসরি download করা যায় না। "
        "সেক্ষেত্রে post-টি bot-এ forward করুন।"
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await start(message)


@dp.message(F.voice)
async def handle_voice(message: Message):
    await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_DOCUMENT)
    file = await bot.get_file(message.voice.file_id)
    path = DOWNLOAD_DIR / f"voice_{message.message_id}.ogg"
    await bot.download_file(file.file_path, destination=path)
    try:
        await message.answer_document(
            FSInputFile(path, filename=path.name),
            caption="✅ Voice downloaded"
        )
    finally:
        path.unlink(missing_ok=True)


@dp.message(F.audio)
async def handle_audio(message: Message):
    audio = message.audio
    filename = safe_name(audio.file_name or f"audio_{message.message_id}.mp3")
    path = DOWNLOAD_DIR / filename
    file = await bot.get_file(audio.file_id)
    await bot.download_file(file.file_path, destination=path)
    try:
        await message.answer_document(
            FSInputFile(path, filename=filename),
            caption="✅ Audio downloaded"
        )
    finally:
        path.unlink(missing_ok=True)


@dp.message(F.document)
async def handle_document(message: Message):
    doc = message.document
    mime = (doc.mime_type or "").lower()
    filename = safe_name(doc.file_name or f"file_{message.message_id}")
    if not (mime.startswith("audio/") or filename.lower().endswith(
        (".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".flac", ".aac")
    )):
        await message.answer("এটি audio file মনে হচ্ছে না। Voice বা audio file পাঠান।")
        return

    path = DOWNLOAD_DIR / filename
    file = await bot.get_file(doc.file_id)
    await bot.download_file(file.file_path, destination=path)
    try:
        await message.answer_document(
            FSInputFile(path, filename=filename),
            caption="✅ Audio downloaded"
        )
    finally:
        path.unlink(missing_ok=True)


@dp.message(F.text)
async def handle_text(message: Message):
    text = (message.text or "").strip()
    if "t.me/" in text or "telegram.me/" in text:
        await message.answer(
            "🔗 Link পেয়েছি। শুধু link দিয়ে সব channel post আনা যায় না।\n"
            "Channel post খুলে Forward → এই bot করুন; আমি voice/audio file ফেরত দেব।"
        )
    else:
        await message.answer("Voice/audio পাঠান বা channel post-টি এখানে forward করুন।")


async def main():
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_NEW_BOT_TOKEN_HERE":
        raise RuntimeError(
            "Set BOT_TOKEN environment variable with a NEW token from @BotFather."
        )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
