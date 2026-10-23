"""
main.py — Telegram Screenshot History Analyzer (NO OpenAI API key)

What it does:
- Accepts 2 screenshots from a Telegram user.
- Runs local OCR with Tesseract (no OpenAI/API key).
- Attempts to read visible Period IDs and result numbers.
- Shows historical frequency summaries, not guaranteed future predictions.

Python packages:
    pip install python-telegram-bot pillow pytesseract

System requirement:
    Tesseract OCR must also be installed on the hosting server.
    Ubuntu/Debian: sudo apt-get install tesseract-ocr
    (If the host does not permit installing Tesseract, this script's OCR won't work.)

Environment variable:
    TELEGRAM_BOT_TOKEN=your_telegram_bot_token

Run:
    python main.py
"""

import os
import re
import logging
from io import BytesIO
from collections import Counter

from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("screenshot-history-bot")

# Paste your BotFather token between the quotes below.
# Keep this file private. Never share it publicly after adding your token.
TOKEN = "PASTE_YOUR_BOTFATHER_TOKEN_HERE"
if not TOKEN or TOKEN == "PASTE_YOUR_BOTFATHER_TOKEN_HERE":
    raise RuntimeError(
        "main.py ফাইলে TOKEN = \"...\" এর মধ্যে BotFather token বসান।"
    )

# Common WinGo-style mapping shown in the user's screenshots.
# These labels are descriptive conventions and may differ between games.
NUMBER_COLORS = {
    0: ("Red", "Violet"),
    1: ("Green",),
    2: ("Red",),
    3: ("Green",),
    4: ("Red",),
    5: ("Green", "Violet"),
    6: ("Red",),
    7: ("Green",),
    8: ("Red",),
    9: ("Green",),
}


def image_to_text(raw: bytes) -> str:
    """OCR the screenshot locally. Requires the Tesseract system binary."""
    image = Image.open(BytesIO(raw)).convert("RGB")
    # Enlarge and improve contrast for small table text.
    image = ImageOps.autocontrast(image)
    image = image.resize((image.width * 2, image.height * 2))
    image = ImageEnhance.Contrast(image).enhance(1.5)
    image = image.filter(ImageFilter.SHARPEN)
    try:
        return pytesseract.image_to_string(
            image,
            config="--oem 3 --psm 6",
            lang="eng",
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Hosting server-এ Tesseract OCR ইনস্টল নেই। "
            "Ubuntu/Debian হলে: sudo apt-get install tesseract-ocr"
        ) from exc


def extract_results(text: str):
    """
    Find rows with a long period ID followed nearby by a single digit.
    OCR formats vary, so only accept likely rows and avoid duplicate periods.
    """
    found = {}
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    for i, line in enumerate(lines):
        period_match = re.search(r"\b(20\d{10,})\b", line)
        if not period_match:
            continue
        period = period_match.group(1)

        # Look at this line and a couple of following lines for a likely number.
        nearby = " ".join(lines[i:i + 3])
        # Remove period digits so they cannot be mistaken for game result.
        nearby_no_period = nearby.replace(period, " ")
        # The result often appears after the period in the same OCR row.
        candidates = re.findall(r"(?<!\d)([0-9])(?!\d)", nearby_no_period)
        if not candidates:
            continue

        # Prefer an isolated number after the period, but OCR ordering is imperfect.
        number = int(candidates[0])
        found[period] = number

    # Screenshots may overlap; unique period keys remove duplicates.
    return sorted(found.items(), key=lambda item: item[0], reverse=True)


