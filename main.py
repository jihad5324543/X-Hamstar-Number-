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
ADMIN_ID = 5747820322

WS_EMOJI_ID = "6298323188849838091"
TG_EMOJI_ID = "6296218646284863141"

is_running = True

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ <b>SECRET OTP BOT Active!</b>", parse_mode="HTML")

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running
    if update.effective_user.id != ADMIN_ID:
        return
    
    is_running = False
    await update.message.reply_text("🛑 <b>All Running OTP Tasks Stopped!</b>", parse_mode="HTML")

async def copy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    code = query.data.split("_")[-1]
    await query.answer(text=f"🔑 OTP Code: {code}", show_alert=True)

async def run_single_task(chat_id: int, full_pattern: str, total_count: int, delay_seconds: float, context: ContextTypes.DEFAULT_TYPE):
    global is_running
    
    is_ws = False
    is_tg = False
    
    pattern_lower = full_pattern.lower()
    if "ws" in pattern_lower or "whatsapp" in pattern_lower:
        is_ws = True
    elif "tg" in pattern_lower or "telegram" in pattern_lower:
        is_tg = True

    # মেসেজ বডির জন্য Premium Emoji
    ws_emoji_html = f'<tg-emoji emoji-id="{WS_EMOJI_ID}">🟢</tg-emoji>'
    tg_emoji_html = f'<tg-emoji emoji-id="{TG_EMOJI_ID}">✈️</tg-emoji>'

    formatted_text = full_pattern
    if is_ws:
        formatted_text = re.sub(r'\b(ws|whatsapp)\b', ws_emoji_html, formatted_text, flags=re.IGNORECASE)
    elif is_tg:
        formatted_text = re.sub(r'\b(tg|telegram)\b', tg_emoji_html, formatted_text, flags=re.IGNORECASE)

    for _ in range(total_count):
        if not is_running:
            break

        otp_code = str(random.randint(100000, 999999))
        message_body = f"<b>{formatted_text}</b>"

        # বাটনের টেক্সট (ইমোজি ছাড়া পরিষ্কার ফরম্যাট)
        if is_ws:
            btn_text = "📋 Copy WhatsApp"
        elif is_tg:
            btn_text = "📋 Copy Telegram"
        else:
            btn_text = "📋 Copy Code"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(btn_text, callback_data=f"copy_{otp_code}")]
        ])

        sent = False
        while not sent and is_running:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_body,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                    disable_web_page_preview=True
                )
                sent = True
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                await asyncio.sleep(1)
                sent = True

        await asyncio.sleep(delay_seconds)

async def handle_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running
    if update.effective_user.id != ADMIN_ID:
        return

    is_running = True
    text = update.message.text.strip()
    parts = text.split()

    if len(parts) < 2:
        return

    delay_seconds = 1.0
    total_count = 100

    last_part = parts[-1].lower()
    
    if last_part.endswith("s"):
        try:
            delay_seconds = float(last_part[:-1])
            parts.pop()
        except ValueError:
            pass
    elif last_part.endswith("m"):
        try:
            delay_seconds = float(last_part[:-1]) * 60
            parts.pop()
        except ValueError:
            pass

    check_count_part = parts[-1].lower()
    if check_count_part.endswith("cd"):
        try:
            total_count = int(check_count_part.replace("cd", ""))
            parts.pop()
        except ValueError:
            pass

    full_pattern = " ".join(parts)

    asyncio.create_task(
        run_single_task(
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
