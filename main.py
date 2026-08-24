import asyncio
import random
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = "8343790496:AAEh8SEmaLC-DYJ5A_ZIM1WjsHb2-lz2F0w"
ADMIN_ID = 5747820322

is_running = False

def replace_mask(text: str) -> str:
    def random_digits(match):
        return "".join([str(random.randint(0, 9)) for _ in match.group(0)])
    return re.sub(r'❌+', random_digits, text)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Admin Control Panel Active!")

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running
    if update.effective_user.id != ADMIN_ID:
        return
    is_running = False
    await update.message.reply_text("🛑 OTP Generation Stopped!")

async def handle_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running
    if update.effective_user.id != ADMIN_ID:
        return

    text = update.message.text.strip()
    parts = text.split()

    if len(parts) < 4:
        return

    delay_seconds = 1.0
    time_part = parts[-1].lower()
    
    if time_part.endswith("s"):
        try:
            delay_seconds = float(time_part[:-1])
            parts.pop()
        except ValueError:
            pass
    elif time_part.endswith("m"):
        try:
            delay_seconds = float(time_part[:-1]) * 60
            parts.pop()
        except ValueError:
            pass

    count_part = parts[-1].lower()
    if not count_part.endswith("cd"):
        return

    try:
        total_count = int(count_part.replace("cd", ""))
        parts.pop()
    except ValueError:
        return

    service_logo = "🟢"
    full_pattern = " ".join(parts)
    
    if " WS " in f" {full_pattern} " or " ws " in f" {full_pattern} ":
        service_logo = "💬"
    elif " TG " in f" {full_pattern} " or " tg " in f" {full_pattern} ":
        service_logo = "✈️"

    is_running = True
    await update.message.reply_text(f"🚀 Broadcast Started! Interval: {delay_seconds}s")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Copy Your Key", callback_data="copy_key")],
        [
            InlineKeyboardButton("🤖 Get Number 2", url="https://t.me/YOUR_GET_NUMBER_LINK"),
            InlineKeyboardButton("📢 Support GP", url="https://t.me/YOUR_DEVELOPER_LINK")
        ]
    ])

    for i in range(total_count):
        if not is_running:
            break

        formatted_line = replace_mask(full_pattern)
        if len(formatted_line) > 4:
            random_last_4 = "".join([str(random.randint(0, 9)) for _ in range(4)])
            formatted_line = formatted_line[:-4] + random_last_4

        otp_code = random.randint(100000, 999999)

        message_body = (
            f"{formatted_line} {service_logo}\n"
            f"🔑 **Code:** `{otp_code}`"
        )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message_body,
            parse_mode="Markdown",
            reply_markup=keyboard
        )

        await asyncio.sleep(delay_seconds)

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_admin_command))
    app.run_polling()