def make_report(image1: bytes, image2: bytes) -> str:
    text1 = image_to_text(image1)
    text2 = image_to_text(image2)
    results = extract_results(text1 + "\n" + text2)

    if not results:
        return (
            "⚠️ ছবির History থেকে Period/Number নির্ভরযোগ্যভাবে পড়া যায়নি।\n\n"
            "আরও পরিষ্কার screenshot পাঠান—যেখানে Period ও Number-এর টেবিল দেখা যায়।\n"
            "OCR ভুল করতে পারে, তাই ফলাফল হাতে মিলিয়ে নেবেন।"
        )

    numbers = [number for _, number in results]
    number_counts = Counter(numbers)
    big_small = Counter("Big" if n >= 5 else "Small" for n in numbers)
    color_counts = Counter(
        color
        for n in numbers
        for color in NUMBER_COLORS.get(n, ())
    )

    latest_period, latest_number = results[0]
    display_rows = results[:10]
    rows_text = "\n".join(
        f"• `{period}` → **{number}**"
        for period, number in display_rows
    )

    number_summary = " · ".join(
        f"{n}: {number_counts.get(n, 0)}"
        for n in range(10)
    )
    common_numbers = sorted(
        number_counts.items(), key=lambda item: (-item[1], item[0])
    )[:3]
    common_text = ", ".join(
        f"{n} ({count} বার)" for n, count in common_numbers
    ) or "যথেষ্ট তথ্য নেই"

    color_text = (
        f"🟢 Green: {color_counts.get('Green', 0)}\n"
        f"🔴 Red: {color_counts.get('Red', 0)}\n"
        f"🟣 Violet: {color_counts.get('Violet', 0)}"
    )

    return (
        "📊 *Screenshot History Report*\n\n"
        f"🔢 পড়া গেছে: *{len(results)}টি unique Period*\n"
        f"🆕 সর্বশেষ শনাক্ত Period: `{latest_period}`\n"
        f"🎯 ওই Period-এর শনাক্ত Number: *{latest_number}*\n\n"
        "📋 *সাম্প্রতিক শনাক্ত ফলাফল*\n"
        f"{rows_text}\n\n"
        "🔟 *Number Frequency (0–9)*\n"
        f"`{number_summary}`\n\n"
        "🎨 *Number অনুযায়ী আনুমানিক Color Count*\n"
        f"{color_text}\n\n"
        "📏 *Big / Small Count* (0–4 Small, 5–9 Big)\n"
        f"🔵 Big: {big_small.get('Big', 0)}\n"
        f"⚪ Small: {big_small.get('Small', 0)}\n\n"
        f"📌 History-তে বেশি দেখা Number: *{common_text}*\n\n"
        "⚠️ *গুরুত্বপূর্ণ:* এটি শুধু OCR ও অতীত ফলাফলের frequency summary। "
        "এটি পরের Period-এর নির্ভরযোগ্য prediction নয়। Random ফলাফল স্বাধীন হতে পারে; "
        "আগে বেশি দেখা Number পরেও আসবে—এমন নিশ্চয়তা নেই। OCR ভুলও হতে পারে।\n\n"
        "🔄 নতুন বিশ্লেষণের জন্য /reset লিখুন."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["screenshots"] = []
    await update.message.reply_text(
        "👋 *Screenshot Analyzer Bot*\n\n"
        "📸 Game History দেখা যায় এমন ২টি screenshot পরপর পাঠান।\n"
        "আমি API key ছাড়াই hosting server-এর Tesseract OCR ব্যবহার করে "
        "Period/Number পড়ার চেষ্টা করব এবং frequency report দেব।\n\n"
        "⚠️ এটি নিশ্চিত prediction নয়। OCR ভুল হতে পারে; ফলাফল যাচাই করবেন।\n\n"
        "নতুন করে শুরু: /reset",
        parse_mode="Markdown",
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["screenshots"] = []
    await update.message.reply_text("✅ Reset হয়েছে। এখন নতুন ২টি screenshot পাঠান।")


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = context.user_data.setdefault("screenshots", [])
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    buf = BytesIO()
    await tg_file.download_to_memory(out=buf)
    photos.append(buf.getvalue())

    if len(photos) < 2:
        await update.message.reply_text("📷 Screenshot ১/২ পেয়েছি। এবার দ্বিতীয় screenshot পাঠান।")
        return

    pair = photos[:2]
    context.user_data["screenshots"] = []
    await update.message.reply_text("🔎 ছবির লেখা পড়ছি…")
    try:
        report = make_report(pair[0], pair[1])
        await update.message.reply_text(
            report[:4000],
            parse_mode="Markdown",
        )
    except RuntimeError as exc:
        log.exception("OCR setup issue")
        await update.message.reply_text(str(exc))
    except Exception:
        log.exception("Screenshot analysis failed")
        await update.message.reply_text(
            "দুঃখিত, ছবিগুলো বিশ্লেষণ করা যায়নি। "
            "পরিষ্কার screenshot দিন এবং hosting-এ Pillow ও Tesseract ইনস্টল আছে কি না দেখুন।"
        )


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.PHOTO, receive_photo))
    app.run_polling()


if __name__ == "__main__":
    main()
