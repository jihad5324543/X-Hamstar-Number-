import asyncio
import random
import re
import os
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import RetryAfter

# --- UptimeRobot keep alive ---
web_app = Flask('')

@web_app.route('/')
def home():
    return "Bot is alive!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# --- Bot Configuration ---
BOT_TOKEN = "8343790496:AAEh8SEmaLC-DYJ5A_ZIM1WjsHb2-lz2F0w"
ADMIN_ID = 5747820322  # আপনার আসল টেলিগ্রাম আইডি দিন

is_running = False

def replace_mask(text: str) -> str:
    def random_digits(match):
        return "".join([str(random.randint(0, 9)) for _ in match.group(0)])
    return re.sub(r'❌+', random_digits, text)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ **SECRET OTP BOT Active!**", parse_mode="Markdown")

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running
    if update.effective_user.id != ADMIN_ID:
        return
    is_running = False
    await update.message.reply_text("🛑 **OTP Loop Stopped!**", parse_mode="Markdown")

async def copy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    code = query.data.split("_")[-1]
    await query.answer(text=f"🔑 Copied Code: {code}", show_alert=True)

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

    full_pattern = " ".join(parts)
    is_running = True
    
    await update.message.reply_text(f"🚀 **Target:** `{total_count}` | **Interval:** `{delay_seconds}s`", parse_mode="Markdown")

    for i in range(total_count):
        if not is_running:
            break

        # ৪/৫ টি স্থির সংখ্যা রেখে বাকি ❌ অংশ র্যান্ডম করা
        formatted_line = replace_mask(full_pattern)
        if len(formatted_line) > 4:
            random_last = "".join([str(random.randint(0, 9)) for _ in range(4)])
            formatted_line = formatted_line[:-4] + random_last

        otp_code = random.randint(100000, 999999)

        # স্ক্রিনশটের মতো হুবহু লাইন লেআউট
        message_body = f"• {formatted_line}\n"

        # কাস্টম বাটন লেআউট
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑  𝙲𝚘𝚙𝚢 𝚈𝚘𝚞𝚛 𝙺𝚎𝚢", callback_data=f"copy_{otp_code}")],
            [
                InlineKeyboardButton("🤖 Get Number", url="https://t.me/YOUR_GET_NUMBER_LINK"),
                InlineKeyboardButton("📢 Support GP", url="https://t.me/YOUR_DEVELOPER_LINK")
            ]
        ])

        # টেলিগ্রাম ফ্লাড লিমিট হ্যান্ডলিং লুপ
        sent = False
        while not sent and is_running:
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=message_body,
                    reply_markup=keyboard
                )
                sent = True
            except RetryAfter as e:
                # টেলিগ্রাম ব্লক দিলে অটো ওয়েট করবে কিন্তু বটের গণনা থামবে না
                await asyncio.sleep(e.retry_after + 1)
            except Exception as e:
                await asyncio.sleep(1)
                sent = True

        await asyncio.sleep(delay_seconds)

if __name__ == "__main__":
    keep_alive()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CallbackQueryHandler(copy_callback, pattern="^copy_"))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_admin_command))
    app.run_polling()
