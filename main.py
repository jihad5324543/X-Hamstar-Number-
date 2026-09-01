import asyncio
import random
import re
import os
from threading import Thread

from flask import Flask

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CopyTextButton
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from telegram.error import RetryAfter


# =========================================================
#                  UPTIME / KEEP ALIVE
# =========================================================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bot is alive!"


def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(
        host="0.0.0.0",
        port=port
    )


def keep_alive():
    thread = Thread(
        target=run_web,
        daemon=True
    )
    thread.start()


# =========================================================
#                  BOT CONFIGURATION
# =========================================================

# এখানে আপনার নতুন BotFather Token বসাবেন
BOT_TOKEN = "8343790496:AAEh8SEmaLC-DYJ5A_ZIM1WjsHb2-lz2F0w"

# আপনার Admin ID
ADMIN_ID = 5747820322


# =========================================================
#                  PREMIUM EMOJI IDS
# =========================================================

WS_EMOJI_ID = "6298323188849838091"       # WhatsApp
TG_EMOJI_ID = "6296218646284863141"       # Telegram
PY_EMOJI_ID = "6258109564676220200"       # PayPal
FB_EMOJI_ID = "6091599390621834528"       # Facebook
INT_EMOJI_ID = "6258233865324732516"      # Instagram


# =========================================================
#                  GLOBAL STATUS
# =========================================================

is_running = True


# =========================================================
#                  START COMMAND
# =========================================================

async def start_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "⚙️ <b>SECRET OTP BOT Active!</b>",
        parse_mode="HTML"
    )


# =========================================================
#                  STOP COMMAND
# =========================================================

async def stop_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global is_running

    if update.effective_user.id != ADMIN_ID:
        return

    is_running = False

    await update.message.reply_text(
        "🛑 <b>All Running OTP Tasks Stopped!</b>",
        parse_mode="HTML"
    )


# =========================================================
#                  PLATFORM SETUP
# =========================================================

def get_platform_data(full_pattern: str):

    pattern_lower = full_pattern.lower()

    platform = "default"

    # -----------------------------------------
    # WhatsApp
    # -----------------------------------------

    if re.search(
        r"\b(ws|whatsapp)\b",
        pattern_lower
    ):

        platform = "ws"

        emoji_html = (
            f'<tg-emoji emoji-id="{WS_EMOJI_ID}">'
            f'🟢'
            f'</tg-emoji>'
        )

        formatted_text = re.sub(
            r"\b(ws|whatsapp)\b",
            emoji_html,
            full_pattern,
            flags=re.IGNORECASE
        )

        button_text = "WhatsApp"
        button_emoji = WS_EMOJI_ID


    # -----------------------------------------
    # Telegram
    # -----------------------------------------

    elif re.search(
        r"\b(tg|telegram)\b",
        pattern_lower
    ):

        platform = "tg"

        emoji_html = (
            f'<tg-emoji emoji-id="{TG_EMOJI_ID}">'
            f'✈️'
            f'</tg-emoji>'
        )

        formatted_text = re.sub(
            r"\b(tg|telegram)\b",
            emoji_html,
            full_pattern,
            flags=re.IGNORECASE
        )

        button_text = "Telegram"
        button_emoji = TG_EMOJI_ID


    # -----------------------------------------
    # PayPal
    # -----------------------------------------

    elif re.search(
        r"\b(py|paypal)\b",
        pattern_lower
    ):

        platform = "py"

        emoji_html = (
            f'<tg-emoji emoji-id="{PY_EMOJI_ID}">'
            f'🅿️'
            f'</tg-emoji>'
        )

        formatted_text = re.sub(
            r"\b(py|paypal)\b",
            emoji_html,
            full_pattern,
            flags=re.IGNORECASE
        )

        button_text = "PayPal"
        button_emoji = PY_EMOJI_ID


    # -----------------------------------------
    # Facebook
    # -----------------------------------------

    elif re.search(
        r"\b(fb|facebook|facebookl)\b",
        pattern_lower
    ):

        platform = "fb"

        emoji_html = (
            f'<tg-emoji emoji-id="{FB_EMOJI_ID}">'
            f'🟦'
            f'</tg-emoji>'
        )

        formatted_text = re.sub(
            r"\b(fb|facebook|facebookl)\b",
            emoji_html,
            full_pattern,
            flags=re.IGNORECASE
        )

        button_text = "Facebook"
        button_emoji = FB_EMOJI_ID


    # -----------------------------------------
    # Instagram
    # -----------------------------------------

    elif re.search(
        r"\b(int|instagram)\b",
        pattern_lower
    ):

        platform = "int"

        emoji_html = (
            f'<tg-emoji emoji-id="{INT_EMOJI_ID}">'
            f'📸'
            f'</tg-emoji>'
        )

        formatted_text = re.sub(
            r"\b(int|instagram)\b",
            emoji_html,
            full_pattern,
            flags=re.IGNORECASE
        )

        button_text = "Instagram"
        button_emoji = INT_EMOJI_ID


    # -----------------------------------------
    # Default
    # -----------------------------------------

    else:

        formatted_text = full_pattern
        button_text = "Copy Code"
        button_emoji = None


    return (
        platform,
        formatted_text,
        button_text,
        button_emoji
    )


