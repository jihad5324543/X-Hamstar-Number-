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
BOT_TOKEN = "8343790496:AAEh8SEmaLC-DYJ5A_ZIM1WjsHb2-lz2F0w"  # আপনার আসল বট টোকেন দিন
ADMIN_ID = 5747820322              # আপনার টেলিগ্রাম আইডি দিন

active_tasks = {}

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ **SECRET OTP BOT Active!**", parse_mode="Markdown")

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    chat_id = update.effective_chat.id
    if chat_id in active_tasks:
        active_tasks[chat_id] = False
        await update.message.reply_text("🛑 **All Running OTP Tasks Stopped!**", parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ No active OTP task to stop.")

async def copy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    code = query.data.split("_")[-1]
    await query.answer(text=f"🔑 Copied OTP: {code}", show_alert=True)

async def run_otp_generator(chat_id: int, full_pattern: str, total_count: int, delay_seconds: float, context: ContextTypes.DEFAULT_TYPE):
    active_tasks[chat_id] = True
    
    # WS এবং TG লোগো হ্যান্ডলিং
    formatted_pattern = full_pattern
    if " WS " in f" {formatted_pattern} " or " ws " in f" {formatted_pattern} ":
        formatted_pattern = re.sub(r'\b(WS|ws)\b', '🟢', formatted_pattern)
    elif " TG " in f" {formatted_pattern} " or " tg " in f" {formatted_pattern} ":
        formatted_pattern = re.sub(r'\b(TG|tg)\b', '✈️', formatted_pattern)

    for i in range(total_count):
        if not active_tasks.get(chat_id, False):
            break

        # ❌ অক্ষত রেখে শেষের ৪ ডিজিট র্যান্ডম চেঞ্জ করা
        final_line = formatted_pattern
        if len(final_line) > 4:
            random_last = "".join([str(random.randint(0, 9)) for _ in range(4)])
            final_line = final_line[:-4] + random_last

        otp_code = str(random.randint(100000, 999999))

        # প্রিমিয়াম আউটপুট লেআউট
        message_body = (
            f"• {final_line}\n"
            f"🔑 **OTP Code:** `{otp_code}`"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑  𝙲𝚘𝚙𝚢 𝚈𝚘𝚞𝚛 𝙺𝚎𝚢", callback_data=f"copy_{otp_code}")],
            [
                InlineKeyboardButton("🤖 Get Number", url="https://t.me/YOUR_GET_NUMBER_LINK"),
                InlineKeyboardButton("📢 Support GP", url="https://t.me/YOUR_DEVELOPER_LINK")
            ]
        ])

        sent = False
        while not sent and active_tasks.get(chat_id, False):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_body,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                sent = True
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception as e:
                await asyncio.sleep(1)
                sent = True

        await asyncio.sleep(delay_seconds)
        
    active_tasks[chat_id] = False

async def handle_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    await update.message.reply_text(
        f"🚀 **Task Started!**\n📊 **Count:** `{total_count}` | ⏱️ **Delay:** `{delay_seconds}s`", 
        parse_mode="Markdown"
    )

    asyncio.create_task(
        run_otp_generator(
            chat_id=update.effective_chat.id, 
            full_pattern=full_pattern, 
            total_count=total_count, 
            delay_seconds=delay_seconds, 
            context=context
        )
    )

if __name__ == "__main__":
    keep_alive()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CallbackQueryHandler(copy_callback, pattern="^copy_"))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_admin_command))
    app.run_polling()