# =========================================================
#                  RUN OTP TASK
# =========================================================

async def run_single_task(
    chat_id: int,
    full_pattern: str,
    total_count: int,
    delay_seconds: float,
    context: ContextTypes.DEFAULT_TYPE
):

    global is_running

    (
        platform,
        formatted_text,
        button_text,
        button_emoji
    ) = get_platform_data(full_pattern)


    # -----------------------------------------
    # Generate messages
    # -----------------------------------------

    for _ in range(total_count):

        if not is_running:
            break


        # -------------------------------------
        # Generate 6 digit OTP
        # -------------------------------------

        otp_code = str(
            random.randint(
                100000,
                999999
            )
        )


        # -------------------------------------
        # Message
        # -------------------------------------

        message_body = (
            f"<b>{formatted_text}</b>"
        )


        # -------------------------------------
        # TRUE COPY BUTTON
        # -------------------------------------
        #
        # চাপলে OTP সরাসরি Telegram Clipboard-এ
        # copy হবে।
        #

        if button_emoji:

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text=button_text,
                            copy_text=CopyTextButton(
                                text=otp_code
                            ),
                            style="primary",
                            icon_custom_emoji_id=button_emoji
                        )
                    ]
                ]
            )

        else:

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text=button_text,
                            copy_text=CopyTextButton(
                                text=otp_code
                            ),
                            style="primary"
                        )
                    ]
                ]
            )


        # -------------------------------------
        # Send message
        # -------------------------------------

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

                await asyncio.sleep(
                    e.retry_after + 1
                )


            except Exception:

                await asyncio.sleep(1)

                sent = True


        # -------------------------------------
        # Delay
        # -------------------------------------

        await asyncio.sleep(
            delay_seconds
        )


# =========================================================
#                  ADMIN COMMAND HANDLER
# =========================================================

async def handle_admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global is_running

    # শুধু Admin
    if update.effective_user.id != ADMIN_ID:
        return


    # আবার চালু
    is_running = True


    text = update.message.text.strip()

    parts = text.split()


    if len(parts) < 2:
        return


    # -----------------------------------------
    # Default values
    # -----------------------------------------

    delay_seconds = 1.0
    total_count = 100


    # -----------------------------------------
    # Timing
    # -----------------------------------------

    last_part = parts[-1].lower()


    # Seconds
    if last_part.endswith("s"):

        try:

            delay_seconds = float(
                last_part[:-1]
            )

            parts.pop()

        except ValueError:
            pass


    # Custom timing
    elif last_part.endswith("c"):

        try:

            delay_seconds = float(
                last_part[:-1]
            )

            parts.pop()

        except ValueError:
            pass


    # Minutes
    elif last_part.endswith("m"):

        try:

            delay_seconds = (
                float(last_part[:-1])
                * 60
            )

            parts.pop()

        except ValueError:
            pass


    # -----------------------------------------
    # Count
    # Example: 10cd
    # -----------------------------------------

    if len(parts) > 0:

        check_count_part = parts[-1].lower()

        if check_count_part.endswith("cd"):

            try:

                total_count = int(
                    check_count_part.replace(
                        "cd",
                        ""
                    )
                )

                parts.pop()

            except ValueError:
                pass


    # -----------------------------------------
    # Full pattern
    # -----------------------------------------

    full_pattern = " ".join(parts)


    if not full_pattern:
        return


    # -----------------------------------------
    # Start task
    # -----------------------------------------

    asyncio.create_task(
        run_single_task(
            chat_id=update.effective_chat.id,
            full_pattern=full_pattern,
            total_count=total_count,
            delay_seconds=delay_seconds,
            context=context
        )
    )


# =========================================================
#                  MAIN
# =========================================================

def main():

    # Keep server alive
    keep_alive()


    # Build Telegram application
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )


    # /start
    app.add_handler(
        CommandHandler(
            "start",
            start_cmd
        )
    )


    # /stop
    app.add_handler(
        CommandHandler(
            "stop",
            stop_cmd
        )
    )


    # Admin text commands
    app.add_handler(
        MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            handle_admin_command
        )
    )


    print("BOT STARTED...")


    # Run bot
    app.run_polling()


# =========================================================
#                  START PROGRAM
# =========================================================

if __name__ == "__main__":
    main()
