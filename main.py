# -*- coding: utf-8 -*-
"""
Secret Proxy Shop - Python Telegram Bot
Converted from the supplied Bots.Business command set.

Main features:
- SQLite user/account storage (no 1,000/1,500 user hard limit)
- Force join @secretproxyupdate
- Referral tracking + 5% manual-deposit referral bonus
- Add Balance: ElitePay automatic payment + manual bKash/Nagad/Binance
- Proxy stock, purchase, delivery and purchase/payment history
- Unlimited purchase/history records
- Bangladesh/Dhaka timezone for order/deposit timestamps
- Admin Panel: Statistics, Broadcast, Ban/Unban, Proxy Stock, Add Admin, Edit Proxy
- Dynamic proxy prices and hide/unhide
- /info USER_ID
- /reset_balance USER_ID
- /approve USER_ID AMOUNT [METHOD] [TRX_ID]
- /reject USER_ID
- Multi-admin permissions
- Broadcast text/photo/video/document/animation/audio/voice and copied/forwarded content
- ElitePay automatic deposit + webhook callback

Run:
    pip install -r requirements.txt
    BOT_TOKEN="YOUR_BOT_TOKEN" python main.py

Requires:
    python-telegram-bot >= 22.7
    (needed for real button colors `style=` and premium emoji icons
    `icon_custom_emoji_id=` on InlineKeyboardButton/KeyboardButton —
    Bot API 9.4. Button colors need a Telegram client released after
    Feb 9, 2026; older clients just show a plain button, nothing
    breaks. The emoji icon on a button only renders if this bot's
    owner account has Telegram Premium, or the bot has a Fragment-
    purchased username — otherwise Telegram just skips the icon.)

For Render, use:
    python main.py

Environment variables:
    BOT_TOKEN       Telegram bot token (required)
    WEBHOOK_URL     Public HTTPS base URL for ElitePay callback (recommended)
    PORT            Render/server port (default 10000)

The supplied ElitePay brand key is kept below for the automatic deposit flow.
"""

import asyncio
import html
import io
import hashlib
import hmac
import time
import json
import logging
import os
import random
import re
import sqlite3
import string
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web
from zoneinfo import ZoneInfo

from telegram import (
    Bot,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8634925900:AAH3G2X9P35iRaR1XwFbM6X2Kk7BjzelnxQ").strip()

# Kept exactly as supplied in the provided Bots.Business code.
# ElitePay automatic deposit gateway (replaces the old ZiniPay flow).
ELITEPAY_BRAND_KEY = "h7oCoQ41uok71PnOI6cWMs6gWltTA1dmWro4jjjiggPWvKgOxU"
ELITEPAY_CREATE_URL = "https://pay.elitepay.top/api/payment/create.php"
ZINI_API_KEY_DEFAULT = "684de9bfc8f7de47e6872e792d8c0002abff78222d95301b"
ZINI_CREATE_URL = "https://api.zinipay.com/v1/payment/create"
ZINI_VERIFY_URL = "https://api.zinipay.com/v1/payment/verify"
ZINIPAY_REDIRECT_URL = (os.getenv("ZINIPAY_REDIRECT_URL") or "https://secretproxy.shop").rstrip("/")
ZINIPAY_CANCEL_URL = (os.getenv("ZINIPAY_CANCEL_URL") or ZINIPAY_REDIRECT_URL).rstrip("/")
BINANCE_PAY_BASE = "https://bpay.binanceapi.com"
BINANCE_CREATE_URL = BINANCE_PAY_BASE + "/binancepay/openapi/v2/order"
BINANCE_QUERY_URL = BINANCE_PAY_BASE + "/binancepay/openapi/order/query"
BINANCE_BALANCE_URL = BINANCE_PAY_BASE + "/binancepay/openapi/balance"

CHANNEL_USERNAME = "@secretproxyupdate"
SUPPORT_USERNAME = "@PROXY_X_AGENT"
SUPPORT_URL = "https://t.me/PROXY_X_AGENT"

# The two original admins. New admins can be added from Admin Panel.
DEFAULT_ADMINS = {5747820322, 8777521898}

USD_RATE = 125
MIN_DEPOSIT = 10.0

# Currency helper must be defined before Database.__init__ seeds VPN rows.
def usd_from_bdt(amount) -> str:
    return f"${float(amount) / USD_RATE:.2f}"
TZ = ZoneInfo("Asia/Dhaka")
DB_PATH = os.getenv("DB_PATH", "secret_proxy_shop.db")
PORT = int(os.getenv("PORT", "10019"))
# ElitePay callback base URL. If the hosting panel does not provide a public
# HTTPS variable, use the current Zero-Bot allocation as the fallback.
# Set WEBHOOK_URL to a public HTTPS domain when Zero-Bot provides one.
WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://62.84.177.65:10019").rstrip("/")

# Admin-configurable main-menu visibility. Persisted in SQLite settings.
MENU_BUTTON_DEFAULTS = {
    "buy_proxy": True, "buy_vpn": True, "buy_mail": True, "add_balance": True, "my_balance": True,
    "cart": True, "hourglass": True, "link": True, "chat": True, "bot": True,
}
MENU_BUTTON_LABELS = {
    "buy_proxy": "Buy Proxy", "buy_vpn": "Buy VPN", "buy_mail": "Buy Mail", "add_balance": "Add Balance",
    "my_balance": "My Balance", "cart": "My Purchases", "hourglass": "Payment History",
    "link": "Proxy Data Checker", "chat": "Support", "bot": "Admin Pannel",
}
MENU_VISIBLE = dict(MENU_BUTTON_DEFAULTS)
MENU_BUTTON_NAMES = dict(MENU_BUTTON_LABELS)
# Premium text-emoji mapping uploaded by the admin.
# Format per line: <normal emoji><premium custom emoji id>
# Example: ⚠️6206174450765796040
TEXT_EMOJI_ID = ""  # kept for backward compatibility
TEXT_EMOJI_MAP = {}


def menu_label(key: str) -> str:
    return MENU_BUTTON_NAMES.get(key, MENU_BUTTON_LABELS.get(key, key))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("secret-proxy-shop")

# ============================================================
# PRODUCT CATALOG
# ============================================================

# price = current editable BDT price.
# hidden = admin can hide/unhide from Buy Proxy.
# stock_key = SQLite product stock bucket.
#
# The prices below are taken from the supplied code.
PRODUCTS = {
    "abc": {
        "name": "ABC Proxy 1GB",
        "menu_name": "𝐀𝐁𝐂 𝐏𝐑𝐎𝐗𝐘",
        "price": 254.0,
        "usd": "2.00$",
        "stock_key": "abcproxy_stock",
        "kind": "proxy",
    },
    "dataimpulse": {
        "name": "Dataimpulse Proxy 1GB",
        "menu_name": "𝐃𝐚𝐭𝐚𝐢𝐦𝐩𝐥𝐮𝐬𝐞",
        "price": 150.0,
        "usd": "1.20$",
        "stock_key": "dataimpulse_stock",
        "kind": "proxy",
    },
    "cli": {
        "name": "Cli Proxy 1GB",
        "menu_name": "𝙲𝙻𝙸 𝙿𝚁𝙾𝚇𝚈",
        "price": 114.0,
        "usd": "0.91$",
        "stock_key": "cli_stock",
        "kind": "proxy",
    },
    "owl_200mb": {
        "name": "OWL Proxy 0.2GB",
        "menu_name": "𝐎𝐖𝐋 𝐏𝐑𝐎𝐗𝐘 — 200MB",
        "price": 10.0,
        "usd": "0.08$",
        "stock_key": "owl_200mb_stock",
        "kind": "proxy",
        "checker": "https://t.me/owlproxy_mb_checker_bot",
    },
    "owl_1gb": {
        "name": "OWL Proxy 1GB",
        "menu_name": "𝐎𝐖𝐋 𝐏𝐑𝐎𝐗𝐘 — 1GB",
        "price": 50.0,
        "usd": "0.40$",
        "stock_key": "owl_1gb_stock",
        "kind": "proxy",
    },
    "mesh": {
        "name": "Mesh Proxy 1GB",
        "menu_name": "𝙼𝙴𝚂𝙷 𝙿𝚁𝙾𝚇𝚈",
        "price": 127.0,
        "usd": "1.02$",
        "stock_key": "mesh_stock",
        "kind": "proxy",
    },
    "rocket": {
        "name": "Rocket Proxy 1GB",
        "menu_name": "Rocket Proxy",
        "price": 160.0,
        "usd": "1.28$",
        "stock_key": "rocket_stock",
        "kind": "proxy",
    },
    "rapid": {
        "name": "Rapid Proxy 1GB",
        "menu_name": "Rapid Proxy",
        "price": 127.0,
        "usd": "1.02$",
        "stock_key": "rapid_stock",
        "kind": "proxy",
    },
    "rapid_200mb": {
        "name": "Rapid Proxy 200MB",
        "menu_name": "Rapid Proxy — 200MB",
        "price": 30.0,
        "usd": "0.24$",
        "stock_key": "rapid_200mb_stock",
        "kind": "proxy",
    },
    "9proxy_mb": {
        "name": "9Proxy 100 MB",
        "menu_name": "𝟵𝗣𝗿𝗼𝘅𝘆 𝗠𝗯",
        "price": 15.0,
        "usd": "0.12$",
        "stock_key": "9proxy_mb_stock",
        "kind": "multi_proxy",
        "unit_mb": 100,
    },
    "9proxy_gb": {
        "name": "9 Proxy GB 1GB",
        "menu_name": "9 Proxy GB",
        "price": 140.0,
        "usd": "1.12$",
        "stock_key": "9proxy_gb_stock",
        "kind": "proxy",
    },
    "cdkey": {
        "name": "9Proxy CDKey 1GB",
        "menu_name": "𝟗 𝐏𝐫𝐨𝐱𝐲 🔑 𝐂𝐝𝐤𝐞𝐲",
        "price": 140.0,
        "usd": "1.12$",
        "stock_key": "cdkeyproxy_stock",
        "kind": "key",
    },
}

# ============================================================
# PREMIUM EMOJI HTML HELPERS
# ============================================================

def pe(emoji_id: str, fallback: str) -> str:
    """Telegram HTML custom emoji."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def parse_text_emoji_mapping(raw_text: str):
    """Parse an admin Emoji Id text file into normal-emoji -> custom-ID mappings."""
    mapping = {}
    for raw_line in (raw_text or "").splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue
        # Allow: emoji + spaces + 15-20 digit custom emoji id.
        import re
        m = re.match(r"^(.*?)\s*(\d{15,22})\s*$", line)
        if not m:
            continue
        emoji = m.group(1).strip()
        eid = m.group(2)
        if emoji and eid.isdigit():
            mapping[emoji] = eid
    return mapping


def premiumize_text(text_value: str, parse_mode=None) -> str:
    """Replace mapped normal emoji with Telegram Premium Custom Emoji HTML.

    Existing <tg-emoji> blocks are left untouched. The replacement is applied
    only to HTML messages because the tg-emoji tag requires HTML parsing.
    """
    if not isinstance(text_value, str) or not TEXT_EMOJI_MAP:
        return text_value
    if parse_mode not in (ParseMode.HTML, "HTML", "html", None):
        return text_value

    import re
    # Protect already-premium emoji blocks so their fallback emoji is not
    # converted a second time.
    protected = []
    def _protect(match):
        protected.append(match.group(0))
        return f"\x00PE{len(protected)-1}\x00"

    outside = re.sub(r"<tg-emoji\b[^>]*>.*?</tg-emoji>", _protect, text_value, flags=re.DOTALL | re.IGNORECASE)

    # Longest first so multi-codepoint emoji (e.g. ❤️ / ⚠️) win over shorter
    # prefixes.
    for emoji, eid in sorted(TEXT_EMOJI_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
        if not emoji:
            continue
        outside = outside.replace(emoji, pe(eid, emoji))

    for idx, original in enumerate(protected):
        outside = outside.replace(f"\x00PE{idx}\x00", original)
    return outside


def premiumize_caption(kwargs):
    """Apply the emoji map to a Telegram media caption in-place."""
    caption = kwargs.get("caption")
    if isinstance(caption, str):
        kwargs["caption"] = premiumize_text(caption, kwargs.get("parse_mode"))
    return kwargs


# Deposit/payment premium emojis
DEPOSIT_AMOUNT_EMOJI = "6001287064589439895"
DEPOSIT_BALANCE_EMOJI = "6190336264940559752"
DEPOSIT_METHOD_EMOJIS = {
    "bkash": ("6089033864922011301", "💸"),
    "nagad": ("6086699962513495516", "💸"),
    "rocket": ("6086803441160558654", "💸"),
    "binance": ("6087120955207851591", "💸"),
}
ADMIN_NEW_DEPOSIT_EMOJI = "6206378324273403309"
ADMIN_DEPOSIT_EMOJI = "6203886371363364022"
ADMIN_TOTAL_DEPOSIT_EMOJI = "6206155797722830770"

def deposit_method_emoji(method: str) -> str:
    """Return the premium emoji for the actual payment method."""
    m = (method or "").strip().lower()
    for name, (emoji_id, fallback) in DEPOSIT_METHOD_EMOJIS.items():
        if name in m:
            return pe(emoji_id, fallback)
    return pe(DEPOSIT_METHOD_EMOJIS["bkash"][0], DEPOSIT_METHOD_EMOJIS["bkash"][1])


PRODUCT_PREMIUM_EMOJI = "6105185226877574701"
VPN_HEADER_EMOJI = "6077678113125306912"

# VPN catalog. Each VPN account is one stock item; the existing stock system
# is reused so Hide / Unhide / Add Stock / Clear Stock / Edit Price all work
# exactly like the Proxy products.
VPN_PRODUCTS = {
    "vpn_nord_7": {
        "name": "NORD VPN 7 DAY", "price": 30.0, "emoji_id": "5796345694969140339", "emoji": "🥏",
    },
    "vpn_nord_30": {
        "name": "NORD VPN 30 DAY", "price": 200.0, "emoji_id": "5796345694969140339", "emoji": "🥏",
    },
    "vpn_proton_14": {
        "name": "Proton VPN 14 DAY", "price": 60.0, "emoji_id": "5796504385420792898", "emoji": "🥚",
    },
    "vpn_proton_30": {
        "name": "Proton VPN 30 DAY", "price": 150.0, "emoji_id": "5796504385420792898", "emoji": "🥚",
    },
    "vpn_express_3": {
        "name": "EXPRESS VPN 3 DAY", "price": 15.0, "emoji_id": "5796153709931009517", "emoji": "👨‍⚖",
    },
    "vpn_surfshark_7": {
        "name": "Surfshark VPN 7 DAY", "price": 30.0, "emoji_id": "5796592771552777710", "emoji": "🍸",
    },
    "vpn_hma_7": {
        "name": "HMA VPN 7 DAY", "price": 30.0, "emoji_id": "5796595022115639741", "emoji": "🥑",
    },
    "vpn_bitdefender_6": {
        "name": "Bitdefender VPN 6 DAY", "price": 25.0, "emoji_id": "5793973558761756143", "emoji": "😁",
    },
}

MAIL_PRODUCTS = {
    "mail_outlook_trusted": {"name": "Outlook Trusted", "price": 0.0, "emoji_id": "5796209712009581332", "emoji": "📧"},
    "mail_outlook_fr": {"name": "Outlook Fr", "price": 0.0, "emoji_id": "5796209712009581332", "emoji": "📧"},
    "mail_hotmail_trusted": {"name": "Hotmail Trusted", "price": 0.0, "emoji_id": "5796209712009581332", "emoji": "📧"},
    "mail_api_gmail": {"name": "Api Gmail", "price": 0.0, "emoji_id": "5796209712009581332", "emoji": "📧"},
}
MAIL_ORDER = list(MAIL_PRODUCTS.keys())

VPN_ORDER = list(VPN_PRODUCTS.keys())

# ============================================================
# BUTTON COLORS + PREMIUM EMOJI ICONS
# ============================================================
# Bot API 9.4 / PTB 22.7+ lets buttons carry a real color (style) and a
# real premium-emoji icon (icon_custom_emoji_id) — no more HTML hacks.
# style requires a Telegram client released after Feb 9, 2026 (older
# clients just show a plain button). icon_custom_emoji_id only renders
# if the bot owner's account has Telegram Premium (or the bot has a
# Fragment-purchased username) — otherwise the icon is silently skipped.
#
# Only the 20 premium emoji supplied by the owner are used, anywhere.
BTN_EMO = {
    "check":     "6269163801178804220",  # ✅
    "check2":    "6267008582294705964",  # ✅
    "warn":      "6267039884016358504",  # ⚠️
    "circle":    "6282702779341346176",  # ⭕️
    "shop":      "6205984948218762570",  # 🛍
    "cross":     "6206110936789423908",  # ❌
    "hundred":   "6203738495639360972",  # 💯
    "link":      "6206497372176913599",  # 🔗
    "chat":      "6206495649895028694",  # 💬
    "check3":    "6298670698948724690",  # ✅
    "hourglass": "6105060531092069509",  # ⌛
    "user":      "6152280926257684465",  # 👤
    "bridge":    "6152032939140981100",  # 🌉
    "rocket":    "6084602502349595551",  # 🚀
    "server":    "6104800784354909891",  # 🖥
    "cart":      "6104734070627900577",  # 🛒
    "heart":     "6082500510995191781",  # ❤️
    "cash":      "6269136309093143551",  # 💸
    "tag":       "6269368817147711866",  # 🏷
    "bot":       "5332560480508591604",  # 🤖
    "buy_proxy": "6105185226877574701",
    "buy_vpn":   "6105033189330263391",
    "buy_mail":  "5796209712009581332",
    "add_balance": "6001287064589439895",
    "my_balance": "6190336264940559752",
    "main_menu": "6327723159113966200",
    "set_welcome": "6084602502349595551",
    "payments": "6269136309093143551", "manualpay": "6269136309093143551", "menuhide": "6205984948218762570",
    "zini": "6269136309093143551", "elitepay": "6269136309093143551", "manual": "6269136309093143551",
    "bkash": "6089033864922011301", "nagad": "6086699962513495516", "rocket_method": "6086803441160558654", "binance_method": "6206116515951941744",
    "world": "6104798108590285245",
    "deposit": "6203886371363364022",
    "vpn_shop": "6077678113125306912",
    "vpn_nord": "5796345694969140339",
    "vpn_proton": "5796504385420792898",
    "vpn_express": "5796153709931009517",
    "vpn_surfshark": "5796592771552777710",
    "vpn_hma": "5796595022115639741",
    "vpn_bitdefender": "5793973558761756143",
    "vpn_shop": VPN_HEADER_EMOJI,
}



def btn(text: str, style: str = None, icon: str = None, **kwargs) -> InlineKeyboardButton:
    """InlineKeyboardButton with a real color (style) + a real premium
    emoji icon (icon_custom_emoji_id) from the store above.
    style: 'primary' (blue) | 'success' (green) | 'danger' (red)
    icon: a key from BTN_EMO, or None for no icon.
    """
    return InlineKeyboardButton(
        text,
        style=style,
        icon_custom_emoji_id=((icon if str(icon).isdigit() else BTN_EMO.get(icon)) if icon else None),
        **kwargs,
    )


def kbtn(text: str, style: str = None, icon: str = None) -> KeyboardButton:
    """Same as btn(), for the main ReplyKeyboardMarkup."""
    return KeyboardButton(
        text,
        style=style,
        icon_custom_emoji_id=BTN_EMO.get(icon) if icon else None,
    )

def product_title_html(p) -> str:
    return pe(PRODUCT_PREMIUM_EMOJI, "🛍") + " " + clean_html(p["name"])

def product_kind(p) -> str:
    return str(p["kind"] or "proxy") if "kind" in p.keys() else ("vpn" if p["key"] in VPN_PRODUCTS else "proxy")

def product_menu_name(p) -> str:
    return str(p["menu_name"] or p["name"]) if "menu_name" in p.keys() else p["name"]

def proxy_style(p) -> str:
    """Purchase UI style for proxy: Q=quantity summary, S=standard."""
    try:
        return str(p["proxy_style"] or "S").upper() if "proxy_style" in p.keys() else "S"
    except Exception:
        return "S"

def proxy_size(p) -> str:
    try:
        return str(p["proxy_size"] or "1GB") if "proxy_size" in p.keys() else "1GB"
    except Exception:
        return "1GB"

def product_emoji_id(p) -> str:
    if "emoji_id" in p.keys() and p["emoji_id"]:
        return str(p["emoji_id"])
    if p["key"] in VPN_PRODUCTS:
        return VPN_PRODUCTS[p["key"]].get("emoji_id")
    return PRODUCT_PREMIUM_EMOJI

def product_emoji_fallback(p) -> str:
    if "emoji" in p.keys() and p["emoji"]:
        return str(p["emoji"])
    if p["key"] in VPN_PRODUCTS:
        return VPN_PRODUCTS[p["key"]].get("emoji", "🛡")
    return "🛍"

def product_title_with_emoji(p) -> str:
    return pe(product_emoji_id(p), product_emoji_fallback(p)) + " " + clean_html(p["name"])

def normalize_checker_url(value):
    """Turn whatever the admin typed (@bot, t.me/bot, bot) into a valid https
    URL. Telegram rejects url-buttons with an invalid URL, which made the
    whole proxy page fail to open. Returns None if it can't be fixed."""
    v = str(value or "").strip().strip("<>\"' ")
    if not v:
        return None
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(\S+)$", v, re.I)
    if m:
        rest = m.group(1).strip("/")
        return ("https://t.me/" + rest) if rest else None
    if v.startswith("@"):
        v = v[1:]
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", v):
        return "https://t.me/" + v
    if re.match(r"^https?://[^\s/]+\.[^\s/]+\S*$", v):
        return v
    return None


def promo_discount_price(price, percent) -> float:
    return round(float(price) * (100.0 - float(percent)) / 100.0, 2)


async def promo_effective(context, key, base_price):
    """(unit_price, percent, code). Discount only counts while the user has
    applied the code AND the admin still has it enabled (not deleted)."""
    base_price = float(base_price)
    try:
        applied = (context.user_data.get("promo_applied") or {}).get(key)
    except Exception:
        applied = None
    if not applied:
        return base_price, 0.0, None
    row = await db.promo_get(key)
    if not row or not row["enabled"] or str(row["code"]).lower() != str(applied).lower():
        return base_price, 0.0, None
    return promo_discount_price(base_price, row["percent"]), float(row["percent"]), row["code"]


def checker_for(key: str):
    # Dynamic products keep their checker in the database. Static products keep
    # their original checker map for backwards compatibility.
    static = {
        "abc": "https://t.me/Abcgb_bot", "dataimpulse": "https://t.me/DataImpluseGB_Bot",
        "cli": "https://t.me/CliproxyGBbot", "owl_200mb": "https://t.me/owlproxy_mb_checker_bot",
        "owl_1gb": "https://t.me/owlproxy_mb_checker_bot", "mesh": "https://t.me/MeshProxyGB_bot",
        "rocket": "https://t.me/RocketProxyGB_bot", "rapid": "https://t.me/RapidProxyGB_Bot",
        "rapid_200mb": "https://t.me/RapidProxyGB_Bot", "9proxy_mb": "https://t.me/Proxy9gb_bot",
        "9proxy_gb": "https://t.me/Proxy9gb_bot",
    }
    return static.get(key)

def duration_for_vpn(p) -> str:
    if "duration" in p.keys() and p["duration"]:
        return str(p["duration"])
    return str(p["name"]).split(" VPN ")[-1] if " VPN " in str(p["name"]) else str(p["name"])

async def stock_out_text(p) -> str:
    return (
        pe(PRODUCT_PREMIUM_EMOJI, "🛍") + f" <b>{clean_html(p['name'])}</b>\n\n"
        "❌ <b>Stock Out</b>\n\n"
        "এই Proxy-এর Stock বর্তমানে শেষ।\n"
        "Please contact Support and try again later."
    )


# ============================================================
# DATABASE
# ============================================================

class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self._init()

    def _init(self):
        c = self.conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")

        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            balance REAL NOT NULL DEFAULT 0,
            total_deposit REAL NOT NULL DEFAULT 0,
            banned INTEGER NOT NULL DEFAULT 0,
            joined_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            referrer_id INTEGER,
            ref_count INTEGER NOT NULL DEFAULT 0
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            usd TEXT,
            stock_key TEXT NOT NULL,
            hidden INTEGER NOT NULL DEFAULT 0,
            kind TEXT NOT NULL DEFAULT 'proxy',
            menu_name TEXT,
            checker TEXT,
            emoji_id TEXT,
            emoji TEXT,
            duration TEXT,
            proxy_size TEXT,
            proxy_style TEXT NOT NULL DEFAULT 'S'
        )
        """)

        # Safe migration for databases created by older versions.
        for col, typ in [
            ('kind', "TEXT NOT NULL DEFAULT 'proxy'"),
            ('menu_name', 'TEXT'),
            ('checker', 'TEXT'),
            ('emoji_id', 'TEXT'),
            ('emoji', 'TEXT'),
            ('duration', 'TEXT'),
            ('proxy_size', 'TEXT'),
            ('proxy_style', "TEXT NOT NULL DEFAULT 'S'"),
            ('delivery_mode', "TEXT NOT NULL DEFAULT 'auto'"),
        ]:
            try:
                c.execute(f"ALTER TABLE products ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass

        c.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product_key TEXT NOT NULL,
            product_name TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            key_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            trxid TEXT,
            created_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS pending_deposits (
            user_id INTEGER PRIMARY KEY,
            amount REAL NOT NULL,
            method TEXT,
            trxid TEXT,
            pending_message_id INTEGER
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS zini_orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            zini_val_id TEXT,
            created_at TEXT NOT NULL,
            gateway TEXT NOT NULL DEFAULT 'elitepay'
        )
        """)
        try:
            c.execute("ALTER TABLE zini_orders ADD COLUMN gateway TEXT NOT NULL DEFAULT 'elitepay'")
        except sqlite3.OperationalError:
            pass

        c.execute("""
        CREATE TABLE IF NOT EXISTS manual_orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product_key TEXT NOT NULL,
            product_name TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            admin_id INTEGER,
            delivered_order_id TEXT
        )
        """)
        # Manual delivery draft details are entered by an admin after Send Order.
        try:
            c.execute("ALTER TABLE manual_orders ADD COLUMN draft_details TEXT")
        except sqlite3.OperationalError:
            pass

        c.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            product_key TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            percent REAL NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        for k, p in PRODUCTS.items():
            c.execute("""
            INSERT INTO products(
                key,name,price,usd,stock_key,hidden,kind,menu_name,checker,
                emoji_id,emoji,duration,proxy_size,proxy_style,delivery_mode
            )
            VALUES(?,?,?,?,?,0,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO NOTHING
            """, (
                k, p["name"], p["price"], p["usd"], p["stock_key"],
                p.get("kind", "proxy"), p.get("menu_name", p["name"]),
                checker_for(k) if 'checker_for' in globals() else p.get("checker"),
                p.get("emoji_id"), p.get("emoji", "🛍"), p.get("duration"),
                p.get("proxy_size"), p.get("proxy_style", "S"), "auto"
            ))

        for k, p in VPN_PRODUCTS.items():
            c.execute("""
            INSERT INTO products(
                key,name,price,usd,stock_key,hidden,kind,menu_name,checker,
                emoji_id,emoji,duration,proxy_size,proxy_style,delivery_mode
            )
            VALUES(?,?,?,?,?,0,'vpn',?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO NOTHING
            """, (
                k, p["name"], p["price"], usd_from_bdt(p["price"]), f"{k}_stock",
                p.get("name"), None, p.get("emoji_id"), p.get("emoji", "🛡"),
                p.get("name", "").split()[-2] + " " + p.get("name", "").split()[-1] if len(p.get("name", "").split()) >= 2 else "",
                None, "S", "auto"
            ))

        for k, p in MAIL_PRODUCTS.items():
            c.execute("""
            INSERT INTO products(
                key,name,price,usd,stock_key,hidden,kind,menu_name,checker,
                emoji_id,emoji,duration,proxy_size,proxy_style,delivery_mode
            )
            VALUES(?,?,?,?,?,0,'mail',?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO NOTHING
            """, (
                k, p["name"], p["price"], usd_from_bdt(p["price"]), f"{k}_stock",
                p["name"], None, p.get("emoji_id"), p.get("emoji", "📧"),
                None, None, "S", "auto"
            ))

        # Backfill metadata for legacy rows without overwriting admin changes.
        for k, p in PRODUCTS.items():
            c.execute("""UPDATE products SET kind=COALESCE(NULLIF(kind,''),'proxy'),
                         menu_name=COALESCE(menu_name,?), checker=COALESCE(checker,?)
                         WHERE key=?""", (p.get("menu_name", p["name"]), p.get("checker"), k))
        for k, p in VPN_PRODUCTS.items():
            c.execute("""UPDATE products SET kind='vpn', menu_name=COALESCE(menu_name,?),
                         emoji_id=COALESCE(emoji_id,?), emoji=COALESCE(emoji,?), duration=COALESCE(duration,?)
                         WHERE key=?""", (p["name"], p.get("emoji_id"), p.get("emoji", "🛡"), p["name"], k))
        for k, p in MAIL_PRODUCTS.items():
            c.execute("""UPDATE products SET kind='mail', menu_name=COALESCE(menu_name,?),
                         emoji_id=COALESCE(emoji_id,?), emoji=COALESCE(emoji,?)
                         WHERE key=?""", (p["name"], p.get("emoji_id"), p.get("emoji", "📧"), k))

        defaults = {
            "gateway:zini:enabled":"0", "gateway:zini:key":ZINI_API_KEY_DEFAULT, "gateway:zini:minimum":str(MIN_DEPOSIT),
            "gateway:elitepay:enabled":"1", "gateway:elitepay:key":ELITEPAY_BRAND_KEY, "gateway:elitepay:minimum":str(MIN_DEPOSIT),
            "gateway:manual:enabled":"1", "manual:min_deposit":str(MIN_DEPOSIT),
            "manual:bkash:number":"01350701462", "manual:nagad:number":"01753586931", "manual:rocket:number":"",
            "manual:bkash:enabled":"1", "manual:nagad:enabled":"1", "manual:rocket:enabled":"0", "manual:binance:enabled":"0",
            "manual:binance:api_key":"", "manual:binance:security_key":"", "manual:binance:rate":"125",
        }
        for _k,_v in defaults.items():
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",(_k,_v))

        for admin_id in DEFAULT_ADMINS:
            c.execute("""
            INSERT INTO admins(user_id, added_by, added_at)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO NOTHING
            """, (admin_id, admin_id, now_bd()))

        # Fix checker links saved in a bad format (@bot, t.me/bot ...) so the
        # proxy page opens again. Unfixable values are cleared.
        for _r in c.execute("SELECT key, checker FROM products WHERE checker IS NOT NULL AND checker!=''").fetchall():
            _fixed = normalize_checker_url(_r["checker"])
            if _fixed != _r["checker"]:
                c.execute("UPDATE products SET checker=? WHERE key=?", (_fixed, _r["key"]))

        self.conn.commit()

    async def execute(self, sql, params=(), fetch=False, many=False):
        async with self.lock:
            cur = self.conn.cursor()
            if many:
                cur.executemany(sql, params)
            else:
                cur.execute(sql, params)
            if fetch:
                rows = cur.fetchall()
                return rows
            self.conn.commit()
            return cur.lastrowid

    async def user_upsert(self, tg_user, referrer_id=None):
        now = now_bd()
        existing = await self.execute(
            "SELECT user_id, referrer_id FROM users WHERE user_id=?",
            (tg_user.id,), True
        )
        if existing:
            await self.execute("""
                UPDATE users SET username=?, first_name=?, last_name=?, last_seen=?
                WHERE user_id=?
            """, (
                tg_user.username, tg_user.first_name or "", tg_user.last_name or "",
                now, tg_user.id
            ))
            return False

        await self.execute("""
            INSERT INTO users(
                user_id,username,first_name,last_name,joined_at,last_seen,referrer_id
            ) VALUES(?,?,?,?,?,?,?)
        """, (
            tg_user.id, tg_user.username, tg_user.first_name or "",
            tg_user.last_name or "", now, now, referrer_id
        ))

        if referrer_id and referrer_id != tg_user.id:
            await self.execute(
                "UPDATE users SET ref_count=ref_count+1 WHERE user_id=?",
                (referrer_id,)
            )
        return True

    async def get_user(self, user_id):
        rows = await self.execute(
            "SELECT * FROM users WHERE user_id=?", (int(user_id),), True
        )
        return rows[0] if rows else None

    async def get_user_by_username(self, username):
        username = str(username or "").strip().lstrip("@").lower()
        if not username:
            return None
        rows = await self.execute(
            "SELECT * FROM users WHERE lower(username)=? ORDER BY user_id LIMIT 1",
            (username,), True
        )
        return rows[0] if rows else None

    async def all_user_ids(self):
        rows = await self.execute("SELECT user_id FROM users", fetch=True)
        return [int(r["user_id"]) for r in rows]

    async def is_admin(self, user_id):
        rows = await self.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (int(user_id),), True
        )
        return bool(rows)

    async def add_admin(self, user_id, added_by):
        await self.execute("""
            INSERT INTO admins(user_id,added_by,added_at)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO NOTHING
        """, (int(user_id), int(added_by), now_bd()))

    async def remove_admin(self, user_id):
        if int(user_id) in DEFAULT_ADMINS:
            return False
        await self.execute("DELETE FROM admins WHERE user_id=?", (int(user_id),))
        return True

    async def admin_ids(self):
        rows = await self.execute("SELECT user_id FROM admins", fetch=True)
        return [int(r["user_id"]) for r in rows]

    async def balance(self, user_id):
        row = await self.get_user(user_id)
        return float(row["balance"]) if row else 0.0

    async def add_balance(self, user_id, amount):
        await self.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (float(amount), int(user_id))
        )

    async def set_balance(self, user_id, amount):
        await self.execute(
            "UPDATE users SET balance=? WHERE user_id=?",
            (float(amount), int(user_id))
        )

    async def add_total_deposit(self, user_id, amount):
        await self.execute(
            "UPDATE users SET total_deposit=total_deposit+? WHERE user_id=?",
            (float(amount), int(user_id))
        )

    async def ban(self, user_id, value=True):
        await self.execute(
            "UPDATE users SET banned=? WHERE user_id=?",
            (1 if value else 0, int(user_id))
        )

    async def is_banned(self, user_id):
        row = await self.get_user(user_id)
        return bool(row and row["banned"])

    async def get_product(self, key):
        rows = await self.execute(
            "SELECT * FROM products WHERE key=?", (key,), True
        )
        return rows[0] if rows else None

    async def set_product_price(self, key, price):
        price = float(price)
        await self.execute(
            "UPDATE products SET price=?, usd=? WHERE key=?",
            (price, usd_from_bdt(price), key)
        )

    async def set_product_hidden(self, key, hidden):
        await self.execute(
            "UPDATE products SET hidden=? WHERE key=?", (1 if hidden else 0, key)
        )

    async def products(self):
        return await self.execute(
            "SELECT * FROM products ORDER BY rowid", fetch=True
        )

    async def create_product(self, key, name, price, kind, menu_name=None, checker=None, emoji_id=None, emoji=None, duration=None, proxy_size=None, proxy_style="S"):
        checker = normalize_checker_url(checker)
        await self.execute("""
            INSERT INTO products(key,name,price,usd,stock_key,hidden,kind,menu_name,checker,emoji_id,emoji,duration,proxy_size,proxy_style,delivery_mode)
            VALUES(?,?,?,?,?,0,?,?,?,?,?,?,?,?,?)
        """, (key, name, float(price), usd_from_bdt(price), f"{key}_stock", kind,
              menu_name or name, checker, emoji_id, emoji or ("🛍" if kind == "proxy" else "🛡"), duration,
              proxy_size, proxy_style if proxy_style in ("Q", "S") else "S", "auto"))

    async def set_product_meta(self, key, **fields):
        allowed = {"name", "menu_name", "checker", "emoji_id", "emoji", "duration", "kind", "proxy_size", "proxy_style", "delivery_mode"}
        parts, vals = [], []
        if fields.get("checker"):
            fields["checker"] = normalize_checker_url(fields["checker"])
        for k, v in fields.items():
            if k in allowed and v is not None:
                parts.append(f"{k}=?")
                vals.append(v)
        if parts:
            vals.append(key)
            await self.execute(f"UPDATE products SET {', '.join(parts)} WHERE key=?", tuple(vals))

    async def delete_product(self, key):
        # Dynamic products can be removed without touching users/orders/history.
        # Delete only the product definition and its remaining stock.
        async with self.lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM stock WHERE product_key=?", (key,))
            cur.execute("DELETE FROM products WHERE key=?", (key,))
            changed = cur.rowcount
            self.conn.commit()
            return changed > 0

    async def get_setting(self, key, default=None):
        rows = await self.execute("SELECT value FROM settings WHERE key=?", (key,), True)
        if not rows:
            return default
        return rows[0]["value"]

    async def set_setting(self, key, value):
        await self.execute("""
            INSERT INTO settings(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))

    async def promo_get(self, key):
        rows = await self.execute("SELECT * FROM promo_codes WHERE product_key=?", (key,), True)
        return rows[0] if rows else None

    async def promo_set(self, key, percent, code):
        await self.execute("""
            INSERT INTO promo_codes(product_key,code,percent,enabled,created_at) VALUES(?,?,?,1,?)
            ON CONFLICT(product_key) DO UPDATE SET code=excluded.code, percent=excluded.percent, enabled=1
        """, (key, str(code), float(percent), now_bd()))

    async def promo_toggle(self, key):
        await self.execute("UPDATE promo_codes SET enabled=1-enabled WHERE product_key=?", (key,))

    async def promo_delete(self, key):
        await self.execute("DELETE FROM promo_codes WHERE product_key=?", (key,))

    async def total_ip_purchased(self, user_id):
        rows = await self.execute("""
            SELECT COALESCE(SUM(quantity),0) AS c FROM orders
            WHERE user_id=? AND product_key NOT IN (SELECT key FROM products WHERE kind IN ('vpn','mail'))
        """, (int(user_id),), True)
        return int(rows[0]["c"] or 0)

    async def stock_count(self, key):
        rows = await self.execute(
            "SELECT COUNT(*) AS c FROM stock WHERE product_key=?", (key,), True
        )
        return int(rows[0]["c"])

    async def add_stock(self, key, items):
        if not items:
            return 0
        await self.execute(
            "INSERT INTO stock(product_key,value,created_at) VALUES(?,?,?)",
            [(key, x, now_bd()) for x in items],
            many=True
        )
        return len(items)

    async def clear_stock(self, key):
        await self.execute("DELETE FROM stock WHERE product_key=?", (key,))

    async def pop_stock(self, key, qty=1):
        # Transaction protected by asyncio lock. Oldest stock is delivered first.
        async with self.lock:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT id,value FROM stock
                WHERE product_key=?
                ORDER BY id ASC LIMIT ?
            """, (key, int(qty)))
            rows = cur.fetchall()
            if len(rows) < qty:
                return []
            ids = [r["id"] for r in rows]
            cur.executemany("DELETE FROM stock WHERE id=?", [(x,) for x in ids])
            self.conn.commit()
            return [r["value"] for r in rows]

    async def save_order(self, order_id, user_id, product_key, name, price, qty, key_text):
        await self.execute("""
            INSERT INTO orders(id,user_id,product_key,product_name,price,quantity,key_text,created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (
            order_id, int(user_id), product_key, name, float(price), int(qty),
            key_text, now_bd()
        ))

    async def orders(self, user_id):
        return await self.execute("""
            SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC, rowid DESC
        """, (int(user_id),), True)

    async def deposits(self, user_id):
        return await self.execute("""
            SELECT * FROM deposits WHERE user_id=? ORDER BY created_at DESC, id DESC
        """, (int(user_id),), True)

    async def save_deposit(self, user_id, amount, method, trxid):
        await self.execute("""
            INSERT INTO deposits(user_id,amount,method,trxid,created_at)
            VALUES(?,?,?,?,?)
        """, (int(user_id), float(amount), method, trxid or "N/A", now_bd()))

    async def set_pending_deposit(self, user_id, amount, method=None, trxid=None, msg_id=None):
        await self.execute("""
            INSERT INTO pending_deposits(user_id,amount,method,trxid,pending_message_id)
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                amount=excluded.amount,
                method=excluded.method,
                trxid=excluded.trxid,
                pending_message_id=excluded.pending_message_id
        """, (int(user_id), float(amount), method, trxid, msg_id))

    async def get_pending_deposit(self, user_id):
        rows = await self.execute(
            "SELECT * FROM pending_deposits WHERE user_id=?", (int(user_id),), True
        )
        return rows[0] if rows else None

    async def clear_pending_deposit(self, user_id):
        await self.execute("DELETE FROM pending_deposits WHERE user_id=?", (int(user_id),))

    async def create_zini(self, order_id, user_id, amount, gateway="elitepay"):
        await self.execute("""
            INSERT INTO zini_orders(order_id,user_id,amount,status,created_at,gateway)
            VALUES(?,?,?,?,?,?)
        """, (order_id, int(user_id), float(amount), "pending", now_bd(), gateway))

    async def get_zini(self, order_id):
        rows = await self.execute(
            "SELECT * FROM zini_orders WHERE order_id=?", (order_id,), True
        )
        return rows[0] if rows else None

    async def get_pending_zini(self, limit=50):
        rows = await self.execute(
            "SELECT * FROM zini_orders WHERE status='pending' AND zini_val_id IS NOT NULL "
            "ORDER BY created_at ASC LIMIT ?", (int(limit),), True
        )
        return rows

    async def set_zini(self, order_id, status=None, val_id=None):
        if status is not None and val_id is not None:
            await self.execute(
                "UPDATE zini_orders SET status=?,zini_val_id=? WHERE order_id=?",
                (status, val_id, order_id)
            )
        elif status is not None:
            await self.execute(
                "UPDATE zini_orders SET status=? WHERE order_id=?",
                (status, order_id)
            )
        elif val_id is not None:
            await self.execute(
                "UPDATE zini_orders SET zini_val_id=? WHERE order_id=?",
                (val_id, order_id)
            )

    async def create_manual_order(self, order_id, user_id, product_key, product_name, price, quantity=1):
        await self.execute("""INSERT INTO manual_orders(order_id,user_id,product_key,product_name,price,quantity,status,created_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                           (order_id,int(user_id),product_key,product_name,float(price),int(quantity),"pending",now_bd()))

    async def get_manual_order(self, order_id):
        rows=await self.execute("SELECT * FROM manual_orders WHERE order_id=?",(order_id,),True)
        return rows[0] if rows else None

    async def set_manual_order(self, order_id, status, admin_id=None, delivered_order_id=None):
        await self.execute("""UPDATE manual_orders SET status=?, admin_id=COALESCE(?,admin_id), delivered_order_id=COALESCE(?,delivered_order_id) WHERE order_id=?""",
                           (status,admin_id,delivered_order_id,order_id))

    async def stats(self):
        users = await self.execute("SELECT COUNT(*) AS c FROM users", fetch=True)
        banned = await self.execute(
            "SELECT COUNT(*) AS c FROM users WHERE banned=1", fetch=True
        )
        admins = await self.execute("SELECT COUNT(*) AS c FROM admins", fetch=True)
        deposits = await self.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM deposits", fetch=True
        )
        orders = await self.execute("SELECT COUNT(*) AS c FROM orders", fetch=True)
        sales = await self.execute(
            "SELECT COALESCE(SUM(price*quantity),0) AS s FROM orders", fetch=True
        )
        return {
            "users": int(users[0]["c"]),
            "banned": int(banned[0]["c"]),
            "admins": int(admins[0]["c"]),
            "deposits": float(deposits[0]["s"]),
            "orders": int(orders[0]["c"]),
            "sales": float(sales[0]["s"]),
        }


# Bangladesh/Dhaka timestamp helper must be defined before Database initialization.
def now_bd() -> str:
    return datetime.now(TZ).strftime("%d/%m/%Y %I:%M %p")


db = Database(DB_PATH)

# ============================================================
# UTILITIES
# ============================================================


def order_id() -> str:
    return "ORD" + "".join(random.choices(string.digits, k=8))


def money(v) -> str:
    return f"{float(v):.2f}"


def clean_html(s: str) -> str:
    return html.escape(str(s or ""))


def parse_amount(text: str) -> Optional[float]:
    try:
        x = float(text.strip().replace(",", ""))
        if x <= 0:
            return None
        return x
    except Exception:
        return None


def parse_referrer(args):
    if not args:
        return None
    try:
        x = int(args[0])
        return x
    except Exception:
        return None


async def ensure_user(update: Update):
    u = update.effective_user
    if not u:
        return
    ref = parse_referrer(update.message.text.split()[1:]) if (
        update.message and update.message.text and update.message.text.startswith("/start ")
    ) else None
    is_new = await db.user_upsert(u, ref)
    if is_new and ref and ref != u.id:
        try:
            await update.get_bot().send_message(
                ref,
                "🎉 <b>New Referral Joined!</b>\n\n"
                "A new user has joined using your referral link. "
                "You will receive a <b>5% bonus</b> when they make a deposit!",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


async def ensure_user_from_callback(query):
    u = query.from_user
    await db.user_upsert(u)


async def banned_guard(update: Update) -> bool:
    u = update.effective_user
    if not u:
        return True
    if await db.is_banned(u.id):
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ আপনি ব্যানড!", show_alert=True)
            except Exception:
                pass
        elif update.effective_message:
            await update.effective_message.reply_text(
                pe("6206110936789423908", "❌") + " <b>আপনি ব্যানড!</b>",
                parse_mode=ParseMode.HTML,
            )
        return True
    return False


async def is_admin(user_id: int) -> bool:
    return await db.is_admin(user_id)


async def admin_guard(update: Update) -> bool:
    u = update.effective_user
    if not u or not await is_admin(u.id):
        if update.callback_query:
            await update.callback_query.answer(
                "❌ শুধুমাত্র Admin-এর জন্য!", show_alert=True
            )
        elif update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ এই কমান্ডটি শুধুমাত্র এডমিনদের জন্য!"
            )
        return False
    return True


# ============================================================
# FORCE CHANNEL JOIN
# ============================================================
DEFAULT_FORCE_JOIN_CHANNELS = [CHANNEL_USERNAME]

async def get_force_join_channels():
    raw = await db.get_setting("force_join_channels", None)
    if raw is None:
        return list(DEFAULT_FORCE_JOIN_CHANNELS)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    return []

async def force_join_enabled() -> bool:
    raw = await db.get_setting("force_join_enabled", "1")
    return str(raw).lower() in ("1", "true", "yes", "on", "enabled")

async def save_force_join_channels(channels):
    clean = []
    for ch in channels:
        ch = str(ch).strip()
        if ch and ch not in clean:
            clean.append(ch)
    await db.set_setting("force_join_channels", json.dumps(clean, ensure_ascii=False))
    return clean

def normalize_channel(value: str) -> str:
    value = (value or "").strip()
    value = value.replace("https://t.me/", "").replace("http://t.me/", "")
    value = value.split("?", 1)[0].split("/", 1)[0].strip()
    if not value.startswith("@"):
        value = "@" + value
    return value

async def check_joined(bot: Bot, user_id: int) -> bool:
    if not await force_join_enabled():
        return True
    channels = await get_force_join_channels()
    if not channels:
        return True
    for channel in channels:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception as e:
            log.warning("Force join check failed for %s: %s", channel, e)
            # A configured channel must be successfully verified; this avoids
            # silently bypassing a channel when its configuration is invalid.
            return False
    return True

async def join_keyboard():
    channels = await get_force_join_channels()
    rows = []
    for i, channel in enumerate(channels, 1):
        username = channel.lstrip("@")
        rows.append([btn(
            f"Join Channel {i}", style="primary", icon="link",
            url=f"https://t.me/{username}"
        )])
    rows.append([btn("Joined / Verify", style="success", icon="check", callback_data="verify_join")])
    return InlineKeyboardMarkup(rows)

async def send_force_join(message):
    channels = await get_force_join_channels()
    names = "\n".join(f"{i}. <b>{clean_html(ch)}</b>" for i, ch in enumerate(channels, 1))
    text = (
        pe("6267039884016358504", "⚠️")
        + " <b>Bot ব্যবহার করতে আগে আমাদের Channel Join করুন</b>\n\n"
        + (names + "\n\n" if names else "")
        + pe("6206497372176913599", "🔗")
        + " নিচের সবগুলো Channel-এ Join করে তারপর <b>Joined / Verify</b> চাপুন।"
    )
    await message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=await join_keyboard()
    )

# ============================================================
# CUSTOM /start WELCOME MESSAGE
# ============================================================
DEFAULT_WELCOME_MESSAGE = (
    "╔═══════════╗\n"
    + pe("6088984206510137565", "👋") + " <b>PROXY &amp; VPN</b>\n"
    + "╚═══════════╝\n"
    + pe("6084602502349595551", "🚀") + " <b>Welcome to PROXY &amp; VPN Service</b>\n"
    + "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    + pe("5352694861990501856", "✅") + " <b>Choose an option below</b>\n"
    + "to continue using the bot.\n"
    + "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    + pe("5352838545826420397", "💎") + " <b>Premium PROXY &amp; VPN Service</b>"
)

async def setting_bool(key, default=False):
    return (await db.get_setting(key, "1" if default else "0")) == "1"

async def gateway_enabled(gateway):
    return await setting_bool(f"gateway:{gateway}:enabled", gateway == "elitepay")

async def gateway_key(gateway):
    default = ELITEPAY_BRAND_KEY if gateway == "elitepay" else (ZINI_API_KEY_DEFAULT if gateway == "zini" else "")
    return await db.get_setting(f"gateway:{gateway}:key", default)

async def gateway_minimum(gateway):
    default = MIN_DEPOSIT
    try: return float(await db.get_setting(f"gateway:{gateway}:minimum", str(default)))
    except Exception: return default

async def menu_visible(key):
    return MENU_VISIBLE.get(key, True)

async def get_welcome_message() -> str:
    return await db.get_setting("welcome_message", DEFAULT_WELCOME_MESSAGE)


# ============================================================
# MAIN REPLY MENU
# ============================================================

def main_menu(is_admin_user=False):
    rows=[]
    first=[]
    if MENU_VISIBLE.get("buy_proxy",True): first.append(kbtn(menu_label("buy_proxy"),"success","buy_proxy"))
    if MENU_VISIBLE.get("buy_vpn",True): first.append(kbtn(menu_label("buy_vpn"),"success","buy_vpn"))
    if first: rows.append(first)
    if MENU_VISIBLE.get("buy_mail",True): rows.append([kbtn(menu_label("buy_mail"),"success","buy_mail")])
    if MENU_VISIBLE.get("add_balance",True) or MENU_VISIBLE.get("my_balance",True):
        r=[]
        if MENU_VISIBLE.get("add_balance",True): r.append(kbtn(menu_label("add_balance"),"success","add_balance"))
        if MENU_VISIBLE.get("my_balance",True): r.append(kbtn(menu_label("my_balance"),"primary","my_balance"))
        rows.append(r)
    if MENU_VISIBLE.get("cart",True) or MENU_VISIBLE.get("hourglass",True):
        r=[]
        if MENU_VISIBLE.get("cart",True): r.append(kbtn(menu_label("cart"),"primary","cart"))
        if MENU_VISIBLE.get("hourglass",True): r.append(kbtn(menu_label("hourglass"),"primary","hourglass"))
        rows.append(r)
    if MENU_VISIBLE.get("link",True): rows.append([kbtn(menu_label("link"),"primary","link")])
    if MENU_VISIBLE.get("chat",True): rows.append([kbtn(menu_label("chat"),"danger","chat")])
    if is_admin_user and MENU_VISIBLE.get("bot",True): rows.append([kbtn(menu_label("bot"),"danger","bot")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# ============================================================
# /start
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update)

    if await banned_guard(update):
        return

    if not await check_joined(context.bot, update.effective_user.id):
        await send_force_join(update.message)
        return

    is_ad = await is_admin(update.effective_user.id)
    welcome = await get_welcome_message()
    await update.message.reply_text(
        welcome,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(is_ad),
    )


async def verify_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await ensure_user_from_callback(q)

    if not await check_joined(context.bot, q.from_user.id):
        await q.message.reply_text(
            "⚠️ এখনও Join করা হয়নি। আগে Channel Join করুন।",
            reply_markup=await join_keyboard(),
        )
        return

    # Remove the old force-join message after successful verification.
    try:
        await q.message.delete()
    except Exception:
        pass

    await context.bot.send_message(
        q.from_user.id,
        await get_welcome_message(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(await is_admin(q.from_user.id)),
    )


# ============================================================
# MY BALANCE
# ============================================================

async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await banned_guard(update):
        return
    u = update.effective_user
    row = await db.get_user(u.id)
    balance = float(row["balance"]) if row else 0.0
    deposited = float(row["total_deposit"]) if row else 0.0
    username = f"@{u.username}" if u.username else "N/A"
    name = (u.first_name or "") + (f" {u.last_name}" if u.last_name else "")
    joined = str(row["joined_at"]) if row else "N/A"
    try:
        joined = datetime.strptime(joined, "%d/%m/%Y %I:%M %p").strftime("%Y-%m-%d")
    except Exception:
        pass
    ip_count = await db.total_ip_purchased(u.id)

    text = (
        pe("5352838545826420397", "💎") + " <b>My Account</b> ✴️\n\n"
        + pe("6307777408300753473", "👤") + f" <b>Name:</b> {clean_html(name)}\n"
        + pe("6206096153511990389", "🔵") + f" <b>Username:</b> {clean_html(username)}\n"
        + pe("6086814943082976469", "🆔") + f" <b>UID:</b> {u.id}\n"
        + "📅" + f" <b>Join:</b> {clean_html(joined)}\n\n"
        + pe("6190336264940559752", "💰") + f" <b>My Balance:</b> ${balance / USD_RATE:.2f}\n"
        + pe("6105185226877574701", "🎁") + f" <b>Total Deposited:</b> ${deposited / USD_RATE:.2f}\n"
        + "💳" + f" <b>Total Ip Purchased:</b> {ip_count}"
    )
    kb = InlineKeyboardMarkup([[
        btn("Deposit", "success", "deposit", callback_data="add_balance"),
        btn("History", "primary", "hourglass", callback_data="payment_history"),
    ]])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

# ============================================================
# PAYMENT / ADD BALANCE
# ============================================================

async def send_add_balance_message(message, context: ContextTypes.DEFAULT_TYPE):
    # AutoPay intentionally keeps the original one-step UX: when an automatic
    # gateway is enabled, Add Balance immediately asks for the amount instead
    # of showing ZiniPay/ElitePay selection buttons. ZiniPay has priority when
    # both automatic gateways are enabled; otherwise ElitePay is used.
    context.user_data["deposit_step"] = None

    zini_enabled = await gateway_enabled("zini")
    elite_enabled = await gateway_enabled("elitepay")
    manual_enabled = await gateway_enabled("manual")

    if zini_enabled or elite_enabled:
        gateway = "zini" if zini_enabled else "elitepay"
        minimum = await gateway_minimum(gateway)
        context.user_data["deposit_step"] = "gateway_amount"
        context.user_data["deposit_gateway"] = gateway

        # This is the original AutoPay-style screen requested by the owner.
        method_line = (
            deposit_method_emoji("bkash") + " bKash  |  "
            + deposit_method_emoji("nagad") + " Nagad  |  "
            + deposit_method_emoji("rocket") + " Rocket  |  "
            + deposit_method_emoji("binance") + " Binance"
        )
        text = (
            pe("6104800784354909891", "💸") + " <b>Deposit (AutoPay)</b>\n"
            + "━━━━━━━━━━━━━━━━━━━━\n"
            + pe("6267039884016358504", "👑") + " <b>Method:</b> " + method_line + "\n"
            + pe(DEPOSIT_AMOUNT_EMOJI, "💳") + f" <b>Minimum:</b> {minimum:.2f}৳\n\n"
            + pe("6105060531092069509", "💫") + " <b>কত টাকা balance-এ add করতে চান?</b>\n"
            + "<i>উদাহরণ: 100, 200, 500</i>"
        )
        await message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Cancel", "danger", "cross", callback_data="cancel_add_balance")]])
        )
        return

    # Manual-only: go directly to the manual method selector.
    if manual_enabled:
        await manual_payment_menu_from_message(message)
        return

    await message.reply_text(
        "❌ <b>No payment method is currently available.</b>\nPlease contact support.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[btn("Close", "danger", "cross", callback_data="cancel_add_balance")]])
    )


async def manual_payment_menu_from_message(message):
    """Open Manual Payment methods directly from a normal Message."""
    rows = []
    icons = {"bkash": "bkash", "nagad": "nagad", "rocket": "rocket_method", "binance": "binance_method"}
    for method, label in [("bkash", "bKash"), ("nagad", "Nagad"), ("rocket", "Rocket"), ("binance", "Binance")]:
        if await manual_method_enabled(method):
            rows.append([btn(label, "success" if method != "binance" else "primary", icons[method], callback_data=f"manualmethod:{method}")])
    rows.append([btn("Cancel", "danger", "cross", callback_data="cancel_add_balance")])
    header = pe("5318845125219077511", "💳")
    await message.reply_text(header + " <b>Select Payment Method:</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))

async def begin_gateway_amount(q, context, gateway):
    minimum = await gateway_minimum(gateway)
    context.user_data["deposit_step"]="gateway_amount"
    context.user_data["deposit_gateway"]=gateway
    await q.message.reply_text(
        f"💳 <b>{clean_html(gateway.title())} Deposit</b>\n\n"
        f"Minimum deposit: <b>{minimum:.2f}৳</b>\n\n"
        "Enter the amount you want to add:", parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="cancel_add_balance")]])
    )



async def start_elitepay_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float):
    """Create an ElitePay payment link using the supplied Bots.Business gateway flow."""
    u = update.effective_user
    oid = f"EPAY_{u.id}_{int(datetime.now(TZ).timestamp()*1000)}"
    await db.create_zini(oid, u.id, amount, "elitepay")

    wait_msg = await update.message.reply_text(
        pe("6084602502349595551", "🚀") + " <b>Processing your payment...</b>\n\n"
        + pe("6105060531092069509", "⏳") + " <b>Please wait...</b>",
        parse_mode=ParseMode.HTML,
    )

    # Bots.Business used Libs.Webhooks.getUrlFor(). On this Python host the
    # equivalent is our own HTTP callback endpoint. WEBHOOK_URL can override it
    # with the provider-approved public HTTPS domain when available.
    webhook = f"{WEBHOOK_URL}/elitepay_webhook"
    callback_url = f"{webhook}?order_id={oid}"
    clean_name = re.sub(r"[^a-zA-Z0-9 ]", "", u.first_name or "User").strip() or "Customer"
    bot_info = await update.get_bot().get_me()
    bot_username = bot_info.username or ""

    payload = {
        "brand_key": await gateway_key("elitepay"),
        "origin": "bdusp.com",
        "provider": "pay.elitepay.top",
        "host": "bb",
        "order_id": oid,
        "cus_name": clean_name,
        "cus_email": "example@gmail.com",
        "amount": float(amount),
        "success_url": callback_url,
        "cancel_url": f"https://t.me/{bot_username}" if bot_username else callback_url,
    }
    if webhook:
        payload["webhook_url"] = webhook

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ELITEPAY_CREATE_URL,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                raw = await resp.text()
                try:
                    data = json.loads(raw) if raw else {}
                except Exception:
                    data = {}

        if not isinstance(data, dict):
            data = {}
        result = data.get("data") if isinstance(data.get("data"), dict) else data
        payment_url = (
            result.get("payment_url") or result.get("paymentUrl") or result.get("url")
            or result.get("checkout_url") or data.get("payment_url")
            or data.get("paymentUrl") or data.get("url") or data.get("checkout_url")
        )
        provider_order = result.get("order_id") or result.get("invoice_id") or data.get("order_id") or data.get("invoice_id")
        if provider_order:
            await db.set_zini(oid, val_id=str(provider_order))

        status_value = result.get("status", data.get("status"))
        success_value = result.get("success", data.get("success"))
        if success_value is None:
            success_value = result.get("ok", data.get("ok"))
        ok_status = (
            success_value is True
            or str(status_value).strip().lower() in {"true", "success", "completed", "paid", "1"}
            or bool(payment_url)
        )
        log.info("ElitePay create response: http=%s status=%r payment_url=%s order_id=%s body=%s",
                 resp.status, status_value, bool(payment_url), provider_order or oid, raw[:1000])

        try:
            await wait_msg.delete()
        except Exception:
            pass

        if resp.status < 400 and ok_status and payment_url:
            kb = InlineKeyboardMarkup([
                [btn("Pay Now (পেমেন্ট করুন)", "success", "cash", url=str(payment_url))],
                [btn("Cancel", "danger", "cross", callback_data="cancel_add_balance")],
            ])
            await update.message.reply_text(
                "<b>Payment link তৈরি হয়েছে!</b>\n\n"
                "নিচের <b>Pay Now</b> বাটনে ক্লিক করে পেমেন্ট সম্পূর্ণ করুন।\n"
                "পেমেন্ট সফল হলে আপনার balance অটো add হবে।",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            api_msg = result.get("message") or data.get("message") or data.get("error") or raw[:700] or f"HTTP {resp.status}"
            await update.message.reply_text(
                "❌ <b>Payment link তৈরি হয়নি।</b>\n\n"
                f"<b>ElitePay:</b> <code>{clean_html(str(api_msg)[:1000])}</code>",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        try:
            await wait_msg.delete()
        except Exception:
            pass
        log.exception("ElitePay create error")
        await update.message.reply_text(
            f"❌ Payment link তৈরি করতে সমস্যা হয়েছে।\n<code>{clean_html(str(e)[:500])}</code>",
            parse_mode=ParseMode.HTML,
        )


async def start_zinipay_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float):
    """Create a ZiniPay invoice using the original working request shape."""
    u = update.effective_user
    oid = f"ZINI_{u.id}_{int(datetime.now(TZ).timestamp()*1000)}"
    await db.create_zini(oid, u.id, amount, "zini")

    wait = await update.message.reply_text(
        "⏳ <b>Generating auto-payment link...</b>",
        parse_mode=ParseMode.HTML,
    )

    webhook = f"{WEBHOOK_URL}/zinipay_webhook" if WEBHOOK_URL else ""
    clean_name = re.sub(r"[^a-zA-Z0-9 ]", "", u.first_name or "User").strip() or "Customer"
    payload = {
        "cus_name": clean_name,
        "cus_email": f"user_{u.id}@telegram.org",
        "amount": float(amount),
        "metadata": {"order_id": oid, "user_id": str(u.id)},
        "redirect_url": ZINIPAY_REDIRECT_URL,
        "cancel_url": ZINIPAY_CANCEL_URL,
    }
    if webhook:
        payload["webhook_url"] = webhook

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ZINI_CREATE_URL,
                headers={
                    "Content-Type": "application/json",
                    "zini-api-key": await gateway_key("zini"),
                },
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                raw = await resp.text()
                try:
                    data = json.loads(raw) if raw else {}
                except Exception:
                    data = {}

        if not isinstance(data, dict):
            data = {}
        result = data.get("data") if isinstance(data.get("data"), dict) else data
        payment_url = result.get("payment_url") or data.get("payment_url")
        invoice_id = (
            result.get("invoice_id") or result.get("val_id") or result.get("invoice") or result.get("id")
            or data.get("invoice_id") or data.get("val_id") or data.get("invoice") or data.get("id")
        )
        if not invoice_id and payment_url:
            url_text = str(payment_url)
            for pattern in [r"/payment/([^/?#]+)", r"[?&](?:invoice_id|val_id|invoice|id)=([^&#]+)"]:
                m = re.search(pattern, url_text, re.IGNORECASE)
                if m:
                    invoice_id = m.group(1)
                    break
        if invoice_id:
            invoice_id = str(invoice_id).strip()
            await db.set_zini(oid, val_id=invoice_id)

        status_value = result.get("status", data.get("status"))
        success_value = result.get("success", data.get("success"))
        ok_status = (
            status_value is True
            or str(status_value).strip().lower() in {"true", "success", "completed", "paid"}
            or success_value is True
        )
        log.info("ZiniPay create response: http=%s status=%r payment_url=%s invoice_id=%s body=%s", resp.status, status_value, bool(payment_url), invoice_id, raw[:1000])

        if resp.status < 400 and ok_status and payment_url:
            await wait.delete()
            kb = InlineKeyboardMarkup([
                [btn("Pay Now (পেমেন্ট করুন)", "success", "cash", url=str(payment_url))],
                [btn("Verify Payment (যাচাই করুন)", "success", "check3", callback_data=f"zini_verify:{oid}")],
                [btn("কিভাবে টাকা Add করবেন", "primary", "chat", url="https://t.me/secretproxyupdate/86")],
            ])
            await update.message.reply_text(
                "<b>Payment invoice has been created!</b>\n\n"
                "নিচের <b>Pay Now</b> বাটনে ক্লিক করে পেমেন্ট সম্পূর্ণ করুন।\n"
                "পেমেন্ট শেষ হলে <b>Verify Payment</b> চাপুন। Bot ব্যাকগ্রাউন্ডেও payment status check করবে।",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            api_msg = result.get("message") or data.get("message") or data.get("error") or raw[:700] or f"HTTP {resp.status}"
            await wait.edit_text(
                "❌ <b>পেমেন্ট লিংক তৈরি হয়নি।</b>\n\n"
                f"<b>ZiniPay:</b> <code>{clean_html(str(api_msg)[:1000])}</code>\n\n"
                "⚠️ <b>redirect_url</b> অবশ্যই আপনার ZiniPay Brand-এর website domain-এর সাথে match করতে হবে। বর্তমানে bot-এ <code>"
                + clean_html(ZINIPAY_REDIRECT_URL) + "</code> ব্যবহার হচ্ছে।",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        log.exception("ZiniPay create error")
        try:
            await wait.edit_text(f"❌ পেমেন্ট লিংক তৈরি করতে সমস্যা হয়েছে।\n<code>{clean_html(str(e)[:500])}</code>", parse_mode=ParseMode.HTML)
        except Exception:
            pass


async def verify_zini_invoice(invoice_id: str):
    """Verify one ZiniPay invoice directly from the backend."""
    async with aiohttp.ClientSession() as session:
        async with session.post(ZINI_VERIFY_URL,headers={"Content-Type":"application/json","zini-api-key":await gateway_key("zini")},json={"invoice_id":str(invoice_id),"val_id":str(invoice_id)},timeout=aiohttp.ClientTimeout(total=30)) as resp:
            raw=await resp.text()
            try: data=json.loads(raw) if raw else {}
            except Exception: data={}
            return resp.status,raw,data

def zini_invoice_completed(raw: str,res: dict)->bool:
    if not isinstance(res,dict): return False
    vals=[res.get("status"),res.get("payment_status")]
    if isinstance(res.get("data"),dict): vals += [res["data"].get("status"),res["data"].get("payment_status")]
    return any(str(v).strip().upper()=="COMPLETED" for v in vals if v is not None)

async def complete_zini_payment(bot: Bot,oid: str,res: dict):
    data=await db.get_zini(oid)
    if not data or data["status"]=="completed": return
    amount=float(data["amount"]); await db.set_zini(oid,status="completed"); target=int(data["user_id"])
    payload=res.get("data") if isinstance(res,dict) and isinstance(res.get("data"),dict) else (res if isinstance(res,dict) else {})
    trx_id=payload.get("trx_id") or payload.get("transaction_id") or payload.get("invoice_id") or (res.get("trx_id") if isinstance(res,dict) else None) or oid
    method=payload.get("payment_method") or payload.get("method") or (str(data["gateway"]).title() if "gateway" in data.keys() else "ZiniPay")
    await db.add_balance(target,amount); await db.add_total_deposit(target,amount); await db.save_deposit(target,amount,method,trx_id)
    row=await db.get_user(target); bal=float(row["balance"]) if row else 0.0; usd=bal/USD_RATE
    msg=pe("6269163801178804220","✅")+" <b>Deposit Successful!</b>\n\n━━━━━━━━━━━━━━\n"+pe(DEPOSIT_AMOUNT_EMOJI,"🪙")+f" <b>Amount:</b> {amount:.1f}৳\n\n"+deposit_method_emoji(method)+f" <b>Method:</b> {clean_html(str(method))}\n\n"+pe(DEPOSIT_BALANCE_EMOJI,"💰")+f" <b>Your Total Balance :</b> {bal:.4f}৳ (${usd:.2f} USD)\n━━━━━━━━━━━━━━\n"+pe("6203738495639360972","💯")+" Deposit added successfully."
    try: await bot.send_message(target,msg,parse_mode=ParseMode.HTML)
    except Exception: pass
    if row:
        name=((row["first_name"] or "")+" "+(row["last_name"] or "")).strip() or "Customer"; uname=f"@{row['username']}" if row["username"] else "N/A"
        admin_msg=pe(ADMIN_NEW_DEPOSIT_EMOJI,"🎉")+" <b>New Deposit Received</b>\n\n━━━━━━━━━━━━━━\n"+pe("6152280926257684465","👤")+f" <b>Name:</b> {clean_html(name)}\n"+pe("6152280926257684465","👤")+f" <b>User ID:</b> <code>{target}</code>\n"+pe("6206497372176913599","🔗")+f" <b>Username:</b> {clean_html(uname)}\n\n"+pe(ADMIN_DEPOSIT_EMOJI,"📥")+f" <b>Deposit:</b> {amount} BDT\n"+deposit_method_emoji(method)+f" <b>Method:</b> {clean_html(str(method))}\n"+pe("6104734070627900577","🛒")+f" <b>Invoice ID:</b> <code>{clean_html(str(trx_id))}</code>\n"+pe("6105060531092069509","⌛")+f" <b>Time:</b> {now_bd()}\n\n"+pe(DEPOSIT_BALANCE_EMOJI,"💰")+f" <b>User Balance:</b> {bal:.2f} BDT\n"+pe(ADMIN_TOTAL_DEPOSIT_EMOJI,"💵")+f" <b>Total Deposit:</b> {float(row['total_deposit']):.2f} BDT\n━━━━━━━━━━━━━━"
        for aid in await db.admin_ids():
            try: await bot.send_message(aid,admin_msg,parse_mode=ParseMode.HTML)
            except Exception: pass

async def zini_verify_callback(update: Update,context: ContextTypes.DEFAULT_TYPE,oid: str):
    q=update.callback_query
    try: await q.answer()
    except Exception: pass
    data=await db.get_zini(oid)
    if not data: await q.message.reply_text("❌ <b>অর্ডার সম্পর্কিত তথ্য পাওয়া যায়নি!</b>",parse_mode=ParseMode.HTML); return
    if data["status"]=="completed":
        try: await q.message.delete()
        except Exception: pass
        await context.bot.send_message(q.from_user.id,"✅ <b>এই পেমেন্টটি ইতিমধ্যে আপনার ব্যালেন্সে যোগ করা হয়েছে!</b>",parse_mode=ParseMode.HTML); return
    search_id=data["zini_val_id"]
    if not search_id: await q.message.reply_text("❌ <b>ZiniPay invoice ID পাওয়া যায়নি।</b>\n\nনতুন করে Add Balance করে একটি নতুন payment invoice তৈরি করুন।",parse_mode=ParseMode.HTML); return
    try:
        status,raw,res=await verify_zini_invoice(str(search_id))
        if status<400 and zini_invoice_completed(raw,res):
            await complete_zini_payment(context.bot,oid,res)
            try: await q.message.delete()
            except Exception: pass
        else:
            detail=(res.get("message") if isinstance(res,dict) else "") or raw[:1500] or "Payment is not completed yet."
            await q.message.reply_text("⏳ <b>পেমেন্ট এখনও completed হয়নি।</b>\n\n<code>"+clean_html(str(detail))[:4000]+"</code>",parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("ZiniPay verify callback failed")
        await q.message.reply_text("❌ <b>পেমেন্ট যাচাই করতে সমস্যা হয়েছে।</b>\n\n<code>"+clean_html(str(e)[:800])+"</code>",parse_mode=ParseMode.HTML)

async def zini_payment_poller(bot: Bot):
    while True:
        try:
            for row in await db.get_pending_zini(50):
                if str(row["gateway"] or "")!="zini": continue
                try:
                    status,raw,res=await verify_zini_invoice(row["zini_val_id"])
                    if status<400 and zini_invoice_completed(raw,res): await complete_zini_payment(bot,row["order_id"],res)
                except Exception: log.exception("ZiniPay polling failed for %s",row["order_id"])
                await asyncio.sleep(0.2)
        except Exception: log.exception("ZiniPay poller loop error")
        await asyncio.sleep(15)

async def zinipay_webhook(request: web.Request):
    try:
        raw = await request.text()
        try: data = json.loads(raw) if raw else {}
        except Exception: data = {}
        oid = request.query.get("order_id") or data.get("order_id") or (data.get("metadata") or {}).get("order_id")
        if not oid:
            return web.json_response({"ok": True, "ignored": True})
        row = await db.get_zini(oid)
        if not row:
            return web.json_response({"ok": True, "ignored": True})
        val = row["zini_val_id"] or data.get("val_id") or data.get("invoice_id")
        verified = False
        if val:
            try:
                status, verify_raw, verify_res = await verify_zini_invoice(str(val))
                verified = status < 400 and zini_invoice_completed(verify_raw, verify_res)
                if verified:
                    data = verify_res if isinstance(verify_res, dict) else data
            except Exception:
                log.exception("ZiniPay verify failed")
        if not verified:
            st = str(data.get("status") or "").lower()
            verified = st in {"completed", "success", "paid", "true"} or data.get("success") is True
        if verified:
            await complete_elitepay_payment(request.app["telegram_bot"], oid, data)
            return web.json_response({"ok": True, "paid": True})
        return web.json_response({"ok": True, "paid": False})
    except Exception as e:
        log.exception("Zini webhook error")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


def binance_headers(api_key, secret, body):
    ts=str(int(time.time()*1000)); nonce=''.join(random.choice(string.ascii_letters+string.digits) for _ in range(32))
    payload=ts+"\n"+nonce+"\n"+body+"\n"
    sig=hmac.new(secret.encode(),payload.encode(),hashlib.sha512).hexdigest().upper()
    return {"Content-Type":"application/json","BinancePay-Timestamp":ts,"BinancePay-Nonce":nonce,"BinancePay-Certificate-SN":api_key,"BinancePay-Signature":sig}

async def start_binance_payment(update, context, bdt_amount):
    u=update.effective_user
    rate=float(await db.get_setting("manual:binance:rate","125") or 125)
    if rate<=0: rate=125
    usdt=round(float(bdt_amount)/rate,2)
    api_key=await db.get_setting("manual:binance:api_key","")
    secret=await db.get_setting("manual:binance:security_key","")
    if not api_key or not secret:
        await update.message.reply_text("❌ <b>Binance API is not configured by the administrator.</b>",parse_mode=ParseMode.HTML); return
    oid="BN"+str(int(time.time()*1000))+str(u.id)[-6:]
    await db.create_zini(oid,u.id,float(bdt_amount),"binance")
    body=json.dumps({"env":{"terminalType":"APP"},"merchantTradeNo":oid,"orderAmount":usdt,"currency":"USDT","goods":{"goodsType":"01","goodsCategory":"D000","referenceGoodsId":"DEPOSIT","goodsName":"Telegram Balance Deposit","goodsDetail":f"Deposit {bdt_amount:.2f} BDT at rate {rate:.2f}"}},separators=(",",":"))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(BINANCE_CREATE_URL,headers=binance_headers(api_key,secret,body),data=body,timeout=aiohttp.ClientTimeout(total=30)) as resp:
                raw=await resp.text(); data=json.loads(raw) if raw else {}
        d=data.get("data") or {}
        checkout=d.get("checkoutUrl") or d.get("checkoutURL") or d.get("deeplink") or d.get("universalUrl")
        if resp.status>=400 or not checkout:
            code=data.get("code") or data.get("errorCode") or "N/A"
            err=data.get("errorMessage") or data.get("message") or data.get("error") or raw[:600] or f"HTTP {resp.status}"
            await update.message.reply_text("❌ <b>Binance payment link could not be created.</b>\n\n"+f"<b>Code:</b> <code>{clean_html(str(code))}</code>\n<b>Reason:</b> <code>{clean_html(str(err)[:700])}</code>",parse_mode=ParseMode.HTML); return
        await update.message.reply_text(f"<b>Binance Pay</b>\n\nAmount: <b>{bdt_amount:.2f}৳</b>\nPay amount: <b>{usdt:.2f} USDT</b>\nRate: <b>{rate:.2f}</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Pay with Binance","success","cash",url=checkout)],[btn("Cancel","danger","cross",callback_data="cancel_add_balance")]]))
        asyncio.create_task(binance_poll_payment(update.get_bot(),oid,usdt,api_key,secret))
    except Exception as e:
        log.exception("Binance create error")
        await update.message.reply_text(f"❌ Binance API error.\n<code>{clean_html(str(e)[:500])}</code>",parse_mode=ParseMode.HTML)

async def binance_poll_payment(bot, oid, expected_usdt, api_key, secret):
    for _ in range(90):
        await asyncio.sleep(10)
        try:
            body=json.dumps({"merchantTradeNo":oid},separators=(",",":"))
            async with aiohttp.ClientSession() as session:
                async with session.post(BINANCE_QUERY_URL,headers=binance_headers(api_key,secret,body),data=body,timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    data=await resp.json(content_type=None)
            d=data.get("data") or {}
            status=str(d.get("status") or "").upper()
            paid_amount=float(d.get("orderAmount") or d.get("totalFee") or expected_usdt)
            if status=="PAID" and abs(paid_amount-expected_usdt)<0.01:
                await complete_elitepay_payment(bot,oid,{"transaction_id":d.get("transactionId") or oid,"payment_method":"Binance Pay"})
                return
        except Exception:
            log.exception("Binance payment poll failed for %s",oid)
async def complete_elitepay_payment(bot: Bot, oid: str, res: dict):
    data = await db.get_zini(oid)
    if not data:
        return
    if data["status"] == "completed":
        try:
            await bot.send_message(
                data["user_id"],
                pe("6269163801178804220", "✅")
                + " <b>এই পেমেন্টটি ইতিমধ্যে আপনার ব্যালেন্সে যোগ করা হয়েছে!</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    amount = float(data["amount"])
    await db.set_zini(oid, status="completed")

    target_user = int(data["user_id"])
    trx_id = (
        res.get("trx_id")
        or res.get("transaction_id")
        or (res.get("data") or {}).get("trx_id")
        or oid
    )
    method = (
        res.get("payment_method")
        or res.get("method")
        or (res.get("data") or {}).get("payment_method")
        or (res.get("data") or {}).get("method")
        or ("Binance Pay" if str(data["gateway"])=="binance" else str(data["gateway"]).title())
    )

    await db.add_balance(target_user, amount)
    await db.add_total_deposit(target_user, amount)
    await db.save_deposit(target_user, amount, method, trx_id)

    row = await db.get_user(target_user)
    new_balance = float(row["balance"])
    usd_bal = new_balance / USD_RATE

    success_msg = (
        pe("6269163801178804220", "✅") + " <b>Deposit Successful!</b>\n\n"
        "━━━━━━━━━━━━━━\n"
        + pe(DEPOSIT_AMOUNT_EMOJI, "🪙")
        + f" <b>Amount:</b> {amount:.1f}৳\n\n"
        + deposit_method_emoji(method)
        + f" <b>Method:</b> {clean_html(method)}\n\n"
        + pe(DEPOSIT_BALANCE_EMOJI, "💰")
        + f" <b>Your Total Balance :</b> {new_balance:.4f}৳ (${usd_bal:.2f} USD)\n"
        "━━━━━━━━━━━━━━\n"
        + pe("6203738495639360972", "💯")
        + " Deposit added successfully."
    )
    try:
        await bot.send_message(
            target_user, success_msg, parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

    admins = await db.admin_ids()
    u = await db.get_user(target_user)
    name = ((u["first_name"] or "") + " " + (u["last_name"] or "")).strip() or "Customer"
    uname = f"@{u['username']}" if u["username"] else "N/A"

    admin_msg = (
        pe(ADMIN_NEW_DEPOSIT_EMOJI, "🎉") + " <b>New Deposit Received</b>\n\n"
        "━━━━━━━━━━━━━━\n"
        + pe("6152280926257684465", "👤") + f" <b>Name:</b> {clean_html(name)}\n"
        + pe("6152280926257684465", "👤") + f" <b>User ID:</b> <code>{target_user}</code>\n"
        + pe("6206497372176913599", "🔗") + f" <b>Username:</b> {clean_html(uname)}\n\n"
        + pe(ADMIN_DEPOSIT_EMOJI, "📥") + f" <b>Deposit:</b> {amount} BDT\n"
        + deposit_method_emoji(method) + f" <b>Method:</b> {clean_html(method)}\n"
        + pe("6104734070627900577", "🛒") + f" <b>Invoice ID:</b> <code>{clean_html(trx_id)}</code>\n"
        + pe("6105060531092069509", "⌛") + f" <b>Time:</b> {now_bd()}\n\n"
        + pe(DEPOSIT_BALANCE_EMOJI, "💰") + f" <b>User Balance:</b> {new_balance:.2f} BDT\n"
        + pe(ADMIN_TOTAL_DEPOSIT_EMOJI, "💵") + f" <b>Total Deposit:</b> {float(u['total_deposit']):.2f} BDT\n"
        "━━━━━━━━━━━━━━"
    )
    for aid in admins:
        try:
            await bot.send_message(aid, admin_msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ============================================================
# ELITEPAY WEBHOOK / SUCCESS CALLBACK
# ============================================================

async def elitepay_webhook(request: web.Request):
    """Handle ElitePay success callback/webhook from the supplied gateway flow."""
    try:
        raw = await request.text()
        data = {}
        if raw:
            try:
                data = json.loads(raw)
            except Exception:
                # Also accept the form/query callback style used by many PHP gateways.
                try:
                    from urllib.parse import parse_qs
                    data = {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}
                except Exception:
                    data = {}
        if not isinstance(data, dict):
            data = {}
        # Accept both JSON body and query/form style callbacks.
        for key in ("order_id", "invoice_id", "transaction_id", "trx_id", "status", "payment_status", "payment_method", "method", "amount", "payment_amount"):
            if key not in data and request.query.get(key) is not None:
                data[key] = request.query.get(key)
        if request.query.get("transactionId") and "transaction_id" not in data:
            data["transaction_id"] = request.query.get("transactionId")
        if request.query.get("paymentMethod") and "payment_method" not in data:
            data["payment_method"] = request.query.get("paymentMethod")
        if request.query.get("paymentAmount") and "amount" not in data:
            data["amount"] = request.query.get("paymentAmount")
        if request.query.get("status") and "status" not in data:
            data["status"] = request.query.get("status")

        nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        for k, aliases in {
            "order_id": ("orderId", "orderID", "reference", "merchant_order_id"),
            "transaction_id": ("transactionId", "transactionID", "trxid", "trxId", "invoiceId"),
            "status": ("paymentStatus", "payment_status", "order_status"),
            "amount": ("paymentAmount", "payment_amount"),
            "payment_method": ("paymentMethod", "method"),
        }.items():
            if data.get(k) in (None, ""):
                for alias in aliases:
                    if data.get(alias) not in (None, ""):
                        data[k] = data[alias]
                        break
            if data.get(k) in (None, ""):
                for alias in aliases:
                    if nested.get(alias) not in (None, ""):
                        data[k] = nested[alias]
                        break

        order_id = data.get("order_id") or data.get("reference_id") or request.query.get("order_id")
        if not order_id:
            return web.Response(text="Payment callback received.", content_type="text/plain")

        row = await db.get_zini(str(order_id))
        if not row:
            return web.Response(text="Invalid order.", status=404, content_type="text/plain")
        if row["status"] == "completed":
            return web.Response(text="Payment already processed.", content_type="text/plain")

        status = str(data.get("status") or data.get("payment_status") or "").strip().lower()
        # ElitePay's supplied integration uses the success callback after payment.
        # Only success/completed/paid states are credited.
        if status not in {"success", "successful", "completed", "paid", "true", "1"}:
            return web.Response(text="Payment not completed.", content_type="text/plain")

        # If the gateway returns an amount, require it to match the pending order.
        callback_amount = data.get("amount") or data.get("payment_amount") or data.get("paymentAmount")
        if callback_amount not in (None, ""):
            try:
                if abs(float(callback_amount) - float(row["amount"])) > 0.01:
                    return web.Response(text="Amount mismatch.", status=400, content_type="text/plain")
            except Exception:
                pass

        bot = request.app["telegram_bot"]
        await complete_elitepay_payment(bot, str(order_id), data)
        return web.Response(
            text="<html><body><h3>Payment Successful</h3><p>Your balance has been updated. You can return to Telegram.</p></body></html>",
            content_type="text/html",
        )
    except Exception as e:
        log.exception("ElitePay webhook error")
        return web.Response(text="Callback error.", status=500, content_type="text/plain")



# ============================================================
# ZINIPAY TRANSACTION AUTO CREDIT (ONLY FOR MANUAL TRX FLOW)
# ============================================================

ZINI_TRX_VERIFY_URL = "https://api.zinipay.com/v1/trx/verify"
ZINI_TRX_CONFIRM_URL = "https://api.zinipay.com/v1/trx/confirm"

async def zinipay_trx_request(url: str, payload: dict) -> tuple[int, dict]:
    key = await gateway_key("zini")
    if not key:
        return 0, {"success": False, "message": "ZiniPay API key is not configured."}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "zinipay-api-key": str(key),
                },
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                raw = await resp.text()
                try:
                    data = json.loads(raw) if raw else {}
                except Exception:
                    data = {"message": raw[:1000] if raw else ""}
                return resp.status, data if isinstance(data, dict) else {}
    except Exception as e:
        log.exception("ZiniPay transaction API request failed")
        return 0, {"success": False, "message": str(e)}

async def zini_trx_already_used(trxid: str) -> bool:
    rows = await db.execute(
        "SELECT id FROM deposits WHERE UPPER(TRIM(trxid))=UPPER(TRIM(?)) LIMIT 1",
        (trxid,),
        True,
    )
    return bool(rows)

async def handle_zinipay_manual_trx(update, context):
    """Verify the entered TrxID through ZiniPay and credit automatically.
    This changes only the final manual TrxID verification step; the rest of
    the existing Add Balance/ZiniPay UI remains untouched.
    """
    trx = (update.message.text or "").strip()
    amount = float(context.user_data.get("deposit_amount", 0) or 0)
    method = str(context.user_data.get("manual_method", "bKash")).strip().lower()

    if not trx or not amount or not method:
        await update.message.reply_text("❌ Payment session invalid. আবার Add Balance শুরু করুন।")
        context.user_data.clear()
        return

    if await zini_trx_already_used(trx):
        await update.message.reply_text(
            "❌ <b>এই Transaction ID আগে থেকেই ব্যবহার করা হয়েছে।</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        "⌛ <b>Payment verify করা হচ্ছে...</b>\n\nPlease wait...",
        parse_mode=ParseMode.HTML,
    )

    verify_status, verify = await zinipay_trx_request(
        ZINI_TRX_VERIFY_URL,
        {"transactionId": trx, "amount": float(amount)},
    )

    if verify_status >= 400 or verify.get("success") is not True:
        msg = verify.get("message") or verify.get("error") or "Transaction verification failed."
        await update.message.reply_text(
            "❌ <b>Payment verify failed.</b>\n\n"
            f"<b>ZiniPay:</b> <code>{clean_html(str(msg)[:1000])}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [btn("Cancel", "danger", "cross", callback_data="cancel_add_balance")]
            ]),
        )
        return

    vdata = verify.get("data") if isinstance(verify.get("data"), dict) else {}
    internal_id = vdata.get("id")
    verified_amount = float(vdata.get("amount") or 0)
    verified_trx = str(vdata.get("trxID") or vdata.get("transactionId") or trx).strip()
    provider = str(vdata.get("provider") or method).strip()

    if not internal_id:
        await update.message.reply_text(
            "❌ <b>ZiniPay verify response-এ confirmation ID পাওয়া যায়নি।</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    if abs(verified_amount - amount) > 0.01:
        await update.message.reply_text(
            "❌ <b>Payment amount mismatch.</b>\n\n"
            f"আপনি দিয়েছেন: <b>৳{amount:.2f}</b>\n"
            f"ZiniPay পেয়েছে: <b>৳{verified_amount:.2f}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    got = provider.lower().replace(" ", "")
    expected = method.lower().replace(" ", "")
    if expected and expected not in got:
        await update.message.reply_text(
            "❌ <b>Payment Method mismatch.</b>\n\n"
            f"আপনি নির্বাচন করেছেন: <b>{clean_html(method.title())}</b>\n"
            f"ZiniPay provider: <b>{clean_html(provider)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    confirm_status, confirm = await zinipay_trx_request(
        ZINI_TRX_CONFIRM_URL,
        {
            "transactionId": verified_trx,
            "amount": float(amount),
            "id": internal_id,
            "invoiceId": f"BOT_{update.effective_user.id}_{int(time.time()*1000)}",
        },
    )

    if confirm_status >= 400 or confirm.get("success") is not True:
        msg = confirm.get("message") or confirm.get("error") or "Transaction confirmation failed."
        await update.message.reply_text(
            "❌ <b>Payment confirm failed.</b>\n\n"
            f"<b>ZiniPay:</b> <code>{clean_html(str(msg)[:1000])}</code>\n\n"
            "Balance add করা হয়নি।",
            parse_mode=ParseMode.HTML,
        )
        return

    cdata = confirm.get("data") if isinstance(confirm.get("data"), dict) else {}
    final_amount = float(cdata.get("amount") or verified_amount or amount)
    final_trx = str(cdata.get("transactionId") or verified_trx).strip()
    final_provider = str(cdata.get("provider") or provider).strip()

    if await zini_trx_already_used(final_trx):
        await update.message.reply_text(
            "❌ <b>এই Transaction ID আগে থেকেই ব্যবহার করা হয়েছে।</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    await db.add_balance(update.effective_user.id, final_amount)
    await db.add_total_deposit(update.effective_user.id, final_amount)
    await db.save_deposit(update.effective_user.id, final_amount, final_provider.upper(), final_trx)

    current = await db.balance(update.effective_user.id)
    await update.message.reply_text(
        "✅ <b>Payment Confirmed!</b>\n\n"
        f"💰 Added: <b>৳{final_amount:.2f}</b>\n"
        f"🏦 Method: <b>{clean_html(final_provider.upper())}</b>\n"
        f"🆔 TrxID: <code>{clean_html(final_trx)}</code>\n\n"
        f"💳 নতুন Balance: <b>৳{current:.2f}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(await is_admin(update.effective_user.id)),
    )

    admin_text = (
        "⚡ <b>Auto Payment Confirmed</b>\n\n"
        f"👤 User ID: <code>{update.effective_user.id}</code>\n"
        f"💰 Amount: ৳{final_amount:.2f}\n"
        f"🏦 Method: {clean_html(final_provider.upper())}\n"
        f"🆔 TrxID: <code>{clean_html(final_trx)}</code>\n"
        "✅ Balance automatically credited by ZiniPay."
    )
    for aid in await db.admin_ids():
        try:
            await context.bot.send_message(aid, admin_text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    context.user_data.clear()


# ============================================================
# MANUAL DEPOSIT
# ============================================================

async def manual_deposit_amount(update, context, amount):
    method=context.user_data.get("manual_method","bKash")
    num=await manual_method_number(method.lower())
    context.user_data["deposit_step"]="manual_trx"; context.user_data["deposit_amount"]=amount
    await update.message.reply_text(f"📧 <b>Please enter your Transaction ID (TrxID):</b>\n\nMethod: <b>{clean_html(method.title())}</b>\nSend To: <code>{clean_html(num)}</code>\nAmount: <b>{amount:.2f} BDT</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="cancel_add_balance")]]))

async def handle_manual_trx(update, context):
    return await handle_zinipay_manual_trx(update, context)



# ============================================================
# PAYMENT MANAGEMENT / MANUAL DEPOSIT
# ============================================================

async def manual_method_enabled(method):
    return await setting_bool(f"manual:{method}:enabled", True)

async def manual_method_number(method):
    return await db.get_setting(f"manual:{method}:number", "")

async def manual_payment_menu(q):
    rows = []
    icons = {"bkash": "bkash", "nagad": "nagad", "rocket": "rocket_method", "binance": "binance_method"}
    for method, label in [("bkash", "bKash"), ("nagad", "Nagad"), ("rocket", "Rocket"), ("binance", "Binance")]:
        if await manual_method_enabled(method):
            rows.append([btn(label, "success" if method != "binance" else "primary", icons[method], callback_data=f"manualmethod:{method}")])
    rows.append([btn("Cancel", "danger", "cross", callback_data="cancel_add_balance")])
    header = pe("5318845125219077511", "💳")
    await q.message.edit_text(header + " <b>Select Payment Method:</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))

async def manual_method_start(q, context, method):
    if method=="binance":
        context.user_data["deposit_step"]="manual_binance_amount"
        await q.message.edit_text("🟡 <b>Binance Deposit</b>\n\nEnter the BDT amount. It will be converted to USDT using the admin-set rate.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="cancel_add_balance")]])); return
    num=await manual_method_number(method)
    if not num:
        await q.answer("This payment method is not configured.",show_alert=True); return
    context.user_data["deposit_step"]="manual_amount"; context.user_data["manual_method"]=method
    await q.message.edit_text(f"💳 <b>Method: {method.title()}</b>\n🏷 <b>Send To:</b> <code>{clean_html(num)}</code>\n\n💰 <b>Enter Amount (BDT):</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="cancel_add_balance")]]))

async def create_manual_deposit_request(update, context, method, amount, trx):
    u=update.effective_user
    order_id="MD"+str(int(time.time()*1000))+str(u.id)[-6:]
    sent=await update.message.reply_text("⏳ <b>Checking your payment... Please wait up to 1 minute.</b>",parse_mode=ParseMode.HTML)
    await db.set_pending_deposit(u.id,amount,method=method,trxid=trx,msg_id=sent.message_id)
    name=((u.first_name or "")+" "+(u.last_name or "")).strip() or "N/A"
    uname="@"+u.username if u.username else "N/A"
    admin_msg=(pe(ADMIN_NEW_DEPOSIT_EMOJI,"🎉")+" <b>New Manual Deposit Request</b>\n\n"
      f"👤 <b>Name:</b> {clean_html(name)}\n🔗 <b>Username:</b> {clean_html(uname)}\n🆔 <b>User ID:</b> <code>{u.id}</code>\n\n"
      f"💰 <b>Amount:</b> {amount:.2f} BDT\n💳 <b>Method:</b> {clean_html(method)}\n🧾 <b>Transaction ID:</b> <code>{clean_html(trx)}</code>\n📅 <b>Date & Time:</b> {now_bd()}\n\n"
      "Choose an action below:")
    kb=InlineKeyboardMarkup([[btn("Approve","success","check3",callback_data=f"manual:approve:{u.id}"),btn("Rejected","danger","cross",callback_data=f"manual:reject:{u.id}")]])
    for aid in await db.admin_ids():
        try: await context.bot.send_message(aid,admin_msg,parse_mode=ParseMode.HTML,reply_markup=kb)
        except Exception: pass

async def approve_manual_callback(q, context, uid):
    pending=await db.get_pending_deposit(uid); row=await db.get_user(uid)
    if not pending or not row: await q.answer("Request not found or already processed.",show_alert=True); return
    amount=float(pending["amount"]); method=pending["method"] or "Manual"; trx=pending["trxid"] or "N/A"
    if pending["pending_message_id"]:
        try: await context.bot.delete_message(uid,pending["pending_message_id"])
        except Exception: pass
    await db.add_balance(uid,amount); await db.add_total_deposit(uid,amount); await db.save_deposit(uid,amount,method,trx); await db.clear_pending_deposit(uid)
    current=await db.balance(uid)
    await db.set_setting("last_manual_approval",now_bd())
    await q.answer("Approved",show_alert=True)
    try: await q.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    try: await context.bot.send_message(uid, f"✅ <b>Deposit Approved</b>\n\nYour payment of <b>{amount:.2f} BDT</b> has been approved successfully.\n\n💰 <b>Current Balance:</b> {current:.2f} BDT\n🧾 <b>Transaction ID:</b> <code>{clean_html(trx)}</code>",parse_mode=ParseMode.HTML)
    except Exception: pass

async def reject_manual_callback(q, context, uid):
    pending=await db.get_pending_deposit(uid)
    if not pending: await q.answer("Request not found or already processed.",show_alert=True); return
    if pending["pending_message_id"]:
        try: await context.bot.delete_message(uid,pending["pending_message_id"])
        except Exception: pass
    await db.clear_pending_deposit(uid); await q.answer("Rejected",show_alert=True)
    try: await q.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    try: await context.bot.send_message(uid,"❌ <b>Deposit Rejected</b>\n\nYour deposit request was rejected by the administrator. If you believe this was a mistake, please contact support.",parse_mode=ParseMode.HTML)
    except Exception: pass

# ============================================================
# PAYMENT HISTORY
# ============================================================

async def payment_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await banned_guard(update):
        return
    u = update.effective_user
    deps = await db.deposits(u.id)
    row = await db.get_user(u.id)
    total = float(row["total_deposit"]) if row else 0.0

    if not deps:
        text = (
            pe("6206110936789423908", "❌")
            + " <b>আপনার কোনো পেমেন্ট/ডিপোজিট হিস্ট্রি পাওয়া যায়নি!</b>\n\n"
            + pe("6269136309093143551", "💸")
            + " <b>সর্বমোট ডিপোজিট:</b> 0.00 ৳"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    msg = (
        pe("6104734070627900577", "🛒")
        + " <b>আপনার পেমেন্ট / ডিপোজিট হিস্ট্রি</b>\n\n"
        + pe("6269136309093143551", "💸")
        + f" <b>সর্বমোট ডিপোজিট:</b> {total:.2f} ৳\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    for i, item in enumerate(deps, 1):
        msg += (
            f"{i}. {pe('6107034055679680789','✅')} <b>পরিমাণ:</b> {item['amount']} ৳\n"
            f"   {pe('6107069665253530336','💳')} <b>মেথড:</b> {clean_html(item['method'])}\n"
            f"   {pe('6156731345599995679','🆔')} <b>TrxID:</b> <code>{clean_html(item['trxid'] or 'N/A')}</code>\n"
            f"   {pe('6107003883534425579','📅')} <b>তারিখ:</b> {clean_html(item['created_at'])}\n\n"
        )

    await send_long(update.message, msg, ParseMode.HTML)


# ============================================================
# PURCHASE HISTORY - UNLIMITED
# ============================================================

async def my_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await banned_guard(update):
        return
    u = update.effective_user
    orders = await db.orders(u.id)

    if not orders:
        text = (
            pe("6206110936789423908", "❌")
            + " <b>আপনার কোনো পূর্ববর্তী কেনাকাটার রেকর্ড পাওয়া যায়নি!</b>\n\n"
            + pe("6203738495639360972", "💯")
            + " <i>আমাদের শপ থেকে কোনো প্রক্সি বা ভিপিএন কিনলে তার ডিটেইলস এখানে দেখতে পাবেন।</i>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    msg = (
        pe("6104734070627900577", "🛒")
        + " <b>আপনার পারচেজ হিস্ট্রি (My Purchases):</b>\n"
        + pe("5332560480508591604", "🤖")
        + f" <b>মোট কেনাকাটা:</b> <code>{len(orders)} টি</code>\n"
        "━━━━━━━━━━━━━━\n\n"
    )

    for i, item in enumerate(orders, 1):
        msg += (
            f"{i}. {pe('6156731345599995679','🔑')} <b>{clean_html(item['product_name'])}</b>\n"
            f"{pe('6107069665253530336','ℹ️')} <b>Order ID:</b> <code>{item['id']}</code>\n"
            f"{pe('6156731345599995679','🔑')} <b>Key / Details:</b>\n"
            f"<code>{clean_html(item['key_text'])}</code>\n"
            f"📅 <b>তারিখ ও সময়:</b> <code>{clean_html(item['created_at'])}</code>\n"
            "-----------------------------------\n"
        )

    await send_long(update.message, msg, ParseMode.HTML)


async def send_long(message, text, parse_mode=None):
    # Telegram message limit is ~4096 chars.
    # Split at safe boundaries; no 10-item history limit.
    while len(text) > 3800:
        cut = text.rfind("\n", 0, 3800)
        if cut < 1000:
            cut = 3800
        await message.reply_text(text[:cut], parse_mode=parse_mode)
        text = text[cut:]
    if text:
        await message.reply_text(text, parse_mode=parse_mode)


# ============================================================
# PROXY CHECKER
# ============================================================

async def proxy_checker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await banned_guard(update):
        return
    msg = "🧩 <b>Checker Menu</b>\nযেটাতে যেতে চান, সেটাতে ক্লিক করুন:"
    kb = InlineKeyboardMarkup([
        [btn("9proxy cheker bot", "primary", "link", url="https://t.me/Proxy9gb_bot")],
        [btn("Abc gb checker bot", "primary", "link", url="https://t.me/Abcgb_bot")],
        [btn("Dataimpluse checker bot", "primary", "link", url="https://t.me/DataImpluseGB_Bot")],
        [btn("Rapid gb checker", "primary", "link", url="https://t.me/RapidProxyGB_Bot")],
        [btn("Cli proxy gb checker", "primary", "link", url="https://t.me/CliproxyGBbot")],
        [btn("Mesh proxy gb checker", "primary", "link", url="https://t.me/MeshProxyGB_bot")],
        [btn("Rocket proxy GB checker", "primary", "link", url="https://t.me/RocketProxyGB_bot")],
    ])
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb)


# ============================================================
# SUPPORT
# ============================================================

async def get_support_admins():
    raw = await db.get_setting("support_admins", None)
    if raw is None:
        # Backward-compatible default until the admin explicitly manages support.
        return [SUPPORT_USERNAME]
    try:
        vals = json.loads(raw)
        if isinstance(vals, list):
            return [str(x).strip() for x in vals if str(x).strip()]
    except Exception:
        pass
    return []


def support_url(value: str) -> str:
    value = str(value).strip()
    if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
        return value
    return "https://t.me/" + value.lstrip("@/")


async def save_support_admins(values):
    clean = []
    for value in values:
        value = str(value).strip()
        if not value:
            continue
        if value.startswith("https://t.me/"):
            display = "@" + value.rstrip("/").rsplit("/", 1)[-1]
        else:
            display = value if value.startswith("@") else "@" + value
        if display not in clean:
            clean.append(display)
    await db.set_setting("support_admins", json.dumps(clean, ensure_ascii=False))
    return clean


async def primary_support_url() -> str:
    admins = await get_support_admins()
    return support_url(admins[0]) if admins else SUPPORT_URL


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await banned_guard(update):
        return
    admins = await get_support_admins()
    lines = []
    rows = []
    for i, admin in enumerate(admins, 1):
        lines.append(f"{i}. {clean_html(admin)}")
        rows.append([btn(f"Contact {admin}", "primary", "chat", url=support_url(admin))])
    msg = (
        pe("5332560480508591604", "🤖") + " <b>Support Center</b>\n"
        "━━━━━━━━━━━━━━\n"
        "যেকোনো সমস্যার জন্য আমাদের Support Admin-এর সাথে যোগাযোগ করুন।\n\n"
        + "\n".join(lines) + "\n\n"
        + pe("6084602502349595551", "🚀") + " যত দ্রুত সম্ভব আপনাকে সহায়তা করা হবে।"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))


# ============================================================
# BUY MAIL
# ============================================================

async def send_mail_menu(message, edit=False):
    rows=[]
    for p in await db.products():
        if product_kind(p)!="mail" or p["hidden"]: continue
        rows.append([btn(f"{p['name']} — {float(p['price']):.2f}৳","success",product_emoji_id(p),callback_data=f"mail:{p['key']}")])
    rows.append([btn("Main Menu","danger","main_menu",callback_data="main_menu")])
    text="📧 <b>Select Mail Type</b>\n\nChoose the mail product you want to buy."
    if edit:
        try: await message.edit_text(text,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows)); return
        except Exception: pass
    await message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def buy_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await banned_guard(update): return
    await send_mail_menu(update.effective_message)

async def mail_product_menu_callback(update, context, key):
    q=update.callback_query; p=await db.get_product(key)
    if not p or product_kind(p)!="mail" or p["hidden"]:
        await q.answer("Mail product unavailable.",show_alert=True); return
    await q.answer(); stock=await db.stock_count(key); price=float(p["price"]); bal=await db.balance(q.from_user.id)
    if price<=0:
        await q.message.edit_text(product_title_with_emoji(p)+"\n\n❌ <b>Price is not configured yet.</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Back","danger","main_menu",callback_data="buy_mail")]])); return
    rows=[[btn("Buy Mail","success","cart",callback_data=f"mailqty:{key}:1")]] if stock>0 else [[btn("Stock Out","danger","cross",callback_data="ignore")]]
    rows.append([btn("Back","danger","main_menu",callback_data="buy_mail")])
    text=product_title_with_emoji(p)+f"\n\n💰 <b>Price:</b> {price:.2f}৳\n📦 <b>Available Stock:</b> {stock}\n💳 <b>Your Balance:</b> {bal:.2f}৳"
    await q.message.edit_text(text,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def show_mail_quantity(q,key,qty=1):
    p=await db.get_product(key)
    if not p or product_kind(p)!="mail" or p["hidden"]: await q.answer("Mail unavailable.",show_alert=True); return
    stock=await db.stock_count(key)
    if stock<1: await q.answer("Stock Out",show_alert=True); return
    qty=max(1,min(int(qty),stock)); price=float(p["price"]); total=price*qty
    text=pe("6104734070627900577","🛍")+" <b>Mail Purchase Summary</b>\n\n"+product_title_with_emoji(p)+f"\n📦 <b>Available Stock:</b> {stock}\n🔢 <b>Quantity:</b> {qty}\n💵 <b>Rate:</b> {price:.2f}৳\n💰 <b>Total:</b> {total:.2f}৳"
    rows=[[btn("−","danger",callback_data=f"mailqty:{key}:{max(1,qty-1)}"),btn(str(qty),"primary","cart",callback_data="ignore"),btn("+","success",callback_data=f"mailqty:{key}:{min(stock,qty+1)}")],[btn("Enter Quantity","primary","cart",callback_data=f"mailqtyenter:{key}")],[btn("Back","primary","main_menu",callback_data="buy_mail"),btn("Cancel","danger","cross",callback_data="main_menu")],[btn("Confirm","success","check3",callback_data=f"mailbuy:{key}:{qty}")]]
    try: await q.message.edit_text(text,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))
    except Exception: await q.message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def mail_quantity_enter(q,context,key):
    await q.answer(); context.user_data["mail_qty_key"]=key; context.user_data["mail_qty_input"]=True
    await q.message.reply_text("🔢 <b>Enter mail quantity</b>\nশুধু সংখ্যা লিখুন।",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="mail_qty_cancel")]]))

async def mail_buy_confirm(q,context,key,qty):
    p=await db.get_product(key)
    if not p or product_kind(p)!="mail" or p["hidden"]: await q.answer("Mail unavailable.",show_alert=True); return
    stock=await db.stock_count(key); qty=int(qty)
    if qty<1 or qty>stock: await q.answer(f"Stock available: {stock}",show_alert=True); return
    total=float(p["price"])*qty; bal=await db.balance(q.from_user.id)
    if bal<total:
        await q.message.reply_text("❌ <b>Your balance is insufficient.</b>\nPlease add balance first.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Add Balance","success","cash",callback_data="add_balance")],[btn("Back","danger","main_menu",callback_data="buy_mail")]])); return
    items=await db.pop_stock(key,qty)
    if len(items)!=qty: await q.answer("Stock changed. Please try again.",show_alert=True); return
    await db.add_balance(q.from_user.id,-total); oid=order_id(); await db.save_order(oid,q.from_user.id,key,p["name"],total,qty,"\n\n".join(items)); bal_after=await db.balance(q.from_user.id)
    try: await q.message.delete()
    except Exception: pass
    if qty>=5:
        data=(f"{p['name']}\nQuantity: {qty}\nOrder ID: {oid}\n\n"+"\n\n".join(f"{i}# {x.strip()}" for i,x in enumerate(items,1))).encode("utf-8")
        doc=InputFile(io.BytesIO(data),filename=f"{re.sub(r'[^A-Za-z0-9_-]+','_',p['name'])}_{qty}pcs.txt")
        await q.get_bot().send_message(q.from_user.id,pe("6269163801178804220","✅")+" <b>Purchase Successful</b>\n━━━━━━━━━━━━━━━━━━\n\n"+product_title_with_emoji(p)+f"\n💵 <b>Price:</b> {total:.2f}৳\n💳 <b>Your Balance:</b> {bal_after:.2f}৳\n📦 <b>Quantity:</b> {qty}\n📄 <b>Mail details are attached in the file.</b>",parse_mode=ParseMode.HTML)
        await q.get_bot().send_document(q.from_user.id,document=doc,caption="📧 <b>Mail Delivery File</b>",parse_mode=ParseMode.HTML)
    else:
        parts=[f"<b>{i}#</b> <code>{clean_html(x.strip())}</code>" for i,x in enumerate(items,1)]
        await q.get_bot().send_message(q.from_user.id,pe("6269163801178804220","✅")+" <b>Purchase Successful</b>\n━━━━━━━━━━━━━━━━━━\n\n"+product_title_with_emoji(p)+f"\n💵 <b>Price:</b> {total:.2f}৳\n💳 <b>Your Balance:</b> {bal_after:.2f}৳\n\n"+"\n\n".join(parts),parse_mode=ParseMode.HTML)
    await q.answer("Mail delivered successfully.",show_alert=True)

# ============================================================
# BUY VPN MENUS

VPN_EMOJI_FALLBACK = {
    "vpn_nord_7": "vpn_nord", "vpn_nord_30": "vpn_nord",
    "vpn_proton_14": "vpn_proton", "vpn_proton_30": "vpn_proton",
    "vpn_express_3": "vpn_express",
    "vpn_surfshark_7": "vpn_surfshark",
    "vpn_hma_7": "vpn_hma",
    "vpn_bitdefender_6": "vpn_bitdefender",
}

def vpn_product_title(key: str) -> str:
    p = None
    # This helper remains synchronous for existing call sites; static fallback.
    if key in VPN_PRODUCTS:
        p = VPN_PRODUCTS[key]
        return pe(p["emoji_id"], p["emoji"]) + " " + clean_html(p["name"])
    return pe(VPN_HEADER_EMOJI, "🛡") + " " + clean_html(key)

def vpn_button_icon(key: str) -> str:
    return VPN_EMOJI_FALLBACK.get(key, "vpn_shop")

async def buy_vpn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await banned_guard(update):
        return
    await send_vpn_menu(update.effective_message)

async def send_vpn_menu(message, edit=False):
    rows = []
    for p in await db.products():
        if product_kind(p) != "vpn" or p["hidden"]:
            continue
        icon_id = product_emoji_id(p)
        # Inline buttons support custom emoji icons only through the named map;
        # use a stable fallback icon key for dynamically created VPNs.
        label = f"{p['name']} - {float(p['price']):.1f}৳"
        rows.append([btn(label, "primary", product_emoji_id(p), callback_data=f"vpn:{p['key']}")])
    rows.append([btn("Back to Main Menu", "danger", "main_menu", callback_data="main_menu")])
    text = pe(VPN_HEADER_EMOJI, "🛍") + " <b>:- Available Products:</b>"
    kb = InlineKeyboardMarkup(rows)
    if edit:
        try:
            await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb); return
        except Exception: pass
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def vpn_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    q = update.callback_query
    await q.answer()
    p = await db.get_product(key)
    if not p or product_kind(p) != "vpn":
        await q.answer("❌ VPN product পাওয়া যায়নি।", show_alert=True)
        return
    if not p or p["hidden"]:
        await q.answer("❌ এই VPN service বর্তমানে hidden।", show_alert=True)
        return

    price = float(p["price"])
    balance = await db.balance(q.from_user.id)
    manual_mode = str(p["delivery_mode"] or "auto") == "manual"
    stock = await db.stock_count(key)

    # Priority is always balance first. Even when stock is empty, a user with
    # insufficient balance must be asked to deposit before stock status is shown.
    if balance < price:
        duration = clean_html(duration_for_vpn(p))
        text = (
            product_title_with_emoji(p) + "\n\n"
            "╭ - - - - - - - - - - - - - - - ╮\n"
            f"┊ <b>Product:</b> {clean_html(p['name'])}\n"
            f"┊ <b>Package:</b> {duration}\n"
            f"┊ <b>Price:</b> {price:.1f}৳\n"
            "╰ - - - - - - - - - - - - - - - ╯\n\n"
            "╭ - - - - - - - - - - - - - - - ╮\n"
            "┊ <b>Quantity:</b> 1\n"
            f"┊ <b>Duration:</b> {duration}\n"
            f"┊ <b>Unit Price:</b> {price:.1f}৳\n"
            f"┊ <b>Total Price:</b> {price:.1f}৳\n"
            "╰ - - - - - - - - - - - - - - - ╯\n\n"
            "❌ <b>আপনার balance পর্যাপ্ত নয়!</b>\nআগে Add Balance করুন।"
        )
        kb = InlineKeyboardMarkup([
            [btn("Add Balance", "primary", "cash", callback_data="add_balance")],
            [btn("Back", "danger", "main_menu", callback_data="buy_vpn")],
        ])
    elif manual_mode:
        # Manual delivery does not consume the automatic stock. It must remain
        # purchasable even when the automatic stock table is empty.
        duration = clean_html(duration_for_vpn(p))
        text = (
            product_title_with_emoji(p) + "\n\n"
            "╭ - - - - - - - - - - - - - - - ╮\n"
            f"┊ <b>Product:</b> {clean_html(p['name'])}\n"
            f"┊ <b>Package:</b> {duration}\n"
            f"┊ <b>Price:</b> {price:.1f}৳\n"
            "╰ - - - - - - - - - - - - - - - ╯\n\n"
            "╭ - - - - - - - - - - - - - - - ╮\n"
            "┊ <b>Quantity:</b> 1\n"
            f"┊ <b>Duration:</b> {duration}\n"
            f"┊ <b>Unit Price:</b> {price:.1f}৳\n"
            f"┊ <b>Total Price:</b> {price:.1f}৳\n"
            "╰ - - - - - - - - - - - - - - - ╯\n\n"
            "🛠 <b>Manual Delivery</b>\nAdmin will prepare and deliver your VPN after the order is placed."
        )
        kb = InlineKeyboardMarkup([
            [btn("Buy Now", "success", "cart", callback_data=f"vpn_buy:{key}")],
            [btn("Add Balance", "primary", "cash", callback_data="add_balance")],
            [btn("Back", "danger", "main_menu", callback_data="buy_vpn")],
        ])
    elif stock < 1:
        text = (
            pe(p["emoji_id"], p["emoji"]) + f" <b>{clean_html(p['name'])}</b>\n\n"
            "❌ <b>Stock Out</b>\n\n"
            "এই VPN service-এর stock বর্তমানে শেষ।\n"
            "Please contact Support and try again later."
        )
        support_admins = await get_support_admins()
        support_rows = ([[btn("Contact Support", "primary", "chat", url=await primary_support_url())]]
                        if support_admins else [])
        kb = InlineKeyboardMarkup(
            support_rows + [[btn("Back", "danger", "main_menu", callback_data="buy_vpn")]]
        )
    else:
        duration = clean_html(duration_for_vpn(p))
        text = (
            product_title_with_emoji(p) + "\n\n"
            "╭ - - - - - - - - - - - - - - - ╮\n"
            f"┊ <b>Product:</b> {clean_html(p['name'])}\n"
            f"┊ <b>Package:</b> {duration}\n"
            f"┊ <b>Price:</b> {price:.1f}৳\n"
            "╰ - - - - - - - - - - - - - - - ╯\n\n"
            "╭ - - - - - - - - - - - - - - - ╮\n"
            "┊ <b>Quantity:</b> 1\n"
            f"┊ <b>Duration:</b> {duration}\n"
            f"┊ <b>Unit Price:</b> {price:.1f}৳\n"
            f"┊ <b>Total Price:</b> {price:.1f}৳\n"
            "╰ - - - - - - - - - - - - - - - ╯\n\n"
            "Select quantity and click <b>Buy Now</b>."
        )
        kb = InlineKeyboardMarkup([
            [btn("Buy Now", "success", "cart", callback_data=f"vpn_buy:{key}")],
            [btn("Add Balance", "primary", "cash", callback_data="add_balance")],
            [btn("Back", "danger", "main_menu", callback_data="buy_vpn")],
        ])

    try:
        await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

def parse_vpn_credentials(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return "", ""

    email = password = ""
    email_patterns = [
        r"(?:Gmail|Email|E-mail)\s*(?:➜|→|:|=)\s*([^\r\n]+)",
        r"(?:Gmail|Email|E-mail)\s+([^\s]+@[^\s]+)",
    ]
    pass_patterns = [
        r"(?:Password|Pass|PW)\s*(?:➜|→|:|=)\s*([^\r\n]+)",
        r"(?:Password|Pass|PW)\s+([^\s]+)",
    ]
    for pat in email_patterns:
        m = re.search(pat, raw, re.I)
        if m:
            email = m.group(1).strip()
            break
    for pat in pass_patterns:
        m = re.search(pat, raw, re.I)
        if m:
            password = m.group(1).strip()
            break

    if not email or not password:
        lines = [x.strip() for x in raw.splitlines() if x.strip()]
        compact = lines[0] if len(lines) == 1 else raw
        parts = re.split(r"\s*(?:\||;|,)\s*", compact, maxsplit=1)
        if len(parts) == 2 and "@" in parts[0]:
            email, password = parts[0].strip(), parts[1].strip()
        elif len(lines) == 1 and ":" in compact:
            left, right = compact.split(":", 1)
            if "@" in left:
                email, password = left.strip(), right.strip()

    if not email:
        m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw)
        if m:
            email = m.group(0)
    return email, password

async def buy_vpn_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    q = update.callback_query
    await q.answer()
    p = await db.get_product(key)
    if not p or product_kind(p) != "vpn":
        return
    if not p or p["hidden"]:
        await q.answer("❌ এই VPN service hidden।", show_alert=True)
        return

    price = float(p["price"])
    balance = await db.balance(q.from_user.id)
    if balance < price:
        await q.answer("❌ Balance পর্যাপ্ত নয়!", show_alert=True)
        return
    if str(p["delivery_mode"] or "auto")=="manual":
        await create_manual_product_order(q,key,1); return

    if await db.stock_count(key) < 1:
        await q.answer("❌ Stock Out!", show_alert=True)
        return

    items = await db.pop_stock(key, 1)
    if len(items) != 1:
        await q.answer("❌ Stock পাওয়া যায়নি।", show_alert=True)
        return

    item = items[0]
    await db.add_balance(q.from_user.id, -price)
    oid = order_id()
    await db.save_order(oid, q.from_user.id, key, p["name"], price, 1, item)

    # Delete the VPN service/details message before sending the account.
    try:
        await q.message.delete()
    except Exception:
        pass

    email, password = parse_vpn_credentials(item)
    if not email:
        email = item.strip()
    if not password:
        password = "See account details above"

    delivery = (
        "╭ - - - - - - - - - - - - - - - - - - - - - - -  ╮\n"
        f"┊  {product_title_with_emoji(p)} {pe('6269163801178804220', '✅')}\n"
        "┊\n"
        f"┊  {pe('6206495649895028694', '📧')} <b>Gmail➜</b> <code>{clean_html(email)}</code>\n"
        "┊\n"
        f"┊  {pe('6269368817147711866', '🔐')} <b>Password➜</b> <code>{clean_html(password)}</code>\n"
        "┊\n"
        "╰ - - - - - - - - - - - - - - - - - - - - - - -  ╯"
    )
    copy_kb = InlineKeyboardMarkup([
        [btn("Copy Gmail", "primary", "chat", copy_text=CopyTextButton(text=email))],
        [btn("Copy Password", "primary", "tag", copy_text=CopyTextButton(text=password))],
        [btn("Buy Another VPN", "primary", "buy_vpn", callback_data="buy_vpn")],
        [btn("Main Menu", "danger", "main_menu", callback_data="main_menu")],
    ])
    await q.get_bot().send_message(
        q.from_user.id, delivery, parse_mode=ParseMode.HTML, reply_markup=copy_kb
    )

    # Admin sale notification.
    uname = f"@{q.from_user.username}" if q.from_user.username else "No Username"
    stock_left = await db.stock_count(key)
    admin_msg = (
        "🛒 <b>নতুন VPN Sale!</b>\n\n"
        f"👤 <b>User:</b> {clean_html(uname)}\n"
        f"🆔 <b>ID:</b> <code>{q.from_user.id}</code>\n"
        f"📦 <b>Product:</b> {clean_html(p['name'])}\n"
        f"💰 <b>Amount:</b> {price:.2f} ৳\n"
        f"🧾 <b>Order:</b> <code>{oid}</code>\n"
        f"⏰ <b>Time:</b> {now_bd()}\n"
        f"📊 <b>Remaining Stock:</b> {stock_left}"
    )
    for aid in await db.admin_ids():
        try:
            await q.get_bot().send_message(aid, admin_msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass

# ============================================================
# BUY PROXY MENUS
# ============================================================

async def buy_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await banned_guard(update):
        return
    await send_proxy_menu(update.effective_message)


async def send_proxy_menu(message, edit=False):
    rows = []
    for p in await db.products():
        if product_kind(p) not in ("proxy", "multi_proxy", "key") or p["hidden"]:
            continue
        label = f"{product_menu_name(p)} — {float(p['price']):.0f}৳"
        rows.append([btn(label, "success", product_emoji_id(p), callback_data=f"product:{p['key']}")])
    rows.append([btn("Main Menu", "danger", "main_menu", callback_data="main_menu")])
    kb = InlineKeyboardMarkup(rows)
    text = pe("5355208818017999139", "📱") + " <b>আপনি কোন Proxy কিনতে চান? নিচে থেকে নির্বাচন করুন:</b>"
    if edit:
        try:
            await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def promo_apply_start(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    q = update.callback_query
    p = await db.get_product(key)
    if not p or p["hidden"]:
        await q.answer("⚠️ এই Proxy বর্তমানে unavailable.", show_alert=True)
        return
    row = await db.promo_get(key)
    if not row or not row["enabled"]:
        await q.answer("❌ এই Proxy-তে এখন কোনো Promo Code চালু নেই।", show_alert=True)
        return
    await q.answer()
    context.user_data["deposit_step"] = None
    context.user_data["promo_wait"] = key
    await q.message.reply_text(
        pe("5235989279024373566", "🎰") + " <b>Promo Code দিন</b>\n\nআপনার Promo Code লিখে পাঠান:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[btn("Cancel", "danger", "cross", callback_data="promo:cancel")]]),
    )


async def promo_apply_submit(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    key = context.user_data.get("promo_wait")
    p = await db.get_product(key) if key else None
    row = await db.promo_get(key) if key else None
    if not p or not row or not row["enabled"]:
        context.user_data.pop("promo_wait", None)
        await update.message.reply_text(
            "❌ <b>এই Promo Code এখন আর চালু নেই।</b>", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Back", "danger", "main_menu", callback_data="buy_proxy")]]),
        )
        return
    if code.strip().lower() != str(row["code"]).lower():
        await update.message.reply_text(
            "❌ <b>Promo Code সঠিক নয়!</b>\n\nআবার চেষ্টা করুন অথবা Cancel চাপুন।", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Cancel", "danger", "cross", callback_data="promo:cancel")]]),
        )
        return
    context.user_data.pop("promo_wait", None)
    context.user_data.setdefault("promo_applied", {})[key] = row["code"]
    pct = float(row["percent"])
    new_price = promo_discount_price(p["price"], pct)
    await update.message.reply_text(
        pe("5235989279024373566", "🎰") + " <b>Promo Code Applied!</b>\n\n"
        + product_title_with_emoji(p) + "\n"
        + f"<b>Discount:</b> {pct:g}%\n"
        + f"<b>Price:</b> <s>{float(p['price']):.2f}৳</s> <b>{new_price:.2f}৳</b> ({usd_from_bdt(new_price)})",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[btn("Continue", "success", "check3", callback_data=f"product:{key}")]]),
    )


async def product_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    q = update.callback_query
    await q.answer()
    p = await db.get_product(key)
    if not p or p["hidden"]:
        await q.answer("⚠️ এই Proxy বর্তমানে unavailable/hidden.", show_alert=True)
        return
    base_price = float(p["price"]); bal = await db.balance(q.from_user.id)
    price, pct, _pcode = await promo_effective(context, key, base_price)
    plan = proxy_size(p)
    checker = p["checker"] if "checker" in p.keys() and p["checker"] else checker_for(key)
    if proxy_style(p) == "Q":
        await show_proxy_quantity(q, context, key, 1)
        return
    rows = []
    if bal < price:
        rows.append([btn("Add Balance", "success", "cash", callback_data="add_balance")])
        status = "❌ <b>আপনার balance পর্যাপ্ত নয়! দয়া করে Add Balance করুন।</b>"
    else:
        rows.append([btn("Confirm Purchase", "success", "6267225207560214192", callback_data=f"buy:{key}")])
        status = "✅ <b>আপনার balance পর্যাপ্ত। সরাসরি balance থেকে কিনতে পারবেন।</b>"
    rows.append([btn("Apply Promo Code", "primary", "5235989279024373566", callback_data=f"promo:apply:{key}")])
    rows.append([btn("Back", "danger", "main_menu", callback_data="buy_proxy")])
    if pct > 0:
        price_text = f"<s>{base_price:.2f}৳</s> {price:.2f}৳ ({usd_from_bdt(price)}) — <b>{pct:g}% OFF</b>"
    else:
        price_text = f"{price:.2f}৳ ({usd_from_bdt(price)})"
    text = (
        product_title_with_emoji(p) + "\n"
        f"<b>Plan:</b> {clean_html(plan)}\n"
        f"<b>Price:</b> {price_text}\n"
        f"<b>Your Balance:</b> {bal:.2f}৳\n\n"
        "<b>কিভাবে Pay করবেন?</b>\n" + status
    )
    try:
        await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
    except Exception:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))


async def show_proxy_quantity(q, context, key, qty=1):
    p = await db.get_product(key)
    if not p or p["hidden"] or product_kind(p) not in ("proxy", "multi_proxy", "key"):
        await q.answer("❌ Proxy unavailable", show_alert=True); return
    manual_mode = str(p["delivery_mode"] or "auto")=="manual"
    stock = await db.stock_count(key)
    if not manual_mode and stock < 1:
        text = (product_title_with_emoji(p) + "\n\n❌ <b>Stock Out</b>")
        kb = InlineKeyboardMarkup([[btn("Back", "danger", "main_menu", callback_data="buy_proxy")]])
        try: await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception: pass
        return
    qty = max(1, int(qty)) if manual_mode else max(1, min(int(qty), stock))
    base_price = float(p["price"]); price, pct, _pcode = await promo_effective(context, key, base_price); total = price * qty
    rate_text = f"{price:.1f}" if pct <= 0 else f"<s>{base_price:.1f}</s> {price:.1f} ({pct:g}% OFF)"
    size = clean_html(proxy_size(p))
    text = (
        pe("6104734070627900577", "🛍") + " <b>Purchase Summary</b>\n\n"
        + pe("6269368817147711866", "🏷") + " <b>Category:</b> Proxy\n"
        + pe(product_emoji_id(p), product_emoji_fallback(p)) + f" <b>Package:</b> {clean_html(p['name'])}\n"
        + pe("6105185226877574701", "🎁") + f" <b>Sub Package:</b> {size}\n\n"
        + pe("6269368817147711866", "🏷") + ("<b>Delivery:</b> Manual Delivery\n" if manual_mode else f"<b>Available Stock:</b> {stock} pcs\n")
        + pe("6267008582294705964", "🔢") + f" <b>Quantity:</b> {qty}\n"
        + pe("6001287064589439895", "💵") + f" <b>Rate:</b> {rate_text} Tk\n"
        + pe("6190336264940559752", "💰") + f" <b>Total Price:</b> {total:.1f} Tk"
    )
    rows = [
        [btn("−", "danger", callback_data=f"pq:{key}:{max(1,qty-1)}"), btn(str(qty), "primary", "cart", callback_data="ignore"), btn("+", "success", callback_data=f"pq:{key}:{qty+1 if manual_mode else min(stock,qty+1)}")],
        [btn("Enter quantity", "primary", "cart", callback_data=f"pqenter:{key}")],
        [btn("Back", "primary", "main_menu", callback_data="buy_proxy"), btn("Cancel", "danger", "cross", callback_data="main_menu")],
        [btn("Confirm", "success", "check3", callback_data=f"pqconfirm:{key}:{qty}")],
    ]
    rows.insert(-1, [btn("Apply Promo Code", "primary", "5235989279024373566", callback_data=f"promo:apply:{key}")])
    try: await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
    except Exception: await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))

async def proxy_quantity_callback(update, context, key, qty):
    q=update.callback_query
    try: qty=int(qty)
    except: qty=1
    await q.answer()
    await show_proxy_quantity(q, context, key, qty)

async def proxy_quantity_enter(update, context, key):
    q=update.callback_query
    await q.answer()
    context.user_data["proxy_qty_key"]=key
    context.user_data["admin_action"]="proxy_quantity_input"
    await q.message.reply_text("🔢 <b>Enter quantity</b>\nকতটি নিতে চান? শুধু সংখ্যা লিখুন।\n/cancel দিয়ে বাতিল করতে পারবেন।", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Cancel", "danger", "cross", callback_data="proxy_qty_cancel")]]))

async def proxy_quantity_confirm(update, context, key, qty):
    q=update.callback_query
    await q.answer()
    p=await db.get_product(key)
    if not p or p["hidden"]: return
    manual_mode=str(p["delivery_mode"] or "auto")=="manual"
    stock=await db.stock_count(key)
    if qty<1:
        await q.answer("❌ Quantity must be at least 1.", show_alert=True); return
    if not manual_mode and qty>stock:
        await q.answer(f"❌ Stock available: {stock}", show_alert=True); return
    unit_price,_pct,_pc=await promo_effective(context,key,float(p["price"]))
    total=unit_price*qty
    bal=await db.balance(q.from_user.id)
    if bal<total:
        await q.message.reply_text("❌ <b>আপনার balance পর্যাপ্ত নয়!</b>\n\nদয়া করে আগে Add Balance করুন।", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Add Balance","success","cash",callback_data="add_balance")],[btn("Back","primary","main_menu",callback_data="buy_proxy")]])); return
    if str(p["delivery_mode"] or "auto")=="manual":
        await create_manual_product_order(q, key, qty, unit_price=unit_price); return
    await deliver_product(q,key,qty,unit_price=unit_price)

async def buy_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    q = update.callback_query
    await q.answer()
    if await db.is_banned(q.from_user.id):
        await q.answer("❌ আপনি ব্যানড!", show_alert=True)
        return

    p = await db.get_product(key)
    if not p or p["hidden"] or product_kind(p) == "vpn":
        await q.answer("❌ এই Proxy বর্তমানে hidden/unavailable.", show_alert=True)
        return

    price, _pct, _pc = await promo_effective(context, key, float(p["price"]))
    bal = await db.balance(q.from_user.id)

    # Keep the same priority at the actual purchase step too:
    # insufficient balance -> Add Balance; sufficient balance -> check stock.
    if bal < price:
        await q.message.reply_text(
            "❌ <b>আপনার balance পর্যাপ্ত নয়!</b>\n\nদয়া করে আগে Add Balance করুন।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [btn("Add Balance", "success", "cash", callback_data="add_balance")],
                [btn("Back", "danger", "main_menu", callback_data="buy_proxy")],
            ])
        )
        return

    if str(p["delivery_mode"] or "auto")=="manual":
        await create_manual_product_order(q,key,1,unit_price=price); return

    stock = await db.stock_count(key)
    if stock < 1:
        await q.message.reply_text(
            await stock_out_text(p),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                ([[btn("Contact Support", "primary", "chat", url=await primary_support_url())]] if await get_support_admins() else [])
                + [[btn("Back", "danger", "main_menu", callback_data="buy_proxy")]]
            )
        )
        return

    await deliver_product(q, key, 1, unit_price=price)


async def create_manual_product_order(q,key,qty,unit_price=None):
    p=await db.get_product(key); total=(float(unit_price) if unit_price is not None else float(p["price"]))*qty; uid=q.from_user.id
    if await db.balance(uid)<total:
        await q.answer("❌ Balance পর্যাপ্ত নয়!",show_alert=True); return
    # Manual delivery is NOT a stock delivery. The admin will provide the
    # proxy/VPN details manually, so stock must never be popped here.
    oid="MO"+str(int(time.time()*1000))+str(uid)[-5:]
    await db.create_manual_order(oid,uid,key,p["name"],total,qty)
    try: await q.message.delete()
    except Exception: pass
    await q.get_bot().send_message(uid,"✅ <b>Your delivery request has been received.</b>\n\nOur admin is preparing your order. Please wait a little while; your product details will be delivered here after approval.",parse_mode=ParseMode.HTML)
    uname="@"+q.from_user.username if q.from_user.username else "N/A"
    admin=("📦 <b>New Manual Delivery Request</b>\n\n"
      f"👤 <b>Username:</b> {clean_html(uname)}\n🆔 <b>User ID:</b> <code>{uid}</code>\n"
      f"📦 <b>Product:</b> {clean_html(p['name'])}\n💰 <b>Price:</b> {total:.2f} BDT\n🔢 <b>Quantity:</b> {qty}\n🧾 <b>Order ID:</b> <code>{oid}</code>\n📅 <b>Date & Time:</b> {now_bd()}\n\nChoose an action:")
    kb=InlineKeyboardMarkup([[btn("Send Order","success","check3",callback_data=f"manualorder:send:{oid}"),btn("Reject Order","danger","cross",callback_data=f"manualorder:reject:{oid}")]])
    for aid in await db.admin_ids():
        try: await q.get_bot().send_message(aid,admin,parse_mode=ParseMode.HTML,reply_markup=kb)
        except Exception: pass

async def manual_order_send(q,context,oid):
    row=await db.get_manual_order(oid)
    if not row or row["status"]!="pending": await q.answer("Order already processed.",show_alert=True); return
    p=await db.get_product(row["product_key"])
    if not p: await q.answer("Product no longer exists.",show_alert=True); return
    kind=product_kind(p)
    context.user_data["admin_action"]="manual_order_details"
    context.user_data["manual_order_id"]=oid
    label="Proxy" if kind != "vpn" else "VPN"
    prompt=(f"📦 <b>Send {label} Details</b>\n\n"
            f"Product: <b>{clean_html(p['name'])}</b>\n"
            f"Order ID: <code>{oid}</code>\n\n"
            f"Please send the {label} details exactly as you want them delivered to the customer.\n\n"
            "After sending the details, you will get <b>Confirm</b> and <b>Edit Details</b> options.")
    try: await q.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await q.message.reply_text(prompt,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="manualorder:cancel_details")]]))
    await q.answer("Send the details now.",show_alert=False)

async def manual_order_preview(q,context,oid):
    row=await db.get_manual_order(oid)
    if not row or row["status"]!="pending": await q.answer("Order already processed.",show_alert=True); return
    details=(row["draft_details"] or "").strip()
    if not details: await q.answer("Details not found. Send the details first.",show_alert=True); return
    p=await db.get_product(row["product_key"])
    if not p: await q.answer("Product no longer exists.",show_alert=True); return
    preview=clean_html(details)
    text=("📋 <b>Delivery Details Preview</b>\n\n"
          f"📦 <b>Product:</b> {clean_html(p['name'])}\n"
          f"🧾 <b>Order ID:</b> <code>{oid}</code>\n\n"
          f"<code>{preview}</code>\n\n"
          "If everything is correct, press <b>Confirm</b>. Otherwise press <b>Edit Details</b>.")
    kb=InlineKeyboardMarkup([[btn("Confirm","success","check3",callback_data=f"manualorder:confirm:{oid}"),btn("Edit Details","primary","tag",callback_data=f"manualorder:edit:{oid}")],[btn("Cancel","danger","cross",callback_data="manualorder:cancel_details")]])
    await q.message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=kb)
    await q.answer("Preview ready.")

async def manual_order_edit(q,context,oid):
    row=await db.get_manual_order(oid)
    if not row or row["status"]!="pending": await q.answer("Order already processed.",show_alert=True); return
    p=await db.get_product(row["product_key"])
    context.user_data["admin_action"]="manual_order_details"
    context.user_data["manual_order_id"]=oid
    label="Proxy" if product_kind(p)!="vpn" else "VPN"
    await q.answer("Send the corrected details.")
    await q.message.reply_text(f"✏️ <b>Edit {label} Details</b>\n\nSend the corrected details now. The previous details will be replaced.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="manualorder:cancel_details")]]))

async def manual_order_confirm(q,context,oid):
    row=await db.get_manual_order(oid)
    if not row or row["status"]!="pending": await q.answer("Order already processed.",show_alert=True); return
    details=(row["draft_details"] or "").strip()
    if not details: await q.answer("Details not found.",show_alert=True); return
    p=await db.get_product(row["product_key"])
    if not p: await q.answer("Product no longer exists.",show_alert=True); return
    uid=int(row["user_id"]); total=float(row["price"]); qty=int(row["quantity"])
    bal=await db.balance(uid)
    if bal<total: await q.answer("User balance is no longer sufficient.",show_alert=True); return
    await db.add_balance(uid,-total)
    oid2=order_id()
    await db.save_order(oid2,uid,row["product_key"],p["name"],total,qty,details)
    await db.set_manual_order(oid,"sent",q.from_user.id,oid2)
    context.user_data.pop("admin_action",None); context.user_data.pop("manual_order_id",None)
    # Deliver the admin-supplied details. No stock is popped in manual mode.
    if product_kind(p)=="vpn":
        delivery=("✅ <b>Order Delivered Successfully</b>\n\n"+product_title_with_emoji(p)+"\n\n"+f"<code>{clean_html(details)}</code>")
    else:
        formatted=format_proxy_lines(details)
        delivery=(pe("6269163801178804220","✅")+" <b>Purchase Successful</b>\n━━━━━━━━━━━━━━━━━━\n\n"+pe("6077678113125306912","📦")+"<b>Product Name :-</b> "+product_title_html(p)+"\n"+pe("6105092867900840631","💵")+f"<b>Price :-</b> {total:.2f}৳ ({usd_from_bdt(total)})\n"+pe("6190336264940559752","💳")+f"<b>Your Balance :-</b> {(await db.balance(uid)):.2f} ৳\n\n━━━━━━━━━━━━━━━━━━━━━━\n"+pe("6269368817147711866","🏷")+"<b>Proxy Details</b>\n\n"+formatted)
    await context.bot.send_message(uid,delivery,parse_mode=ParseMode.HTML)
    uname="@"+q.from_user.username if q.from_user.username else "N/A"
    notify=("✅ <b>Manual Order Delivered</b>\n\n"
            f"👤 <b>Username:</b> {clean_html(uname)}\n🆔 <b>User ID:</b> <code>{uid}</code>\n"
            f"📦 <b>Product:</b> {clean_html(p['name'])}\n💰 <b>Amount:</b> {total:.2f} BDT\n"
            f"🧾 <b>Order ID:</b> <code>{oid}</code>\n📅 <b>Delivered:</b> {now_bd()}\n"
            f"👑 <b>Processed by:</b> <code>{q.from_user.id}</code>\n\nThe manually supplied details were delivered to the customer.")
    for aid in await db.admin_ids():
        try: await context.bot.send_message(aid,notify,parse_mode=ParseMode.HTML)
        except Exception: pass
    try: await q.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await q.answer("Order confirmed and delivered.",show_alert=True)

async def manual_order_cancel_details(q,context):
    context.user_data.pop("admin_action",None); context.user_data.pop("manual_order_id",None)
    await q.answer("Cancelled")
    try: await q.message.delete()
    except Exception: pass

async def manual_order_reject(q,context,oid):
    row=await db.get_manual_order(oid)
    if not row or row["status"]!="pending": await q.answer("Order already processed.",show_alert=True); return
    await db.set_manual_order(oid,"rejected",q.from_user.id)
    context.user_data.pop("admin_action",None); context.user_data.pop("manual_order_id",None)
    try: await q.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    try: await context.bot.send_message(int(row["user_id"]),"❌ <b>Delivery Request Rejected</b>\n\nYour order request was rejected by the administrator. Your balance was not charged.",parse_mode=ParseMode.HTML)
    except Exception: pass
    await q.answer("Order rejected",show_alert=True)

async def deliver_product(q, key, qty=1, unit_price=None):
    p = await db.get_product(key)
    try:
        await q.message.delete()
    except Exception:
        pass
    price = float(unit_price) if unit_price is not None else float(p["price"])
    total = price * qty
    user_id = q.from_user.id

    bal = await db.balance(user_id)
    if bal < total:
        await q.answer("❌ আপনার ব্যালেন্সে পর্যাপ্ত টাকা নেই!", show_alert=True)
        return

    items = await db.pop_stock(key, qty)
    if len(items) != qty:
        await q.answer("❌ পর্যাপ্ত স্টক নেই!", show_alert=True)
        return

    # Charge after stock reservation; if this is a single process the SQLite
    # transaction keeps stock safe. Balance update is then applied.
    await db.add_balance(user_id, -total)

    oid = order_id()
    key_text = "\n\n".join(items)
    await db.save_order(
        oid, user_id, key, p["name"], total, qty, key_text
    )

    # Q-Style bulk delivery: 5 or more proxies are delivered as a TXT file.
    # Smaller orders keep separate tap-to-copy <code> fields in Telegram.
    if product_kind(p) in ("proxy", "multi_proxy", "key") and proxy_style(p) == "Q" and qty >= 5:
        bal_after = await db.balance(user_id)
        summary = (
            pe("6269163801178804220", "✅") + "<b>Purchase Successful</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            + pe("6077678113125306912", "📦") + "<b>Product Name :-</b>" + product_title_html(p) + "\n"
            + pe("6105092867900840631", "💵") + f"<b>Price :-</b> {total:.2f}৳ ({usd_from_bdt(total)})\n"
            + pe("6190336264940559752", "💳") + f"<b>Your Balance :-</b> {bal_after:.2f} ৳\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            + pe("6269368817147711866", "🏷") + f"<b>Proxy Details</b>\n\n"
            + f"📦 <b>Total Files:</b> {qty}\n"
            + "📄 <b>5+ quantity order হলে proxy file আকারে delivery করা হয়েছে।</b>"
        )
        file_parts = []
        for i, item in enumerate(items, 1):
            parsed = parse_proxy_fields(item)
            if parsed:
                server, port, username, password, one_line = parsed
                file_parts.append(
                    f"{i}. Server : {server}\n"
                    f"Port : {port}\n"
                    f"Username : {username}\n"
                    f"Password : {password}\n"
                    f"One-line : {one_line}\n"
                    + "-" * 55
                )
            else:
                file_parts.append(f"{i}. {item.strip()}\n" + "-" * 55)
        data = ("Purchase Successful\n" + f"Product: {p['name']}\nQuantity: {qty}\n\n" + "\n".join(file_parts)).encode("utf-8")
        doc = InputFile(io.BytesIO(data), filename=f"{re.sub(r'[^A-Za-z0-9_-]+','_',p['name'])}_{qty}pcs.txt")
        await q.message.reply_text(summary, parse_mode=ParseMode.HTML)
        await q.get_bot().send_document(user_id, document=doc, caption="📦 <b>Proxy Delivery File</b>\nএখানে আপনার সব proxy details আছে।", parse_mode=ParseMode.HTML)

    elif key == "9proxy_mb":
        total_mb = qty * 100
        user_msg = (
            pe("6269163801178804220", "✅")
            + " <b>পেমেন্ট সফল ও অটো ডেলিভারি সম্পন্ন!</b>\n"
            "━━━━━━━━━━━━━━\n"
            + pe("6104734070627900577", "🛒")
            + f" পরিমাণ: <b>{total_mb} MB</b>\n"
            + pe("6269136309093143551", "💸")
            + f" মোট খরচ: <b>{total:.2f} ৳</b>\n"
            + pe("6206495649895028694", "💬")
            + f" অর্ডার আইডি: <code>{oid}</code>\n\n"
            + pe("6084602502349595551", "🚀")
            + " <i>নিচে প্রক্সি ডিটেইলস দেওয়া হলো:</i>"
        )
        await q.message.reply_text(user_msg, parse_mode=ParseMode.HTML)
        for item in items:
            await q.message.reply_text(
                format_proxy_lines(item),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    btn("Proxy GB Checker", "primary", "link", url=checker_for(key))
                ]]) if checker_for(key) else None
            )
    elif key == "owl_200mb":
        await q.message.reply_text(
            format_owl_delivery(p, items[0], total, oid, "0.2 GB"),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn(
                    "Checker Bot", "primary", "link",
                    url="https://t.me/owlproxy_mb_checker_bot"
                )
            ]])
        )
    else:
        # Generic delivery matching the original proxy/key flows.
        bal_after = await db.balance(user_id)
        details = format_proxy_lines(items[0]) if qty == 1 else "\n\n".join(
            format_proxy_lines(x) for x in items
        )
        # Premium proxy delivery: exact compact layout + individual Copy buttons.
        checker = (p["checker"] if "checker" in p.keys() and p["checker"] else checker_for(key))
        raw_item = items[0].strip() if qty == 1 else ""
        parsed = parse_proxy_fields(raw_item) if qty == 1 else None

        if parsed:
            server, port, username, password, one_line = parsed
            user_msg = (
                pe("6269163801178804220", "✅") + "<b>Purchase Successful</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                + pe("6077678113125306912", "📦") + "<b>Product Name :-</b>"
                + product_title_html(p) + "\n"
                + pe("6105092867900840631", "💵") + f"<b>Price :-</b> {total:.2f}৳ ({usd_from_bdt(total)})\n"
                + pe("6190336264940559752", "💳") + f"<b>Your Balance :-</b> {bal_after:.2f} ৳\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                + pe("6269368817147711866", "🏷") + "<b>Proxy Details</b>\n\n"
                + f"<b>Server :</b> <code>{clean_html(server)}</code>\n\n"
                + f"<b>Port :</b> <code>{clean_html(port)}</code>\n\n"
                + f"<b>Username :</b> <code>{clean_html(username)}</code>\n\n"
                + f"<b>Password :</b> <code>{clean_html(password)}</code>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                + pe("6206285149252883826", "⛔️") + "<b>One-line :</b>\n"
                + f"<code>{clean_html(one_line)}</code>"
            )
            delivery_kb = InlineKeyboardMarkup([[btn("Proxy GB Checker", "primary", "link", url=checker)]]) if checker else None
        else:
            # Multiple-stock fallback keeps the existing delivery behavior.
            details = "\n\n".join(format_proxy_lines(x) for x in items)
            user_msg = (
                pe("6269163801178804220", "✅") + "<b>Purchase Successful</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                + pe("6077678113125306912", "📦") + "<b>Product Name :-</b>" + product_title_html(p) + "\n"
                + pe("6105092867900840631", "💵") + f"<b>Price :-</b> {total:.2f}৳ ({usd_from_bdt(total)})\n"
                + pe("6190336264940559752", "💳") + f"<b>Your Balance :-</b> {bal_after:.2f} ৳\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                + pe("6269368817147711866", "🏷") + "<b>Proxy Details</b>\n\n" + details
            )
            delivery_kb = InlineKeyboardMarkup([[btn("Proxy GB Checker", "primary", "link", url=checker)]]) if checker else None

        await q.message.reply_text(
            user_msg, parse_mode=ParseMode.HTML, reply_markup=delivery_kb
        )

    # Admin sale notification
    uname = f"@{q.from_user.username}" if q.from_user.username else "No Username"
    admins = await db.admin_ids()
    stock_left = await db.stock_count(key)
    admin_msg = (
        "🛒 <b>নতুন Proxy Sale!</b>\n\n"
        f"👤 <b>User:</b> {clean_html(uname)}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"📦 <b>Product:</b> {clean_html(p['name'])}\n"
        f"💰 <b>Amount:</b> {total:.2f} ৳\n"
        f"🧾 <b>Order:</b> <code>{oid}</code>\n"
        f"⏰ <b>Time:</b> {now_bd()}\n"
        f"📊 <b>Remaining Stock:</b> {stock_left}"
    )
    for aid in admins:
        try:
            await q.get_bot().send_message(aid, admin_msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass


def parse_proxy_fields(item: str):
    """Return server, port, username, password, one-line for proxy stock."""
    raw = item.strip()
    # Labeled stock: Server/IP, Port, Username, Password.
    if re.search(r"\b(?:IP|Server)\s*:", raw, re.I):
        def get(pattern):
            m = re.search(pattern, raw, re.I)
            return m.group(1).strip() if m else ""
        server = get(r"(?:Server|IP)\s*:\s*(.+?)(?=\n|\r|$)")
        port = get(r"Port\s*:\s*(.+?)(?=\n|\r|$)")
        username = get(r"Username\s*:\s*(.+?)(?=\n|\r|$)")
        password = get(r"Password\s*:\s*(.+?)(?=\n|\r|$)")
        one = get(r"One-line\s*:\s*(.+?)(?=\n|\r|$)")
        if server and port and username and password:
            return server, port, username, password, (one or f"{server}:{port}:{username}:{password}")

    # Server:Port:Username:Password
    parts = raw.split(":")
    if len(parts) >= 4:
        server = parts[0].strip()
        port = parts[1].strip()
        username = parts[2].strip()
        password = ":".join(parts[3:]).strip()
        return server, port, username, password, raw
    return None


def format_proxy_lines(item: str) -> str:
    """Format proxy details with each value on its own separated line."""
    parsed = parse_proxy_fields(item)
    if parsed:
        server, port, username, password, one_line = parsed
        return (
            f"<b>Server :</b> <code>{clean_html(server)}</code>\n\n"
            f"<b>Port :</b> <code>{clean_html(port)}</code>\n\n"
            f"<b>Username :</b> <code>{clean_html(username)}</code>\n\n"
            f"<b>Password :</b> <code>{clean_html(password)}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            + pe("6206285149252883826", "⛔️") + "<b>One-line :</b>\n"
            + f"<code>{clean_html(one_line)}</code>"
        )
    return f"<code>{clean_html(item.strip())}</code>"

def format_owl_delivery(p, item, total, oid, plan):
    details = format_proxy_lines(item)
    return (
        pe("6269163801178804220", "✅") + " <b>Purchase Successful</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        + pe("6104734070627900577", "🛒") + f" <b>Package :</b> {plan}\n"
        + pe("6269136309093143551", "💸") + f" <b>Price :</b> {total:.2f} ৳ ({usd_from_bdt(total)})\n"
        + pe(PRODUCT_PREMIUM_EMOJI, "🛍") + f" <b>Provider :</b> {clean_html(p['name'])}\n"
        + pe("6105060531092069509", "⌛") + f" <b>Processed :</b> {now_bd()}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        + pe("6269368817147711866", "🏷") + " <b>Proxy Details ⬇️</b>\n\n"
        + details + "\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# 9PROXY 100MB QUANTITY FLOW
# ============================================================

async def show_9proxy_quantity(q, context, qty=1):
    p = await db.get_product("9proxy_mb")
    bal = await db.balance(q.from_user.id)
    manual_mode = str(p["delivery_mode"] or "auto")=="manual"
    stock = await db.stock_count("9proxy_mb")
    qty = max(1, int(qty)) if manual_mode else max(1, min(int(qty), max(1, stock)))
    total = float(p["price"]) * qty

    text = (
        f"💰 <b>Balance:</b> {bal:.2f} ৳\n\n"
        f"{pe(PRODUCT_PREMIUM_EMOJI, '🛍')} <b>9Proxy 100 MB</b>\n"
        f"💎 {float(p['price']):.2f} ৳ x {qty} = <b>{total:.2f} ৳</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Select quantity:"
    )
    kb = InlineKeyboardMarkup([
        [
            btn("➖", "danger", callback_data=f"9qty:{qty-1}"),
            btn(f"{qty}", "primary", "cart", callback_data="ignore"),
            btn("➕", "success", callback_data=f"9qty:{qty+1}"),
        ],
        [
            btn("Confirm", "success", "check3", callback_data=f"9confirm:{qty}"),
            btn("Cancel", "danger", "cross", callback_data="buy_proxy"),
        ],
    ])
    try:
        await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def nine_qty_callback(update, context, qty):
    q = update.callback_query
    if qty < 1:
        await q.answer("⚠️ সর্বনিম্ন ১টি সিলেক্ট করতে হবে!")
        return
    p = await db.get_product("9proxy_mb")
    manual_mode = str(p["delivery_mode"] or "auto")=="manual"
    stock = await db.stock_count("9proxy_mb")
    if not manual_mode and qty > stock:
        qty = stock if stock > 0 else 1
    await q.answer()
    await show_9proxy_quantity(q, context, qty)


async def nine_confirm_callback(update, context, qty):
    q = update.callback_query
    await q.answer()
    if qty < 1:
        return
    p = await db.get_product("9proxy_mb")
    total = float(p["price"]) * qty
    bal = await db.balance(q.from_user.id)
    if bal < total:
        await q.message.reply_text(
            "❌ <b>আপনার balance পর্যাপ্ত নয়!</b>\n\nদয়া করে আগে Add Balance করুন।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [btn("Add Balance", "success", "cash", callback_data="add_balance")],
                [btn("Back", "danger", "main_menu", callback_data="buy_proxy")],
            ])
        )
        return
    if str(p["delivery_mode"] or "auto")=="manual":
        await create_manual_product_order(q,"9proxy_mb",qty); return
    stock = await db.stock_count("9proxy_mb")
    if stock < qty:
        await q.answer(f"❌ পর্যাপ্ত স্টক নেই! স্টকে আছে: {stock} টি", show_alert=True)
        return
    await deliver_product(q, "9proxy_mb", qty)


# ============================================================
# ADMIN PANEL
# ============================================================

async def admin_mail_stock(q):
    rows=[]
    for p in await db.products():
        if product_kind(p)!="mail": continue
        count=await db.stock_count(p["key"]); state="Hidden" if p["hidden"] else "Visible"
        rows.append([btn(f"{p['name']} | {count} | {state}","danger" if p["hidden"] else "primary","bridge",callback_data=f"mailstockedit:{p['key']}")])
    rows += [[btn("Admin Panel","primary","bot",callback_data="admin:panel")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]]
    await q.message.edit_text("📧 <b>Mail Stock</b>\n\nSelect Mail product:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_edit_mail(q):
    rows=[]
    for p in await db.products():
        if product_kind(p)!="mail": continue
        count=await db.stock_count(p["key"]); state="Hidden" if p["hidden"] else "Visible"
        rows.append([btn(f"{p['name']} — {float(p['price']):.2f}৳ | Stock {count} | {state}","primary","tag",callback_data=f"editmail:{p['key']}")])
    rows += [[btn("Admin Panel","primary","bot",callback_data="admin:panel")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]]
    await q.message.edit_text("✏️ <b>Edit Mail</b>\n\nSelect Mail product:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_mail_edit(q,key):
    p=await db.get_product(key)
    if not p or product_kind(p)!="mail": await q.answer("Mail product not found",show_alert=True); return
    count=await db.stock_count(key); hidden=bool(p["hidden"])
    rows=[[btn("Unhide Mail" if hidden else "Hide Mail","success" if hidden else "danger","check" if hidden else "cross",callback_data=f"mailhide:{key}")],[btn("Edit Price","primary","tag",callback_data=f"editprice:{key}")],[btn("Edit Name","primary","tag",callback_data=f"maileditname:{key}")],[btn("Edit Emoji","primary","shop",callback_data=f"mailemoji:{key}")],[btn(f"Add Stock ({count})","primary","cart",callback_data=f"addstock:{key}")],[btn("Clear Stock","danger","cross",callback_data=f"clearstock:{key}")],[btn("Delete Mail","danger","cross",callback_data=f"deletemail:{key}")],[btn("Edit Mail","primary","link",callback_data="admin:editmail")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]]
    await q.message.edit_text(product_title_with_emoji(p)+f"\n\n💰 <b>Price:</b> {float(p['price']):.2f}৳\n📦 <b>Stock:</b> {count}\n👁 <b>Status:</b> {'Hidden' if hidden else 'Visible'}",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

def admin_panel_kb():
    return InlineKeyboardMarkup([
        [btn("Statistics", "primary", "bot", callback_data="admin:stats"), btn("Broadcast", "primary", "chat", callback_data="admin:broadcast")],
        [btn("Add Proxy", "success", "buy_proxy", callback_data="admin:addproxy"), btn("Add VPN", "primary", "buy_vpn", callback_data="admin:addvpn")],
        [btn("Add Mail", "success", "buy_mail", callback_data="admin:addmail"), btn("Mail Stock", "primary", "bridge", callback_data="admin:mailstock")],
        [btn("Edit Mail", "primary", "tag", callback_data="admin:editmail")],
        [btn("Proxy Stock", "success", "bridge", callback_data="admin:stock"), btn("VPN Stock", "primary", "bridge", callback_data="admin:vpnstock")],
        [btn("Edit Proxy", "success", "tag", callback_data="admin:editproxy"), btn("Edit VPN", "primary", "tag", callback_data="admin:editvpn")],
        [btn("Set Promo Code", "success", "5235989279024373566", callback_data="admin:promo")],
        [btn("Ban / Unban User", "danger", "cross", callback_data="admin:banunban")],
        [btn("Admin Management", "danger", "bot", callback_data="admin:admins")],
        [btn("Emoji Customise", "primary", "shop", callback_data="admin:emoji")],
        [btn("Support Management", "primary", "chat", callback_data="admin:support")],
        [btn("Payment Management", "success", "cash", callback_data="admin:payments")],
        [btn("Menu Hide", "primary", "shop", callback_data="admin:menuhide")],
        [btn("Menu Button Edit", "primary", "tag", callback_data="admin:menubuttonedit")],
        [btn("Text Emoji", "primary", "shop", callback_data="admin:textemoji")],
        [btn("Force Channel Join", "primary", "link", callback_data="admin:forcejoin")],
        [btn("Set Welcome", "success", "rocket", callback_data="admin:setwelcome")],
        [btn("Main Menu", "danger", "main_menu", callback_data="main_menu")],
    ])


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    await update.message.reply_text(
        "👑 <b>Admin Pannel</b>\n\nনিচের অপশন থেকে নির্বাচন করুন:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_kb(),
    )


async def admin_stats(q):
    s = await db.stats()
    text = (
        "📊 <b>Statistics</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👥 <b>Total Users:</b> {s['users']}\n"
        f"🚫 <b>Banned Users:</b> {s['banned']}\n"
        f"👑 <b>Admins:</b> {s['admins']}\n"
        f"🧾 <b>Total Orders:</b> {s['orders']}\n"
        f"💰 <b>Total Deposit:</b> {s['deposits']:.2f} ৳\n"
        f"🛒 <b>Total Sales:</b> {s['sales']:.2f} ৳"
    )
    await q.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")
        ]])
    )


async def admin_stock(q):
    rows=[]
    for p in await db.products():
        if product_kind(p) not in ("proxy", "multi_proxy", "key"): continue
        count=await db.stock_count(p["key"]); state="Hidden" if p["hidden"] else "Visible"
        rows.append([btn(f"{p['name']} | {count} | {state}", "danger" if p["hidden"] else "success", "bridge", callback_data=f"stockedit:{p['key']}")])
    rows += [[btn("Admin Panel", "primary", "link", callback_data="admin:panel")],[btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")]]
    await q.message.edit_text("♻️ <b>Proxy Stock</b>\n\nProduct নির্বাচন করুন:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))

async def admin_edit_proxy(q):
    rows=[]
    for p in await db.products():
        if product_kind(p) not in ("proxy", "multi_proxy", "key"): continue
        count=await db.stock_count(p["key"]); state="Hidden" if p["hidden"] else "Visible"
        rows.append([btn(f"{p['name']} — {float(p['price']):.2f}৳ | Stock {count} | {state}", "success", "tag", callback_data=f"editproxy:{p['key']}")])
    rows += [[btn("Admin Panel", "primary", "link", callback_data="admin:panel")],[btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")]]
    await q.message.edit_text("✏️ <b>Edit Proxy</b>\n\nProxy নির্বাচন করুন:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))

async def admin_proxy_edit(q,key):
    p=await db.get_product(key)
    if not p or product_kind(p)=="vpn": await q.answer("Product not found", show_alert=True); return
    count=await db.stock_count(key); hidden=bool(p["hidden"])
    proxy_rows = [[btn("Unhide Proxy" if hidden else "Hide Proxy", "success" if hidden else "danger", "check" if hidden else "cross", callback_data=f"togglehide:{key}")],[btn("Edit Name","primary","tag",callback_data=f"proxyeditname:{key}")],[btn("Edit Price","success","tag",callback_data=f"editprice:{key}")],[btn(f"Add Stock ({count})","success","cart",callback_data=f"addstock:{key}")],[btn("Clear Stock","danger","cross",callback_data=f"clearstock:{key}")],[btn("Delivery Mode: " + ("Manual" if str(p["delivery_mode"] or "auto") == "manual" else "Auto"), "primary", "shop", callback_data=f"deliverymode:{key}")]]
    if str(key).startswith("proxy_"):
        proxy_rows.append([btn("Delete Proxy", "danger", "cross", callback_data=f"admin:deleteproxy:{key}")])
    proxy_rows += [[btn("Edit Proxy","primary","link",callback_data="admin:editproxy")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]]
    kb=InlineKeyboardMarkup(proxy_rows)
    await q.message.edit_text(f"✏️ <b>{clean_html(p['name'])}</b>\n\n💰 <b>Price:</b> {float(p['price']):.2f}৳ ({usd_from_bdt(p['price'])})\n📦 <b>Stock:</b> {count}\n👁 <b>Status:</b> {'Hidden' if hidden else 'Visible'}",parse_mode=ParseMode.HTML,reply_markup=kb)

async def admin_vpn_stock(q):
    rows=[]
    for p in await db.products():
        if product_kind(p)!="vpn": continue
        count=await db.stock_count(p["key"]); state="Hidden" if p["hidden"] else "Visible"
        rows.append([btn(f"{p['name']} | {count} | {state}", "danger" if p["hidden"] else "primary", "vpn_shop", callback_data=f"vpnstockedit:{p['key']}")])
    rows += [[btn("Admin Panel","primary","link",callback_data="admin:panel")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]]
    await q.message.edit_text("♻️ <b>VPN Stock</b>\n\nVPN service নির্বাচন করুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_edit_vpn(q):
    rows=[]
    for p in await db.products():
        if product_kind(p)!="vpn": continue
        count=await db.stock_count(p["key"]); state="Hidden" if p["hidden"] else "Visible"
        rows.append([btn(f"{p['name']} — {float(p['price']):.2f}৳ | Stock {count} | {state}","primary","vpn_shop",callback_data=f"editvpn:{p['key']}")])
    rows += [[btn("Admin Panel","primary","link",callback_data="admin:panel")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]]
    await q.message.edit_text("✏️ <b>Edit VPN</b>\n\nVPN service নির্বাচন করুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_vpn_edit(q,key):
    p=await db.get_product(key)
    if not p or product_kind(p)!="vpn": await q.answer("VPN product not found",show_alert=True); return
    count=await db.stock_count(key); hidden=bool(p["hidden"])
    kb=InlineKeyboardMarkup([[btn("Unhide VPN" if hidden else "Hide VPN","success" if hidden else "danger","check" if hidden else "cross",callback_data=f"togglevpn:{key}")],[btn("Edit Name","primary","tag",callback_data=f"vpneditname:{key}")],[btn("Edit Price","primary","tag",callback_data=f"editprice:{key}")],[btn(f"Add Stock ({count})","primary","cart",callback_data=f"addstock:{key}")],[btn("Clear Stock","danger","cross",callback_data=f"clearstock:{key}")],[btn("Delivery Mode: " + ("Manual" if str(p["delivery_mode"] or "auto") == "manual" else "Auto"),"primary","shop",callback_data=f"deliverymode:{key}")],[btn("Edit VPN","primary","link",callback_data="admin:editvpn")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])
    await q.message.edit_text(product_title_with_emoji(p)+f"\n\n💰 <b>Price:</b> {float(p['price']):.2f}৳ ({usd_from_bdt(p['price'])})\n📦 <b>Stock:</b> {count}\n👁 <b>Status:</b> {'Hidden' if hidden else 'Visible'}",parse_mode=ParseMode.HTML,reply_markup=kb)

async def admin_admins(q):
    rows=[]
    for uid in await db.admin_ids():
        label=f"❌ Delete {uid}" if uid not in DEFAULT_ADMINS else f"🔒 Protected {uid}"
        cb=f"admin:deladmin:{uid}" if uid not in DEFAULT_ADMINS else "ignore"
        rows.append([btn(label,"danger" if uid not in DEFAULT_ADMINS else "primary","cross" if uid not in DEFAULT_ADMINS else "check",callback_data=cb)])
    rows.append([btn("➕ Add Admin","success","bot",callback_data="admin:addadmin")])
    rows.append([btn("Cancel","danger","cross",callback_data="admin:cancel_action")])
    await q.message.edit_text("👑 <b>Admin Management</b>\n\nAdd বা Delete admin করুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

EMOJI_BUTTON_LABELS = {
    "buy_proxy":"Buy Proxy","buy_vpn":"Buy VPN","buy_mail":"Buy Mail","add_balance":"Add Balance","my_balance":"My Balance","cart":"My Purchases","hourglass":"Payment History","link":"Proxy Data Checker / Link","chat":"Support","bot":"Admin Panel","cash":"Payment / Cash","tag":"Edit / Price","bridge":"Stock","shop":"Shop","world":"Proxy Service","main_menu":"Main Menu","cross":"Cancel","check":"Verify / Check","server":"Checker","rocket":"Rocket","heart":"Support / Heart","set_welcome":"Set Welcome","payments":"Payment Management","manualpay":"Manual Payment","menuhide":"Menu Hide","zini":"ZiniPay","elitepay":"ElitePay","manual":"Manual Payment","bkash":"bKash","nagad":"Nagad","rocket_method":"Rocket","binance_method":"Binance","auto_delivery":"Auto Delivery","manual_delivery":"Manual Delivery","approve":"Approve","reject":"Rejected"
}

async def admin_emoji(q):
    rows=[]
    for k,label in EMOJI_BUTTON_LABELS.items():
        rows.append([btn(f"{label}  •  {BTN_EMO.get(k,'default')}","primary",k if k in BTN_EMO else "shop",callback_data=f"emoji:set:{k}")])
    for p in await db.products():
        if product_kind(p) in ("proxy","multi_proxy","key","vpn","mail"):
            rows.append([btn(f"{p['name']}  •  Product Emoji", "primary", "shop", callback_data=f"emoji:product:{p['key']}")])
    rows.append([btn("Cancel","danger","cross",callback_data="admin:cancel_action")])
    await q.message.edit_text("✨ <b>Emoji Customise</b>\n\nযে button/service-এর emoji বদলাতে চান সেটি নির্বাচন করুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))


async def admin_support_management(q):
    admins = await get_support_admins()
    rows = []
    for i, admin in enumerate(admins):
        rows.append([btn(f"❌ Delete {admin}", "danger", "cross", callback_data=f"admin:support_delete:{i}")])
    rows.append([btn("➕ Add Support Admin", "success", "chat", callback_data="admin:support_add")])
    rows.append([btn("Admin Panel", "primary", "bot", callback_data="admin:panel")])
    rows.append([btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")])
    listing = "\n".join(f"{i}. <code>{clean_html(a)}</code>" for i,a in enumerate(admins,1)) or "No support admin added"
    await q.message.edit_text(
        "💬 <b>Support Management</b>\n\n"
        f"<b>Current Support Admins:</b>\n{listing}\n\n"
        "এখান থেকে Support Admin add/delete করতে পারবেন।",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows)
    )


async def admin_force_join(q):
    channels = await get_force_join_channels()
    enabled = await force_join_enabled()
    rows = []
    for i, ch in enumerate(channels):
        rows.append([btn(f"❌ Delete {ch}", "danger", "cross", callback_data=f"admin:forcejoin_delete:{i}")])
    rows.append([btn("➕ Add Channel", "success", "link", callback_data="admin:forcejoin_add")])
    rows.append([btn("🟢 Enabled" if enabled else "🔴 Disabled", "success" if enabled else "danger", "check" if enabled else "cross", callback_data="admin:forcejoin_toggle")])
    rows.append([btn("Admin Panel", "primary", "bot", callback_data="admin:panel")])
    rows.append([btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")])
    status = "🟢 ENABLED" if enabled else "🔴 DISABLED"
    channel_text = "\n".join(f"{i}. <code>{clean_html(ch)}</code>" for i, ch in enumerate(channels, 1)) or "No channel added"
    await q.message.edit_text(
        "🔐 <b>Force Channel Join</b>\n\n"
        f"Status: <b>{status}</b>\n\n"
        f"<b>Channels:</b>\n{channel_text}\n\n"
        "Enabled থাকলে bot ব্যবহার করার আগে সব channel Join + Verify করতে হবে।\n"
        "Disabled করলে /start সরাসরি Main Menu খুলবে.",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows)
    )

# ============================================================
# PAYMENT / MENU / DELIVERY ADMIN SCREENS
# ============================================================

async def admin_payment_management(q):
    rows=[]
    for gw,label in [("zini","ZiniPay"),("elitepay","ElitePay")]:
        on=await gateway_enabled(gw); key=await gateway_key(gw); minimum=await gateway_minimum(gw)
        rows.append([btn(f"{label}  {'🟢 ON' if on else '🔴 OFF'}", "success" if on else "danger", gw, callback_data=f"gatewaycfg:{gw}")])
        # Keep secrets out of the UI; show only whether configured.
        _=key; _=minimum
    rows.append([btn("Manual Payment System","primary","manualpay",callback_data="admin:manualpay")])
    rows.append([btn("Admin Panel","primary","bot",callback_data="admin:panel")])
    await q.message.edit_text("💳 <b>Payment Management</b>\n\nSelect a gateway to configure its Brand/API key, minimum deposit and ON/OFF status.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_gateway_config(q, gateway):
    on=await gateway_enabled(gateway); minimum=await gateway_minimum(gateway); key=await gateway_key(gateway)
    label="ZiniPay" if gateway=="zini" else "ElitePay"
    context=None
    rows=[[btn("🟢 Disable" if on else "🔴 Enable","danger" if on else "success","check" if on else "rocket",callback_data=f"gatewaytoggle:{gateway}")],
          [btn("Set Brand Key / API Key","primary","tag",callback_data=f"gatewaykey:{gateway}")],
          [btn("Set Minimum Deposit","primary","cash",callback_data=f"gatewaymin:{gateway}")],
          [btn("Back","danger","main_menu",callback_data="admin:payments")]]
    status="Configured" if key else "Not configured"
    await q.message.edit_text(f"💳 <b>{label}</b>\n\nStatus: {'🟢 Enabled' if on else '🔴 Disabled'}\nKey: <b>{status}</b>\nMinimum: <b>{minimum:.2f}৳</b>\n\nOnly the Brand/API key is required for this gateway; the API URLs are built into the bot.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_manual_payment(q):
    rows=[[btn("bKash","success","bkash",callback_data="manualcfg:bkash")],[btn("Nagad","success","nagad",callback_data="manualcfg:nagad")],[btn("Rocket","success","rocket_method",callback_data="manualcfg:rocket")],[btn("Binance","primary","binance_method",callback_data="manualcfg:binance")],[btn("Manual System ON / OFF","primary","manual",callback_data="manualtoggle")],[btn("Set Minimum Deposit","primary","cash",callback_data="manualmin")],[btn("Back","danger","main_menu",callback_data="admin:payments")]]
    enabled=await setting_bool("gateway:manual:enabled",True)
    await q.message.edit_text(f"💳 <b>Manual Payment System</b>\n\nStatus: {'🟢 Enabled' if enabled else '🔴 Disabled'}\n\nManage bKash, Nagad, Rocket and Binance.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_manual_cfg(q, method):
    if method=="binance":
        api=await db.get_setting("manual:binance:api_key",""); sec=await db.get_setting("manual:binance:security_key",""); rate=await db.get_setting("manual:binance:rate","125")
        rows=[[btn("Set API Identity Key (Certificate SN)","primary","tag",callback_data="binancecfg:api")],[btn("Set Security Key","primary","tag",callback_data="binancecfg:sec")],[btn("Set Dollar RT","primary","cash",callback_data="binancecfg:rate")],[btn("Test Binance Pay API","primary","check3",callback_data="binancetest")],[btn("Enable / Disable","success","check",callback_data="binancetoggle")],[btn("Back","danger","manual",callback_data="admin:manualpay")]]
        await q.message.edit_text(f"🟡 <b>Binance API Management</b>\n\nAPI Identity Key: {'Configured' if api else 'Not configured'}\nSecurity Key: {'Configured' if sec else 'Not configured'}\nDollar RT: <b>{float(rate):.2f}</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows)); return
    enabled=await manual_method_enabled(method); num=await manual_method_number(method)
    rows=[[btn("Set / Edit Number","primary","tag",callback_data=f"manualnum:{method}")],[btn("Hide Number" if enabled else "Unhide Number","danger" if enabled else "success","cross" if enabled else "check",callback_data=f"manualhide:{method}")],[btn("Delete Number","danger","cross",callback_data=f"manualdelete:{method}")],[btn("Back","primary","manual",callback_data="admin:manualpay")]]
    await q.message.edit_text(f"💳 <b>{method.title()}</b>\n\nNumber: <code>{clean_html(num or 'Not set')}</code>\nStatus: {'🟢 Visible' if enabled else '🔴 Hidden'}\n\nSet Number / Edit Number use the same input.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_menu_button_edit(q):
    rows=[]
    for key in MENU_BUTTON_LABELS:
        rows.append([btn(f"{menu_label(key)}  •  {key}", "primary", key if key in BTN_EMO else "shop", callback_data=f"menubtnedit:{key}")])
    rows.append([btn("Back", "danger", "main_menu", callback_data="admin:panel")])
    await q.message.edit_text("✏️ <b>Menu Button Edit</b>\n\nযে Main Menu button-এর নাম/emoji পরিবর্তন করতে চান সেটি নির্বাচন করুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_menu_button_editor(q,key):
    if key not in MENU_BUTTON_LABELS:
        await q.answer("Menu button not found",show_alert=True); return
    rows=[
        [btn("Edit Name","primary","tag",callback_data=f"menubtnname:{key}")],
        [btn("Edit Emoji","primary","shop",callback_data=f"menubtnemoji:{key}")],
        [btn("Back","danger","main_menu",callback_data="admin:menubuttonedit")],
    ]
    await q.message.edit_text(f"✏️ <b>{clean_html(menu_label(key))}</b>\n\nName বা Premium Emoji পরিবর্তন করুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

async def admin_text_emoji(q):
    global TEXT_EMOJI_ID, TEXT_EMOJI_MAP
    raw_map = await db.get_setting("text_emoji_map", "{}")
    try:
        TEXT_EMOJI_MAP = json.loads(raw_map or "{}")
        if not isinstance(TEXT_EMOJI_MAP, dict):
            TEXT_EMOJI_MAP = {}
    except Exception:
        TEXT_EMOJI_MAP = {}
    TEXT_EMOJI_ID = str(await db.get_setting("text_emoji_id", "") or "")

    count = len(TEXT_EMOJI_MAP)
    sample = ""
    if count:
        sample_items = list(TEXT_EMOJI_MAP.items())[:5]
        sample = "\n".join(f"{e} → <code>{eid}</code>" for e, eid in sample_items)
        if count > 5:
            sample += f"\n… and {count-5} more"
    else:
        sample = "Not configured"

    rows=[
        [btn("📄 Upload Emoji ID File","primary","shop",callback_data="textemoji:set")],
        [btn("🧹 Remove Emoji Map","danger","cross",callback_data="textemoji:remove")],
        [btn("Back","danger","main_menu",callback_data="admin:panel")],
    ]
    await q.message.edit_text(
        "✨ <b>Text Emoji Premium System</b>\n\n"
        "Emoji ID file upload করলে Bot file-এর normal emoji + Premium Emoji ID mapping save করবে।\n"
        "এরপর Bot-এর outgoing HTML text message-এ matching normal emoji থাকলে সেটি automatically Premium Custom Emoji হিসেবে দেখাবে।\n\n"
        f"<b>Loaded mappings:</b> {count}\n\n"
        f"{sample}\n\n"
        "<b>File format:</b> প্রতি লাইনে <code>⚠️6206174450765796040</code> এর মতো normal emoji + ID দিন।",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows)
    )

async def admin_menu_hide(q):
    rows=[]
    for key,label in MENU_BUTTON_LABELS.items():
        visible=MENU_VISIBLE.get(key,True)
        rows.append([btn(f"{label} — {'🟢 Visible' if visible else '🔴 Hidden'}","success" if visible else "danger",key if key in BTN_EMO else "shop",callback_data=f"menutoggle:{key}")])
    rows.append([btn("Back","danger","main_menu",callback_data="admin:panel")])
    await q.message.edit_text("👁 <b>Menu Hide</b>\n\nTap a button to Hide / Unhide it from the Main Menu.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows))

# ============================================================
# ADMIN CALLBACKS
# ============================================================

def _promo_back_row():
    return [btn("Set Promo Code", "primary", PROMO_ICON_ID, callback_data="admin:promo"), btn("Admin Panel", "primary", "bot", callback_data="admin:panel")]

PROMO_ICON_ID = "5235989279024373566"

async def admin_promo_list(q):
    rows = []
    for p in await db.products():
        if product_kind(p) not in ("proxy", "multi_proxy", "key"):
            continue
        promo = await db.promo_get(p["key"])
        if promo:
            label = f"{'🟢' if promo['enabled'] else '🔴'} {p['name']} — {float(promo['percent']):g}%"
        else:
            label = str(p["name"])
        row = [btn(label, "primary", "tag", callback_data=f"admin:promo_p:{p['key']}")]
        if promo:
            row.append(btn("Disable" if promo["enabled"] else "Enable",
                           "danger" if promo["enabled"] else "success",
                           callback_data=f"admin:promo_t:{p['key']}"))
        rows.append(row)
    rows.append([btn("Admin Panel", "primary", "bot", callback_data="admin:panel")])
    await q.message.edit_text(
        pe(PROMO_ICON_ID, "🎰") + " <b>Set Promo Code</b>\n\nযে Proxy-তে discount দিতে চান সেটা সিলেক্ট করুন:\n🟢 = চালু   🔴 = বন্ধ",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))

async def admin_promo_detail(q, key):
    p = await db.get_product(key)
    if not p:
        await q.answer("Product not found", show_alert=True); return
    promo = await db.promo_get(key)
    if promo:
        text = (pe(PROMO_ICON_ID, "🎰") + f" <b>{clean_html(p['name'])}</b>\n\n"
                f"<b>Discount:</b> {float(promo['percent']):g}%\n"
                f"<b>Promo Code:</b> <code>{clean_html(promo['code'])}</code>\n"
                f"<b>Status:</b> {'🟢 Enabled' if promo['enabled'] else '🔴 Disabled'}")
        rows = [
            [btn("Disable" if promo["enabled"] else "Enable", "danger" if promo["enabled"] else "success", callback_data=f"admin:promo_td:{key}")],
            [btn("Change Promo", "primary", "tag", callback_data=f"admin:promo_s:{key}")],
            [btn("Delete Promo", "danger", "cross", callback_data=f"admin:promo_d:{key}")],
        ]
    else:
        text = pe(PROMO_ICON_ID, "🎰") + f" <b>{clean_html(p['name'])}</b>\n\nএই Proxy-তে এখনো কোনো Promo Code নেই।"
        rows = [[btn("Set Promo Code", "success", PROMO_ICON_ID, callback_data=f"admin:promo_s:{key}")]]
    rows.append(_promo_back_row())
    await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))

async def admin_promo_router(update, context, q, data):
    parts = data.split(":", 2)
    act = parts[1]
    arg = parts[2] if len(parts) > 2 else ""
    cancel_row = [btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")]
    if act == "promo":
        await q.answer(); await admin_promo_list(q); return
    if act == "promo_p":
        await q.answer(); await admin_promo_detail(q, arg); return
    if act == "promo_t":
        await db.promo_toggle(arg); await q.answer("Updated"); await admin_promo_list(q); return
    if act == "promo_td":
        await db.promo_toggle(arg); await q.answer("Updated"); await admin_promo_detail(q, arg); return
    if act == "promo_d":
        await db.promo_delete(arg)
        for ctx_app in (context.user_data,):
            ctx_app.get("promo_applied", {}).pop(arg, None)
        await q.answer("Promo Code deleted", show_alert=True); await admin_promo_list(q); return
    if act == "promo_s":
        context.user_data["promo_key"] = arg
        context.user_data.pop("promo_percent", None); context.user_data.pop("promo_code", None)
        context.user_data["admin_action"] = "promo_percent"
        await q.answer()
        await q.message.edit_text(
            pe(PROMO_ICON_ID, "🎰") + " <b>কত % discount দিতে চান?</b>\n\nশুধু সংখ্যা লিখুন (উদাহরণ: <code>5</code>), তারপর Next চাপুন:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Next", "success", "check3", callback_data="admin:promo_pct_next")], cancel_row]))
        return
    if act == "promo_pct_next":
        if context.user_data.get("promo_percent") is None:
            await q.answer("আগে discount % লিখুন", show_alert=True); return
        context.user_data["admin_action"] = "promo_code"
        await q.answer()
        await q.message.edit_text(
            pe(PROMO_ICON_ID, "🎰") + " <b>Promo Code set করুন</b>\n\nএকটি code লিখুন (উদাহরণ: <code>2026</code>):",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([cancel_row]))
        return
    if act == "promo_save":
        key = context.user_data.get("promo_key"); pct = context.user_data.get("promo_percent"); code = context.user_data.get("promo_code")
        if not key or pct is None or not code:
            await q.answer("Incomplete", show_alert=True); return
        await db.promo_set(key, pct, code)
        for k in ("promo_key", "promo_percent", "promo_code", "admin_action"):
            context.user_data.pop(k, None)
        await q.answer("Promo Code saved", show_alert=True)
        await admin_promo_detail(q, key); return
    await q.answer()

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    await ensure_user_from_callback(q)
    if not await is_admin(q.from_user.id):
        await q.answer("❌ Admin only", show_alert=True)
        return

    if data.startswith("admin:promo"):
        return await admin_promo_router(update, context, q, data)
    if data=="admin:payments": await q.answer(); await admin_payment_management(q); return
    if data=="admin:manualpay": await q.answer(); await admin_manual_payment(q); return
    if data=="admin:menuhide": await q.answer(); await admin_menu_hide(q); return
    if data=="admin:menubuttonedit": await q.answer(); await admin_menu_button_edit(q); return
    if data=="admin:textemoji": await q.answer(); await admin_text_emoji(q); return
    if data.startswith("gatewaycfg:"): await q.answer(); await admin_gateway_config(q,data.split(":",1)[1]); return
    if data.startswith("gatewaytoggle:"):
        gw=data.split(":",1)[1]; await db.set_setting(f"gateway:{gw}:enabled","0" if await gateway_enabled(gw) else "1"); await q.answer("Updated",show_alert=True); await admin_gateway_config(q,gw); return
    if data.startswith("gatewaykey:"):
        gw=data.split(":",1)[1]; context.user_data["admin_action"]="gateway_key"; context.user_data["gateway_target"]=gw; await q.answer(); await q.message.edit_text("🔑 <b>Send the Brand Key / API Key</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data.startswith("gatewaymin:"):
        gw=data.split(":",1)[1]; context.user_data["admin_action"]="gateway_min"; context.user_data["gateway_target"]=gw; await q.answer(); await q.message.edit_text("💰 <b>Send Minimum Deposit Amount (BDT)</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data=="manualtoggle":
        on=await setting_bool("gateway:manual:enabled",True); await db.set_setting("gateway:manual:enabled","0" if on else "1"); await q.answer("Updated",show_alert=True); await admin_manual_payment(q); return
    if data=="manualmin": context.user_data["admin_action"]="manual_min"; await q.answer(); await q.message.edit_text("💰 <b>Send Manual Minimum Deposit Amount</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data.startswith("manualcfg:"): await q.answer(); await admin_manual_cfg(q,data.split(":",1)[1]); return
    if data.startswith("manualnum:"):
        m=data.split(":",1)[1]; context.user_data["admin_action"]="manual_num"; context.user_data["manual_target"]=m; await q.answer(); await q.message.edit_text(f"📱 <b>Set / Edit {m.title()} Number</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data.startswith("manualhide:"):
        m=data.split(":",1)[1]; on=await manual_method_enabled(m); await db.set_setting(f"manual:{m}:enabled","0" if on else "1"); await q.answer("Updated",show_alert=True); await admin_manual_cfg(q,m); return
    if data.startswith("manualdelete:"):
        m=data.split(":",1)[1]; await db.set_setting(f"manual:{m}:number",""); await db.set_setting(f"manual:{m}:enabled","0"); await q.answer("Deleted",show_alert=True); await admin_manual_cfg(q,m); return
    if data.startswith("binancecfg:"):
        typ=data.split(":",1)[1]; context.user_data["admin_action"]="binance_api" if typ=="api" else ("binance_sec" if typ=="sec" else "binance_rate"); await q.answer(); await q.message.edit_text("🔐 <b>Send Binance value</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data=="binancetest":
        api=await db.get_setting("manual:binance:api_key",""); sec=await db.get_setting("manual:binance:security_key","")
        if not api or not sec:
            await q.answer("API Identity Key and Security Key are required",show_alert=True); return
        try:
            body=json.dumps({"wallet":"FUNDING_WALLET","currency":"USDT"},separators=(",",":"))
            async with aiohttp.ClientSession() as session:
                async with session.post(BINANCE_BALANCE_URL,headers=binance_headers(api,sec,body),data=body,timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    raw=await resp.text(); dataj=json.loads(raw) if raw else {}
            if resp.status<400 and str(dataj.get("status","")).upper()=="SUCCESS":
                bal=(dataj.get("data") or {}).get("balance","?")
                await q.answer("Binance Pay API OK",show_alert=True)
                await q.message.reply_text(f"✅ <b>Binance Pay API is working.</b>\nFunding USDT balance: <code>{clean_html(str(bal))}</code>",parse_mode=ParseMode.HTML)
            else:
                err=dataj.get("errorMessage") or dataj.get("message") or raw[:500]
                await q.answer("Binance Pay API test failed",show_alert=True)
                await q.message.reply_text(f"❌ <b>Binance Pay API test failed.</b>\n<code>{clean_html(str(err)[:700])}</code>",parse_mode=ParseMode.HTML)
        except Exception as e:
            await q.answer("API test failed",show_alert=True)
            await q.message.reply_text(f"❌ <b>Binance Pay API test failed.</b>\n<code>{clean_html(str(e)[:700])}</code>",parse_mode=ParseMode.HTML)
        return
    if data=="binancetoggle":
        on=await manual_method_enabled("binance"); await db.set_setting("manual:binance:enabled","0" if on else "1"); await q.answer("Updated",show_alert=True); await admin_manual_cfg(q,"binance"); return
    if data.startswith("menutoggle:"):
        key=data.split(":",1)[1]; MENU_VISIBLE[key]=not MENU_VISIBLE.get(key,True); await db.set_setting("menu:hidden:"+key,"0" if MENU_VISIBLE[key] else "1"); await q.answer("Visible" if MENU_VISIBLE[key] else "Hidden",show_alert=True); await admin_menu_hide(q); return
    if data.startswith("menubtnedit:"):
        await q.answer(); await admin_menu_button_editor(q,data.split(":",1)[1]); return
    if data.startswith("menubtnname:"):
        key=data.split(":",1)[1]
        if key not in MENU_BUTTON_LABELS: await q.answer("Not found",show_alert=True); return
        context.user_data["admin_action"]="menu_button_name"; context.user_data["menu_button_key"]=key
        await q.answer(); await q.message.edit_text(f"✏️ <b>Edit Menu Button Name</b>\n\nCurrent: {clean_html(menu_label(key))}\n\nনতুন button name পাঠান:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data.startswith("menubtnemoji:"):
        key=data.split(":",1)[1]
        if key not in MENU_BUTTON_LABELS: await q.answer("Not found",show_alert=True); return
        context.user_data["admin_action"]="menu_button_emoji"; context.user_data["menu_button_key"]=key
        await q.answer(); await q.message.edit_text(f"✨ <b>Edit Menu Button Emoji</b>\n\nCurrent: {clean_html(BTN_EMO.get(key,'default'))}\n\nনতুন Premium Emoji ID পাঠান:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data=="textemoji:set":
        context.user_data["admin_action"]="text_emoji"
        await q.answer(); await q.message.edit_text(
            "✨ <b>Set Text Emoji Premium Map</b>\n\n"
            "এখন আপনার <b>Emoji Id</b> text file/document upload করুন।\n\n"
            "প্রতি লাইনে format হবে: <code>⚠️6206174450765796040</code>\n"
            "Bot normal emoji দেখে matching Premium Custom Emoji automatically ব্যবহার করবে।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])
        ); return
    if data=="textemoji:remove":
        global TEXT_EMOJI_ID, TEXT_EMOJI_MAP
        TEXT_EMOJI_ID=""; TEXT_EMOJI_MAP={}
        await db.set_setting("text_emoji_id","")
        await db.set_setting("text_emoji_map","{}")
        await q.answer("Text emoji map removed",show_alert=True); await admin_text_emoji(q); return

    if data.startswith("deliverymode:"):
        key=data.split(":",1)[1]; p=await db.get_product(key); new="manual" if str(p["delivery_mode"] or "auto")=="auto" else "auto"; await db.set_product_meta(key,delivery_mode=new); await q.answer("Manual Delivery" if new=="manual" else "Auto Delivery",show_alert=True);
        if product_kind(p)=="vpn": await admin_vpn_edit(q,key)
        else: await admin_proxy_edit(q,key)
        return

    if data == "admin:addproxy":
        await q.answer(); context.user_data["admin_action"]="addproxy_name"
        await q.message.edit_text("➕ <b>Add Proxy</b>\n\nপ্রথমে Proxy Name লিখুন, তারপর Next চাপুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addproxy_name_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addproxy_name_next":
        if not context.user_data.get("addproxy_name"): await q.answer("আগে Proxy Name লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addproxy_size"; await q.answer(); await q.message.edit_text("📦 <b>Proxy কত MB / GB হবে?</b>\nউদাহরণ: <code>100MB</code> / <code>200MB</code> / <code>1GB</code>\nতারপর Next চাপুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addproxy_size_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addproxy_size_next":
        if not context.user_data.get("addproxy_size"): await q.answer("আগে MB / GB size লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addproxy_price"; await q.answer(); await q.message.edit_text("💰 <b>Proxy Price লিখুন, তারপর Next চাপুন:</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addproxy_price_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addproxy_price_next":
        if context.user_data.get("addproxy_price") is None: await q.answer("আগে Price লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addproxy_checker"; await q.answer(); await q.message.edit_text("🔗 <b>Proxy Checker Bot দিন</b>\nনা থাকলে Skip করুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Skip","primary","link",callback_data="admin:addproxy_skip")],[btn("Next","success","check3",callback_data="admin:addproxy_checker_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addproxy_checker_next":
        if not context.user_data.get("addproxy_checker"): await q.answer("Checker না থাকলে Skip চাপুন",show_alert=True); return
        context.user_data["admin_action"]="addproxy_style"; await q.answer(); await q.message.edit_text("🎨 <b>Proxy Purchase Style নির্বাচন করুন</b>\n\n<b>Q - Style:</b> Quantity + Purchase Summary system\n<b>S - Style:</b> আগের Standard 1-Tap system",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Q - Style","primary","cart",callback_data="admin:addproxy_style_q"),btn("S - Style","success","server",callback_data="admin:addproxy_style_s")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addproxy_style_q":
        context.user_data["addproxy_style"]="Q"; context.user_data["admin_action"]="addproxy_save"; await q.answer(); await q.message.edit_text("💾 <b>Q - Style selected</b>\nSave Proxy চাপুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Save Proxy","success","check3",callback_data="admin:addproxy_save")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addproxy_style_s":
        context.user_data["addproxy_style"]="S"; context.user_data["admin_action"]="addproxy_save"; await q.answer(); await q.message.edit_text("💾 <b>S - Style selected</b>\nSave Proxy চাপুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Save Proxy","success","check3",callback_data="admin:addproxy_save")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addmail":
        await q.answer(); context.user_data["admin_action"]="addmail_name"
        await q.message.edit_text("➕ <b>Add Mail</b>\n\nপ্রথমে Mail Name লিখুন, তারপর Next চাপুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addmail_name_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data == "admin:addmail_name_next":
        if not context.user_data.get("addmail_name"): await q.answer("আগে Mail Name লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addmail_price"; await q.answer(); await q.message.edit_text("💰 <b>Mail Price লিখুন:</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addmail_price_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data == "admin:addmail_price_next":
        if context.user_data.get("addmail_price") is None: await q.answer("আগে Price লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addmail_emoji"; await q.answer(); await q.message.edit_text("✨ <b>Mail Premium Emoji ID লিখুন:</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addmail_emoji_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data == "admin:addmail_emoji_next":
        if not context.user_data.get("addmail_emoji"): await q.answer("আগে Emoji ID লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addmail_save"; await q.answer(); await q.message.edit_text("💾 <b>Mail Ready</b>\nSave Mail চাপুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Save Mail","success","check3",callback_data="admin:addmail_save")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addvpn":
        await q.answer(); context.user_data["admin_action"]="addvpn_name"
        await q.message.edit_text("➕ <b>Add VPN</b>\n\nপ্রথমে VPN Name লিখুন, তারপর Next চাপুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addvpn_name_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addvpn_name_next":
        if not context.user_data.get("addvpn_name"): await q.answer("আগে VPN Name লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addvpn_days"; await q.answer(); await q.message.edit_text("📅 <b>VPN DAY SELECTED লিখুন</b>\nউদাহরণ: 7 DAY / 30 DAY\nতারপর Next চাপুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addvpn_days_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addvpn_days_next":
        if not context.user_data.get("addvpn_days"): await q.answer("আগে VPN Day লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addvpn_price"; await q.answer(); await q.message.edit_text("💰 <b>VPN Price লিখুন, তারপর Next চাপুন:</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addvpn_price_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addvpn_price_next":
        if context.user_data.get("addvpn_price") is None: await q.answer("আগে Price লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addvpn_emoji"; await q.answer(); await q.message.edit_text("✨ <b>VPN Premium Emoji ID লিখুন, তারপর Next চাপুন:</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addvpn_emoji_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addvpn_emoji_next":
        if not context.user_data.get("addvpn_emoji"): await q.answer("আগে Emoji ID লিখুন",show_alert=True); return
        context.user_data["admin_action"]="addvpn_save"; await q.answer(); await q.message.edit_text("💾 <b>VPN Ready</b>\nSave VPN চাপুন:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Save VPN","success","check3",callback_data="admin:addvpn_save")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:setwelcome":
        await q.answer()
        context.user_data["admin_action"] = "set_welcome"
        await q.message.edit_text(
            "✏️ <b>Set Welcome Message</b>\n\n"
            "এখন /start-এ যে message দেখাতে চান সেটি পাঠান।\n"
            "Premium custom emoji-সহ message পাঠানো যাবে।\n\n"
            "নিচের Cancel button দিয়ে বাতিল করতে পারবেন।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")
            ]])
        )
        return

    if data == "admin:support":
        await q.answer()
        await admin_support_management(q)
        return

    if data == "admin:support_add":
        await q.answer()
        context.user_data["admin_action"] = "support_add"
        await q.message.edit_text(
            "➕ <b>Add Support Admin</b>\n\n"
            "Support username পাঠান। উদাহরণ: <code>@PROXY_X_AGENT</code>\n"
            "অথবা Telegram link: <code>https://t.me/username</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")]])
        )
        return

    if data.startswith("admin:support_delete:"):
        try:
            idx = int(data.rsplit(":", 1)[1])
        except Exception:
            await q.answer("Invalid support", show_alert=True); return
        admins = await get_support_admins()
        if idx < 0 or idx >= len(admins):
            await q.answer("Support not found", show_alert=True); return
        deleted = admins.pop(idx)
        # Keep one fallback support if the admin deletes every configured entry.
        if not admins:
            admins = []
        await save_support_admins(admins)
        await q.answer(f"Deleted {deleted}", show_alert=True)
        await admin_support_management(q)
        return

    if data == "admin:forcejoin":
        await q.answer()
        await admin_force_join(q)
        return

    if data == "admin:forcejoin_add":
        await q.answer()
        context.user_data["admin_action"] = "forcejoin_add"
        await q.message.edit_text(
            "➕ <b>Add Force Join Channel</b>\n\n"
            "Channel username পাঠান।\n"
            "উদাহরণ: <code>@secretproxyupdate</code>\n"
            "অথবা <code>https://t.me/channelname</code>\n\n"
            "তারপর নিচের Cancel button দিয়ে চাইলে বাতিল করতে পারবেন।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")
            ]])
        )
        return

    if data == "admin:forcejoin_toggle":
        enabled = await force_join_enabled()
        await db.set_setting("force_join_enabled", "0" if enabled else "1")
        await q.answer("Force Join " + ("Disabled" if enabled else "Enabled"), show_alert=True)
        await admin_force_join(q)
        return

    if data.startswith("admin:forcejoin_delete:"):
        try:
            idx = int(data.rsplit(":", 1)[1])
        except Exception:
            await q.answer("Invalid channel", show_alert=True); return
        channels = await get_force_join_channels()
        if idx < 0 or idx >= len(channels):
            await q.answer("Channel not found", show_alert=True); return
        deleted = channels.pop(idx)
        await save_force_join_channels(channels)
        if not channels:
            await db.set_setting("force_join_enabled", "0")
        await q.answer(f"Deleted {deleted}", show_alert=True)
        await admin_force_join(q)
        return

    if data == "admin:admins":
        await q.answer(); await admin_admins(q); return

    if data.startswith("admin:deladmin:"):
        uid=int(data.rsplit(":",1)[1]); ok=await db.remove_admin(uid); await q.answer("Deleted" if ok else "Protected",show_alert=True); await admin_admins(q); return

    if data == "admin:emoji":
        await q.answer(); await admin_emoji(q); return

    if data.startswith("emoji:set:"):
        key=data.split(":",2)[2]; context.user_data["emoji_key"]=key; context.user_data["admin_action"]="emoji_button"
        await q.answer(); await q.message.edit_text(f"✨ <b>{clean_html(EMOJI_BUTTON_LABELS.get(key,key))}</b>\n\nনতুন Premium Emoji ID পাঠান:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data.startswith("emoji:product:"):
        key=data.split(":",2)[2]; context.user_data["emoji_product_key"]=key; context.user_data["admin_action"]="emoji_product"
        await q.answer(); await q.message.edit_text("✨ <b>Product Emoji</b>\n\nনতুন Premium Emoji ID পাঠান:",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addproxy_skip":
        await q.answer(); context.user_data["admin_action"]="addproxy_style"; context.user_data["addproxy_checker"]=None
        await q.message.edit_text("🎨 <b>Proxy Purchase Style নির্বাচন করুন</b>\n\n<b>Q - Style:</b> Quantity + Purchase Summary system\n<b>S - Style:</b> আগের Standard 1-Tap system",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Q - Style","primary","cart",callback_data="admin:addproxy_style_q"),btn("S - Style","success","server",callback_data="admin:addproxy_style_s")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return

    if data == "admin:addproxy_save":
        name=context.user_data.get("addproxy_name"); size=context.user_data.get("addproxy_size"); price=context.user_data.get("addproxy_price"); checker=context.user_data.get("addproxy_checker"); style=context.user_data.get("addproxy_style","S")
        if not name or not size or price is None or style not in ("Q","S"): await q.answer("Incomplete",show_alert=True); return
        key="proxy_"+uuid.uuid4().hex[:12]
        await db.create_product(key,name,price,"proxy",name,checker,None,"🛍",None,size,style)
        for k in list(context.user_data):
            if k.startswith("addproxy_") or k=="admin_action": context.user_data.pop(k,None)
        await q.answer("Proxy saved",show_alert=True); await q.message.edit_text("✅ <b>Proxy successfully added!</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Edit Proxy","primary","tag",callback_data="admin:editproxy")],[btn("Admin Panel","primary","bot",callback_data="admin:panel")]])); return

    if data == "admin:addmail_save":
        name=context.user_data.get("addmail_name"); price=context.user_data.get("addmail_price"); eid=context.user_data.get("addmail_emoji")
        if not name or price is None or not eid: await q.answer("Incomplete",show_alert=True); return
        key="mail_"+uuid.uuid4().hex[:12]; await db.create_product(key,name,price,"mail",name,None,eid,"📧")
        for k in list(context.user_data):
            if k.startswith("addmail_") or k=="admin_action": context.user_data.pop(k,None)
        await q.answer("Mail saved",show_alert=True); await q.message.edit_text("✅ <b>Mail successfully added!</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Edit Mail","primary","tag",callback_data="admin:editmail")],[btn("Admin Panel","primary","bot",callback_data="admin:panel")]])); return

    if data == "admin:addvpn_save":
        name=context.user_data.get("addvpn_name"); days=context.user_data.get("addvpn_days"); price=context.user_data.get("addvpn_price"); eid=context.user_data.get("addvpn_emoji")
        if not name or not days or price is None or not eid: await q.answer("Incomplete",show_alert=True); return
        key="vpn_"+uuid.uuid4().hex[:12]; full_name=f"{name} VPN" if not name.upper().endswith("VPN") else name
        await db.create_product(key,full_name,price,"vpn",full_name,None,eid,"🛡",days)
        for k in list(context.user_data):
            if k.startswith("addvpn_") or k=="admin_action": context.user_data.pop(k,None)
        await q.answer("VPN saved",show_alert=True); await q.message.edit_text("✅ <b>VPN successfully added!</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Edit VPN","primary","tag",callback_data="admin:editvpn")],[btn("Admin Panel","primary","bot",callback_data="admin:panel")]])); return

    if data == "admin:panel":
        await q.answer()
        await q.message.reply_text(
            "👑 <b>Admin Pannel</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_kb()
        )
        return

    if data == "admin:stats":
        await q.answer()
        await admin_stats(q)
        return

    if data == "admin:stock":
        await q.answer()
        await admin_stock(q)
        return

    if data == "admin:vpnstock":
        await q.answer()
        await admin_vpn_stock(q)
        return

    if data == "admin:mailstock":
        await q.answer(); await admin_mail_stock(q); return

    if data == "admin:editmail":
        await q.answer(); await admin_edit_mail(q); return

    if data == "admin:editproxy":
        await q.answer()
        await admin_edit_proxy(q)
        return

    if data == "admin:broadcast":
        await q.answer()
        context.user_data["broadcast_mode"] = True
        await q.message.reply_text(
            "📢 <b>Broadcast Mode</b>\n\n"
            "এখন যে কোনো <b>text, photo, video, document, audio, voice, animation, "
            "forwarded/copied message বা premium emoji</b> পাঠান।\n\n"
            "বাতিল করতে /cancel লিখুন।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")
            ]])
        )
        return

    if data == "admin:banunban":
        await q.answer()
        context.user_data["admin_action"] = "banunban"
        await q.message.reply_text(
            "🚫 <b>Ban / Unban User</b>\n\n"
            "User ID পাঠান। উদাহরণ:\n"
            "<code>123456789</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")
            ]])
        )
        return

    if data == "admin:addadmin":
        await q.answer()
        context.user_data["admin_action"] = "addadmin"
        await q.message.reply_text(
            "👑 <b>Add Admin</b>\n\nUser ID পাঠান।\n"
            "Admin করলে তার কাছে সব Admin permission থাকবে।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")
            ]])
        )
        return

    if data.startswith("admin:deleteproxy:"):
        key = data.split(":", 2)[2]
        p = await db.get_product(key)
        if not p or not str(key).startswith("proxy_"):
            await q.answer("Proxy not found", show_alert=True)
            return
        await q.answer()
        await q.message.edit_text(
            "⚠️ <b>Delete Proxy?</b>\n\n"
            f"<b>{clean_html(p['name'])}</b> এবং এর অবশিষ্ট stock permanently delete হবে।\n"
            "Purchase history/order records থাকবে।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [btn("Confirm Delete", "danger", "cross", callback_data=f"admin:deleteproxy_confirm:{key}")],
                [btn("Cancel", "primary", "check", callback_data=f"editproxy:{key}")],
            ])
        )
        return

    if data.startswith("admin:deleteproxy_confirm:"):
        key = data.split(":", 2)[2]
        p = await db.get_product(key)
        if not p or not str(key).startswith("proxy_"):
            await q.answer("Proxy not found", show_alert=True)
            return
        await db.delete_product(key)
        await q.answer("Proxy deleted", show_alert=True)
        await admin_edit_proxy(q)
        return

    if data == "admin:editvpn":
        await q.answer()
        await admin_edit_vpn(q)
        return

    if data.startswith("editmail:") or data.startswith("mailstockedit:"):
        await q.answer(); await admin_mail_edit(q,data.split(":",1)[1]); return
    if data.startswith("stockedit:"):
        await q.answer(); await admin_proxy_edit(q,data.split(":",1)[1]); return
    if data.startswith("mailhide:"):
        key=data.split(":",1)[1]; p=await db.get_product(key)
        if not p or product_kind(p)!="mail": await q.answer("Mail not found",show_alert=True); return
        await db.set_product_hidden(key,not bool(p["hidden"])); await q.answer("Updated",show_alert=True); await admin_mail_edit(q,key); return
    if data.startswith("maileditname:"):
        context.user_data["admin_action"]="mail_edit_name"; context.user_data["mail_edit_key"]=data.split(":",1)[1]; await q.answer(); await q.message.edit_text("✏️ <b>Send new Mail Name</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data.startswith("mailemoji:"):
        context.user_data["admin_action"]="mail_edit_emoji"; context.user_data["mail_edit_key"]=data.split(":",1)[1]; await q.answer(); await q.message.edit_text("✨ <b>Send new Premium Emoji ID</b>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return
    if data.startswith("deletemail:"):
        key=data.split(":",1)[1]; p=await db.get_product(key)
        if not p or product_kind(p)!="mail": await q.answer("Mail not found",show_alert=True); return
        await q.answer(); await q.message.edit_text(f"⚠️ <b>Delete {clean_html(p['name'])}?</b>\n\nIts remaining stock will also be deleted.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Confirm Delete","danger","cross",callback_data=f"deletemailconfirm:{key}")],[btn("Cancel","primary","check",callback_data=f"editmail:{key}")]])); return
    if data.startswith("deletemailconfirm:"):
        key=data.split(":",1)[1]; p=await db.get_product(key)
        if not p or product_kind(p)!="mail": await q.answer("Mail not found",show_alert=True); return
        await db.delete_product(key); await q.answer("Mail deleted",show_alert=True); await admin_edit_mail(q); return

    if data.startswith("proxyeditname:"):
        key = data.split(":",1)[1]
        p = await db.get_product(key)
        if not p or product_kind(p) == "vpn":
            await q.answer("Proxy not found", show_alert=True); return
        context.user_data["admin_action"] = "proxy_edit_name"
        context.user_data["product_edit_key"] = key
        await q.answer()
        await q.message.edit_text("✏️ <b>Send new Proxy Name</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]]))
        return

    if data.startswith("vpneditname:"):
        key = data.split(":",1)[1]
        p = await db.get_product(key)
        if not p or product_kind(p) != "vpn":
            await q.answer("VPN not found", show_alert=True); return
        context.user_data["admin_action"] = "vpn_edit_name"
        context.user_data["product_edit_key"] = key
        await q.answer()
        await q.message.edit_text("✏️ <b>Send new VPN Name</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]]))
        return

    if data.startswith("editproxy:"):
        await q.answer()
        await admin_proxy_edit(q, data.split(":", 1)[1])
        return

    if data.startswith("editvpn:"):
        await q.answer()
        await admin_vpn_edit(q, data.split(":", 1)[1])
        return

    if data.startswith("togglevpn:"):
        key = data.split(":", 1)[1]
        p = await db.get_product(key)
        await db.set_product_hidden(key, not bool(p["hidden"]))
        await q.answer("Updated")
        await admin_vpn_edit(q, key)
        return

    if data.startswith("vpnstockedit:"):
        key = data.split(":", 1)[1]
        await q.answer()
        await admin_vpn_edit(q, key)
        return

    if data.startswith("togglehide:"):
        key = data.split(":", 1)[1]
        p = await db.get_product(key)
        await db.set_product_hidden(key, not bool(p["hidden"]))
        await q.answer("Updated")
        await admin_proxy_edit(q, key)
        return

    if data.startswith("editprice:"):
        key = data.split(":", 1)[1]
        context.user_data["edit_price_key"] = key
        await q.answer()
        await q.message.reply_text(
            "💰 নতুন Price লিখুন। উদাহরণ: <code>150</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")
            ]])
        )
        return

    if data.startswith("addstock:"):
        key = data.split(":", 1)[1]
        context.user_data["stock_input_key"] = key
        await q.answer()
        p = await db.get_product(key)
        prompt = ("📥 <b>Mail Stock Upload</b>\n\nExcel <code>.xlsx</code> file পাঠাতে পারেন। অথবা text message-এ <code>1/ fullmaildetails</code>\n<code>2/ fullmaildetails</code> এভাবে দিন। প্রতিটি row/line একটি আলাদা mail stock হবে।\n\n/cancel দিয়ে বাতিল করুন।" if p and product_kind(p)=="mail" else "📥 Stock পাঠান।\n\nText হলে প্রতিটি proxy/key আলাদা line/block হিসেবে দিন।\nঅথবা <b>.txt</b> file পাঠান।\n\n/cancel দিয়ে বাতিল করুন।")
        await q.message.reply_text(
            prompt,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")
            ]])
        )
        return

    if data.startswith("clearstock:"):
        key = data.split(":", 1)[1]
        await db.clear_stock(key)
        await q.answer("Stock cleared")
        await q.message.reply_text("🗑️ Stock সফলভাবে clear করা হয়েছে।")
        return


# ============================================================
# BAN/UNBAN / ADD ADMIN / PRICE / STOCK COMMANDS
# ============================================================

async def handle_admin_text_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("admin_action")
    if not action:
        return False
    if not await is_admin(update.effective_user.id):
        context.user_data.pop("admin_action", None)
        return False

    text = (update.message.text or "").strip()

    if action == "manual_order_details":
        oid=context.user_data.get("manual_order_id")
        if not oid:
            context.user_data.pop("admin_action",None)
            await update.message.reply_text("❌ Manual order session expired.")
            return True
        row=await db.get_manual_order(oid)
        if not row or row["status"]!="pending":
            context.user_data.pop("admin_action",None); context.user_data.pop("manual_order_id",None)
            await update.message.reply_text("❌ This manual order is no longer pending.")
            return True
        if not text:
            await update.message.reply_text("❌ Details cannot be empty.")
            return True
        await db.execute("UPDATE manual_orders SET draft_details=? WHERE order_id=?",(text,oid))
        context.user_data.pop("admin_action",None)
        context.user_data.pop("manual_order_id",None)
        await update.message.reply_text("📋 <b>Details received.</b>\n\n<code>"+clean_html(text)+"</code>\n\nReview them below. You can Confirm or Edit Details.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Confirm","success","check3",callback_data=f"manualorder:confirm:{oid}"),btn("Edit Details","primary","tag",callback_data=f"manualorder:edit:{oid}")],[btn("Cancel","danger","cross",callback_data="manualorder:cancel_details")]]))
        return True

    if action == "forcejoin_add":
        channel = normalize_channel(text)
        if channel == "@":
            await update.message.reply_text("❌ সঠিক Channel username দিন।")
            return True
        channels = await get_force_join_channels()
        if channel in channels:
            await update.message.reply_text("⚠️ এই channel আগেই added আছে।")
            return True
        # Validate the channel before saving it.
        try:
            await context.bot.get_chat(channel)
        except Exception:
            await update.message.reply_text(
                "❌ Channel পাওয়া যায়নি। Username/Link ঠিক আছে কিনা দেখুন এবং bot-কে channel-এ প্রয়োজনীয় permission দিন।"
            )
            return True
        channels.append(channel)
        await save_force_join_channels(channels)
        await db.set_setting("force_join_enabled", "1")
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(
            f"✅ <b>{clean_html(channel)}</b> Force Join Channel হিসেবে save হয়েছে।\n\n"
            "🟢 Force Join বর্তমানে Enabled.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn("Force Channel Join", "primary", "link", callback_data="admin:forcejoin"),
                btn("Admin Panel", "primary", "bot", callback_data="admin:panel")
            ]])
        )
        return True

    if action == "support_add":
        value = text
        if not value:
            await update.message.reply_text("❌ Support username/link দিন।")
            return True
        if not (value.startswith("@") or "t.me/" in value):
            await update.message.reply_text("❌ @username অথবা https://t.me/username দিন।")
            return True
        admins = await get_support_admins()
        admins = await save_support_admins(admins + [value])
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(
            f"✅ <b>Support Admin added:</b> {clean_html(admins[-1])}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Support Management", "primary", "chat", callback_data="admin:support")],[btn("Admin Panel", "primary", "bot", callback_data="admin:panel")]])
        )
        return True

    if action == "gateway_key":
        gw=context.user_data.get("gateway_target"); value=text.strip()
        if not value: await update.message.reply_text("❌ Key cannot be empty."); return True
        await db.set_setting(f"gateway:{gw}:key",value); context.user_data.pop("admin_action",None); context.user_data.pop("gateway_target",None)
        await update.message.reply_text("✅ <b>Gateway key saved successfully.</b>",parse_mode=ParseMode.HTML); return True
    if action == "gateway_min":
        val=parse_amount(text); gw=context.user_data.get("gateway_target")
        if val is None or val<=0: await update.message.reply_text("❌ Enter a valid amount."); return True
        await db.set_setting(f"gateway:{gw}:minimum",str(val)); context.user_data.pop("admin_action",None); context.user_data.pop("gateway_target",None); await update.message.reply_text("✅ <b>Minimum deposit updated.</b>",parse_mode=ParseMode.HTML); return True
    if action == "manual_min":
        val=parse_amount(text)
        if val is None or val<=0: await update.message.reply_text("❌ Enter a valid amount."); return True
        await db.set_setting("manual:min_deposit",str(val)); context.user_data.pop("admin_action",None); await update.message.reply_text("✅ <b>Manual minimum deposit updated.</b>",parse_mode=ParseMode.HTML); return True
    if action == "manual_num":
        m=context.user_data.get("manual_target"); value=text.strip()
        if not value: await update.message.reply_text("❌ Number cannot be empty."); return True
        await db.set_setting(f"manual:{m}:number",value); await db.set_setting(f"manual:{m}:enabled","1"); context.user_data.pop("admin_action",None); context.user_data.pop("manual_target",None); await update.message.reply_text(f"✅ <b>{m.title()} number saved.</b>",parse_mode=ParseMode.HTML); return True
    if action == "binance_api":
        await db.set_setting("manual:binance:api_key",text.strip()); context.user_data.pop("admin_action",None); await update.message.reply_text("✅ <b>Binance Pay API Identity Key saved.</b>",parse_mode=ParseMode.HTML); return True
    if action == "binance_sec":
        await db.set_setting("manual:binance:security_key",text.strip()); context.user_data.pop("admin_action",None); await update.message.reply_text("✅ <b>Binance Security Key saved.</b>",parse_mode=ParseMode.HTML); return True
    if action == "binance_rate":
        val=parse_amount(text)
        if val is None or val<=0: await update.message.reply_text("❌ Enter a valid dollar rate."); return True
        await db.set_setting("manual:binance:rate",str(val)); context.user_data.pop("admin_action",None); await update.message.reply_text("✅ <b>Binance Dollar RT saved.</b>",parse_mode=ParseMode.HTML); return True

    # Custom /start welcome message. text_html preserves Telegram entities,
    # including pasted premium custom emojis, while escaping normal text safely.
    if action == "set_welcome":
        welcome_html = getattr(update.message, "text_html", None) or text
        if not welcome_html:
            await update.message.reply_text("❌ Welcome message খালি হতে পারবে না।")
            return True
        await db.set_setting("welcome_message", welcome_html)
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(
            "✅ <b>/start Welcome Message successfully updated!</b>\n\n"
            "এখন থেকে নতুন Welcome Message /start-এ দেখাবে।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                btn("Admin Panel", "primary", "bot", callback_data="admin:panel")
            ]])
        )
        return True

    # Promo Code wizard (Admin Panel -> Set Promo Code).
    if action == "promo_percent":
        try:
            val = float(re.sub(r"[%\s]", "", text))
        except Exception:
            val = None
        if val is None or not (0 < val < 100):
            await update.message.reply_text("❌ 1 থেকে 99 এর মধ্যে একটি সংখ্যা লিখুন। উদাহরণ: 5"); return True
        context.user_data["promo_percent"] = val
        await update.message.reply_text(f"✅ Discount <b>{val:g}%</b> saved. এখন Next চাপুন।", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Next", "success", "check3", callback_data="admin:promo_pct_next")], [btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")]]))
        return True
    if action == "promo_code":
        if not re.fullmatch(r"[A-Za-z0-9]{3,20}", text or ""):
            await update.message.reply_text("❌ Promo Code ৩–২০ অক্ষরের হতে হবে (শুধু সংখ্যা/ইংরেজি অক্ষর, space ছাড়া)।"); return True
        context.user_data["promo_code"] = text
        await update.message.reply_text(f"✅ Promo Code <code>{clean_html(text)}</code> ready. Save চাপুন।", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Save", "success", "check3", callback_data="admin:promo_save")], [btn("Cancel", "danger", "cross", callback_data="admin:cancel_action")]]))
        return True

    # Dynamic Proxy creation wizard.
    if action == "addproxy_name":
        if not text: await update.message.reply_text("❌ Proxy name দিন।"); return True
        context.user_data["addproxy_name"]=text
        await update.message.reply_text("✅ Proxy Name saved. এখন Proxy Size (MB / GB) লিখুন।",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addproxy_name_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True
    if action == "addproxy_size":
        if not text: await update.message.reply_text("❌ Proxy size দিন, যেমন 100MB / 1GB।"); return True
        size=re.sub(r"\s+","",text.upper())
        if not re.fullmatch(r"\d+(?:MB|GB)", size): await update.message.reply_text("❌ সঠিক size দিন। উদাহরণ: 100MB / 1GB"); return True
        context.user_data["addproxy_size"]=size
        await update.message.reply_text("✅ Proxy Size saved. এখন Next চাপুন।",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addproxy_size_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True
    if action == "addproxy_price":
        price=parse_amount(text)
        if price is None: await update.message.reply_text("❌ সঠিক price দিন।"); return True
        context.user_data["addproxy_price"]=price
        await update.message.reply_text("✅ Price saved. এখন Next চাপুন।",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addproxy_price_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True
    if action == "addproxy_checker":
        _fixed = normalize_checker_url(text)
        if not _fixed:
            await update.message.reply_text("❌ সঠিক checker bot link দিন।\nউদাহরণ: <code>https://t.me/YourCheckerBot</code> অথবা <code>@YourCheckerBot</code>", parse_mode=ParseMode.HTML); return True
        context.user_data["addproxy_checker"]=_fixed
        await update.message.reply_text("✅ Checker saved. এখন Next চাপুন।",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addproxy_checker_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True

    # Dynamic Mail creation/edit wizard.
    if action == "addmail_name":
        if not text: await update.message.reply_text("❌ Mail name দিন।"); return True
        context.user_data["addmail_name"]=text; await update.message.reply_text("✅ Mail Name saved. এখন Next চাপুন.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addmail_name_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True
    if action == "addmail_price":
        price=parse_amount(text)
        if price is None or price<0: await update.message.reply_text("❌ সঠিক price দিন।"); return True
        context.user_data["addmail_price"]=price; await update.message.reply_text("✅ Price saved. এখন Next চাপুন.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addmail_price_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True
    if action == "addmail_emoji":
        if not text.isdigit(): await update.message.reply_text("❌ numeric Premium Emoji ID দিন।"); return True
        context.user_data["addmail_emoji"]=text; await update.message.reply_text("✅ Emoji ID saved. এখন Next চাপুন.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addmail_emoji_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True
    if action == "proxy_edit_name":
        key=context.user_data.get("product_edit_key")
        if not text: await update.message.reply_text("❌ Proxy name cannot be empty."); return True
        p=await db.get_product(key) if key else None
        if not p or product_kind(p)=="vpn":
            context.user_data.pop("product_edit_key",None); context.user_data.pop("admin_action",None)
            await update.message.reply_text("❌ Proxy product not found."); return True
        await db.set_product_meta(key,name=text.strip(),menu_name=text.strip())
        context.user_data.pop("product_edit_key",None); context.user_data.pop("admin_action",None)
        await update.message.reply_text("✅ <b>Proxy name updated.</b>",parse_mode=ParseMode.HTML)
        return True
    if action == "vpn_edit_name":
        key=context.user_data.get("product_edit_key")
        if not text: await update.message.reply_text("❌ VPN name cannot be empty."); return True
        p=await db.get_product(key) if key else None
        if not p or product_kind(p)!="vpn":
            context.user_data.pop("product_edit_key",None); context.user_data.pop("admin_action",None)
            await update.message.reply_text("❌ VPN product not found."); return True
        await db.set_product_meta(key,name=text.strip(),menu_name=text.strip())
        context.user_data.pop("product_edit_key",None); context.user_data.pop("admin_action",None)
        await update.message.reply_text("✅ <b>VPN name updated.</b>",parse_mode=ParseMode.HTML)
        return True
    if action == "mail_edit_name":
        key=context.user_data.get("mail_edit_key")
        if not text: await update.message.reply_text("❌ Name cannot be empty."); return True
        await db.set_product_meta(key,name=text,menu_name=text); context.user_data.pop("mail_edit_key",None); context.user_data.pop("admin_action",None); await update.message.reply_text("✅ <b>Mail name updated.</b>",parse_mode=ParseMode.HTML); return True
    if action == "mail_edit_emoji":
        key=context.user_data.get("mail_edit_key")
        if not text.isdigit(): await update.message.reply_text("❌ numeric Premium Emoji ID দিন।"); return True
        await db.set_product_meta(key,emoji_id=text); context.user_data.pop("mail_edit_key",None); context.user_data.pop("admin_action",None); await update.message.reply_text("✅ <b>Mail emoji updated.</b>",parse_mode=ParseMode.HTML); return True

    # Dynamic VPN creation wizard.
    if action == "addvpn_name":
        if not text: await update.message.reply_text("❌ VPN name দিন।"); return True
        context.user_data["addvpn_name"]=text
        await update.message.reply_text("✅ VPN Name saved. এখন Next চাপুন।",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addvpn_name_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True
    if action == "addvpn_days":
        if not text: await update.message.reply_text("❌ VPN DAY দিন।"); return True
        context.user_data["addvpn_days"]=text
        await update.message.reply_text("✅ VPN Day saved. এখন Next চাপুন।",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addvpn_days_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True
    if action == "addvpn_price":
        price=parse_amount(text)
        if price is None: await update.message.reply_text("❌ সঠিক price দিন।"); return True
        context.user_data["addvpn_price"]=price
        await update.message.reply_text("✅ Price saved. এখন Next চাপুন।",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addvpn_price_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True
    if action == "addvpn_emoji":
        if not text.isdigit(): await update.message.reply_text("❌ সঠিক numeric Premium Emoji ID দিন।"); return True
        context.user_data["addvpn_emoji"]=text
        await update.message.reply_text("✅ Emoji ID saved. এখন Next চাপুন।",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Next","success","check3",callback_data="admin:addvpn_emoji_next")],[btn("Cancel","danger","cross",callback_data="admin:cancel_action")]])); return True

    if action == "text_emoji":
        global TEXT_EMOJI_ID
        eid=None
        for ent in (getattr(update.message,"entities",None) or []):
            if getattr(ent,"type",None)=="custom_emoji" and getattr(ent,"custom_emoji_id",None):
                eid=ent.custom_emoji_id; break
        st=getattr(update.message,"sticker",None)
        if not eid and st and getattr(st,"custom_emoji_id",None): eid=st.custom_emoji_id
        if not eid:
            await update.message.reply_text("❌ Premium Custom Emoji পাওয়া যায়নি। একটি Premium Emoji message বা custom-emoji sticker পাঠান। সাধারণ image/file দিয়ে custom emoji ID পাওয়া যায় না.")
            return True
        TEXT_EMOJI_ID=str(eid); await db.set_setting("text_emoji_id",TEXT_EMOJI_ID)
        context.user_data.pop("admin_action",None)
        await update.message.reply_text("✅ <b>Text Emoji saved.</b>",parse_mode=ParseMode.HTML); return True

    if action == "menu_button_name":
        key=context.user_data.get("menu_button_key")
        if key not in MENU_BUTTON_LABELS or not text:
            await update.message.reply_text("❌ Button name cannot be empty."); return True
        MENU_BUTTON_NAMES[key]=text.strip()
        await db.set_setting("menu:name:"+key,text.strip())
        context.user_data.pop("menu_button_key",None); context.user_data.pop("admin_action",None)
        await update.message.reply_text("✅ <b>Main Menu button name updated.</b>",parse_mode=ParseMode.HTML); return True
    if action == "menu_button_emoji":
        key=context.user_data.get("menu_button_key")
        if key not in MENU_BUTTON_LABELS or not text.isdigit():
            await update.message.reply_text("❌ numeric Premium Emoji ID দিন।"); return True
        BTN_EMO[key]=text.strip(); await db.set_setting("emoji:button:"+key,text.strip())
        context.user_data.pop("menu_button_key",None); context.user_data.pop("admin_action",None)
        await update.message.reply_text("✅ <b>Main Menu button emoji updated.</b>",parse_mode=ParseMode.HTML); return True

    if action == "emoji_button":
        if not text.isdigit(): await update.message.reply_text("❌ numeric Premium Emoji ID দিন।"); return True
        key=context.user_data.get("emoji_key"); BTN_EMO[key]=text; await db.set_setting("emoji:button:"+key,text)
        context.user_data.pop("emoji_key",None); context.user_data.pop("admin_action",None)
        await update.message.reply_text("✅ <b>Button emoji successfully updated.</b>",parse_mode=ParseMode.HTML); return True
    if action == "emoji_product":
        if not text.isdigit(): await update.message.reply_text("❌ numeric Premium Emoji ID দিন।"); return True
        key=context.user_data.get("emoji_product_key"); await db.set_product_meta(key,emoji_id=text)
        context.user_data.pop("emoji_product_key",None); context.user_data.pop("admin_action",None)
        await update.message.reply_text("✅ <b>Product emoji successfully updated.</b>",parse_mode=ParseMode.HTML); return True

    try:
        uid = int(text)
    except Exception:
        await update.message.reply_text("❌ সঠিক numeric User ID দিন।")
        return True

    if action == "banunban":
        row = await db.get_user(uid)
        if not row:
            await update.message.reply_text("❌ এই User ID database-এ পাওয়া যায়নি।")
        else:
            new_value = not bool(row["banned"])
            await db.ban(uid, new_value)
            await update.message.reply_text(
                f"{'🚫 User Banned' if new_value else '✅ User Unbanned'}\n"
                f"User ID: <code>{uid}</code>",
                parse_mode=ParseMode.HTML
            )
        context.user_data.pop("admin_action", None)
        return True

    if action == "addadmin":
        row = await db.get_user(uid)
        if not row:
            await update.message.reply_text(
                "⚠️ User আগে bot /start করে থাকলে Add Admin করা যাবে।"
            )
        else:
            await db.add_admin(uid, update.effective_user.id)
            await update.message.reply_text(
                f"👑 <b>Admin added successfully.</b>\nUser ID: <code>{uid}</code>",
                parse_mode=ParseMode.HTML
            )
            try:
                await context.bot.send_message(
                    uid,
                    "👑 <b>আপনাকে Secret Proxy Shop-এর Admin করা হয়েছে।</b>\n"
                    "আপনার Admin Panel-এ সব permission চালু আছে।",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_menu(True),
                )
            except Exception:
                pass
        context.user_data.pop("admin_action", None)
        return True

    return False


# ============================================================
# STOCK TEXT PARSER
# ============================================================

def parse_stock_text(raw: str):
    raw = (raw or "").replace("\r\n", "\n").strip()
    if not raw:
        return []

    # Original addstock commands use 1/, 2/, 3/ blocks.
    matches = list(re.finditer(r"(?m)^\s*\d+\s*/\s*", raw))
    if matches:
        blocks = []
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i+1].start() if i+1 < len(matches) else len(raw)
            block = raw[start:end].strip()
            if block:
                blocks.append(block)
        if blocks:
            return blocks

    # Blank-line-separated blocks are ideal for multi-line proxy records.
    parts = [x.strip() for x in re.split(r"\n\s*\n+", raw) if x.strip()]
    if len(parts) > 1:
        return parts

    # If each line is a simple IP:Port:User:Pass/key, each line is one stock.
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    if len(lines) > 1 and all(":" in x for x in lines):
        return lines

    return [raw]


def parse_xlsx_mail_stock(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared=[]
        if "xl/sharedStrings.xml" in z.namelist():
            root=ET.fromstring(z.read("xl/sharedStrings.xml")); ns={"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in root.findall("m:si",ns): shared.append("".join(t.text or "" for t in si.findall(".//m:t",ns)))
        ns={"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main","r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
        wb=ET.fromstring(z.read("xl/workbook.xml")); rel=ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        relmap={x.attrib.get("Id"):x.attrib.get("Target") for x in rel}; sheet=wb.find("m:sheets/m:sheet",ns)
        if sheet is None: return []
        rid=sheet.attrib.get("{%s}id"%ns["r"]); target=relmap.get(rid,"worksheets/sheet1.xml")
        if target.startswith("/"): target=target.lstrip("/")
        elif not target.startswith("xl/"): target="xl/"+target
        root=ET.fromstring(z.read(target)); rows=[]
        for row in root.findall(".//m:sheetData/m:row",ns):
            vals=[]
            for cell in row.findall("m:c",ns):
                typ=cell.attrib.get("t"); v=cell.find("m:v",ns); val=""
                if typ=="inlineStr": val="".join(t.text or "" for t in cell.findall(".//m:t",ns))
                elif v is not None:
                    val=v.text or ""
                    if typ=="s":
                        try: val=shared[int(val)]
                        except Exception: pass
                if str(val).strip(): vals.append(str(val).strip())
            if vals: rows.append("|".join(vals))
        return rows

async def process_stock_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("stock_input_key")
    if not key or not await is_admin(update.effective_user.id):
        return False

    doc = update.message.document
    if not doc:
        return False

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        data = bytes(await tg_file.download_as_bytearray())
        p = await db.get_product(key)
        if p and product_kind(p)=="mail" and str(doc.file_name or "").lower().endswith(".xlsx"):
            items = parse_xlsx_mail_stock(data)
        else:
            raw = data.decode("utf-8", errors="ignore")
            items = parse_stock_text(raw)
        count = await db.add_stock(key, items)
        context.user_data.pop("stock_input_key", None)
        await update.message.reply_text(
            f"✅ <b>{count} টি stock যোগ হয়েছে!</b>\n"
            f"📦 Current Stock: <b>{await db.stock_count(key)}</b>",
            parse_mode=ParseMode.HTML
        )
        return True
    except Exception as e:
        await update.message.reply_text(
            f"❌ File process failed: <code>{clean_html(str(e)[:300])}</code>",
            parse_mode=ParseMode.HTML
        )
        return True


# ============================================================
# BROADCAST
# ============================================================

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return

    if (update.message.text or "").strip() == "/cancel":
        context.user_data["broadcast_mode"] = False
        await update.message.reply_text(
            pe("6206110936789423908", "❌") + " <b>ব্রডকাস্ট বাতিল করা হয়েছে।</b>",
            parse_mode=ParseMode.HTML
        )
        return

    user_ids = await db.all_user_ids()
    if not user_ids:
        context.user_data["broadcast_mode"] = False
        await update.message.reply_text(
            "⚠️ ব্রডকাস্ট করার জন্য কোনো user পাওয়া যায়নি।"
        )
        return

    total = len(user_ids)
    success = 0
    failed = 0

    # copy_message preserves the message content and works for:
    # text, photo, video, document, animation, audio, voice, captions,
    # custom/premium emoji and messages containing links.
    for uid in user_ids:
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
            )
            success += 1
        except Exception:
            failed += 1
        # Small pause helps avoid hitting Telegram flood limits.
        await asyncio.sleep(0.035)

    context.user_data["broadcast_mode"] = False
    rate = (success / total * 100) if total else 0
    await update.message.reply_text(
        pe("6269163801178804220", "✅") + " <b>Broadcast সম্পন্ন হয়েছে!</b>\n\n"
        + pe("5332560480508591604", "🤖") + f" <b>Total Users:</b> {total}\n"
        + "✅ <b>Success:</b> " + str(success) + "\n"
        + "❌ <b>Failed:</b> " + str(failed) + "\n"
        + f"📈 <b>Success Rate:</b> {rate:.2f}%",
        parse_mode=ParseMode.HTML
    )


# ============================================================
# /INFO /RESET /APPROVE /REJECT
# ============================================================

async def resolve_user_identifier(value: str):
    """Resolve a Telegram user by numeric ID or @username/username."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.lstrip("+").isdigit():
        try:
            return await db.get_user(int(raw))
        except Exception:
            return None
    return await db.get_user_by_username(raw)


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update): return
    if not context.args:
        await update.message.reply_text("❌ ব্যবহার:\n<code>/info user_id</code> অথবা <code>/info @username</code>", parse_mode=ParseMode.HTML); return
    row=await resolve_user_identifier(context.args[0])
    if not row:
        await update.message.reply_text("❌ User পাওয়া যায়নি। ID অথবা username ঠিক আছে কিনা দেখুন।"); return
    uid=int(row["user_id"]); username=f"@{row['username']}" if row["username"] else "N/A"; name=f"{row['first_name']} {row['last_name'] or ''}".strip()
    await update.message.reply_text("ℹ️ <b>User Info</b>\n━━━━━━━━━━━━━━\n"+f"👤 <b>Name:</b> {clean_html(name)}\n"+f"🔗 <b>Username:</b> {clean_html(username)}\n"+f"🆔 <b>User ID:</b> <code>{uid}</code>\n"+f"💰 <b>Balance:</b> {float(row['balance']):.2f} ৳\n"+f"💳 <b>Total Deposit:</b> {float(row['total_deposit']):.2f} ৳\n"+f"👥 <b>Referrals:</b> {int(row['ref_count'])}\n"+f"🚫 <b>Banned:</b> {'Yes' if row['banned'] else 'No'}\n"+f"📅 <b>Joined:</b> {clean_html(row['joined_at'])}", parse_mode=ParseMode.HTML)
async def reset_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update): return
    if not context.args:
        await update.message.reply_text("❌ <b>ভুল ফরম্যাট!</b>\n<code>/reset_balance user_id</code> অথবা <code>/reset_balance @username</code>", parse_mode=ParseMode.HTML); return
    row=await resolve_user_identifier(context.args[0])
    if not row:
        await update.message.reply_text("❌ User পাওয়া যায়নি। ID অথবা username ঠিক আছে কিনা দেখুন।"); return
    uid=int(row["user_id"]); previous=float(row["balance"]); await db.set_balance(uid,0)
    uname=clean_html('@'+row['username'] if row['username'] else 'N/A')
    await update.message.reply_text(pe("6269163801178804220","✅")+" <b>ব্যালেন্স সফলভাবে রিসেট করা হয়েছে!</b>\n\n"+f"👤 <b>ইউজার ID:</b> <code>{uid}</code>\n"+f"🔗 <b>Username:</b> {uname}\n"+f"📉 <b>আগের ব্যালেন্স:</b> <code>{previous:.2f} ৳</code>\n"+"💰 <b>বর্তমান ব্যালেন্স:</b> <code>0.00 ৳</code>", parse_mode=ParseMode.HTML)
    try:
        await context.bot.send_message(uid, pe("6267039884016358504","⚠️")+" <b>Account Balance Reset Alert!</b>\n\nYour account balance has been reset to <b>0.00 BDT</b> by the administrator. If you have any questions, please contact support.", parse_mode=ParseMode.HTML)
    except Exception: pass
async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update): return
    if len(context.args)<2:
        await update.message.reply_text("❌ <b>ভুল ফরম্যাট!</b>\n<code>/approve user_id amount [method] [trx_id]</code>\nঅথবা\n<code>/approve @username amount [method] [trx_id]</code>", parse_mode=ParseMode.HTML); return
    row=await resolve_user_identifier(context.args[0])
    if not row:
        await update.message.reply_text("❌ User পাওয়া যায়নি। ID অথবা username ঠিক আছে কিনা দেখুন।"); return
    try: amount=float(context.args[1])
    except Exception: await update.message.reply_text("❌ Amount সঠিক নয়।"); return
    if amount<=0: await update.message.reply_text("❌ Amount অবশ্যই 0-এর বেশি হতে হবে।"); return
    uid=int(row["user_id"]); pending=await db.get_pending_deposit(uid)
    method=pending["method"] if pending and pending["method"] else (context.args[2] if len(context.args)>2 else "bKash / Nagad / Manual")
    trx=pending["trxid"] if pending and pending["trxid"] else (context.args[3] if len(context.args)>3 else "N/A")
    if pending and pending["pending_message_id"]:
        try: await context.bot.delete_message(uid,pending["pending_message_id"])
        except Exception: pass
    await db.add_balance(uid,amount); await db.add_total_deposit(uid,amount); await db.save_deposit(uid,amount,method,trx); await db.clear_pending_deposit(uid)
    if row["referrer_id"]:
        bonus=amount*0.05; await db.add_balance(row["referrer_id"],bonus)
        try: await context.bot.send_message(int(row["referrer_id"]),"🎉 <b>Referral Bonus Received!</b>\n\n"+f"Your referred user has deposited <code>{amount:g} BDT</code>.\n\n💰 <b>Bonus Amount:</b> <code>{bonus:.2f} BDT</code> (5%)\n✅ <b>Your referral bonus has been added to your balance.</b>",parse_mode=ParseMode.HTML)
        except Exception: pass
    current=await db.balance(uid); added_usd=amount/USD_RATE; total_usd=current/USD_RATE
    try: await context.bot.send_message(uid,pe("6203738495639360972","💯")+" <b>GET APPROVE!</b> Your payment is pending approval ✅\n\n"+pe("6269136309093143551","💸")+f" <b>Has been added:</b> {amount:g} ৳ ({added_usd:.2f} $)\n"+pe("6269136309093143551","💸")+f" <b>Your current balance:</b> {current:.2f} ৳ ({total_usd:.2f} $)",parse_mode=ParseMode.HTML)
    except Exception: pass
    await update.message.reply_text(pe("6269163801178804220","✅")+f" <b>সফলভাবে ইউজার <code>{uid}</code> এর অ্যাকাউন্টে {amount:g} ৳ ({added_usd:.2f} $) অ্যাড করা হয়েছে।</b>",parse_mode=ParseMode.HTML)
async def reject_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update): return
    if not context.args:
        await update.message.reply_text("❌ ব্যবহার:\n<code>/reject user_id</code> অথবা <code>/reject @username</code>",parse_mode=ParseMode.HTML); return
    row=await resolve_user_identifier(context.args[0])
    if not row:
        await update.message.reply_text("❌ User পাওয়া যায়নি। ID অথবা username ঠিক আছে কিনা দেখুন।"); return
    uid=int(row["user_id"]); pending=await db.get_pending_deposit(uid)
    if pending and pending["pending_message_id"]:
        try: await context.bot.delete_message(uid,pending["pending_message_id"])
        except Exception: pass
    await db.clear_pending_deposit(uid)
    await update.message.reply_text(f"❌ <b>Deposit request rejected.</b>\nUser ID: <code>{uid}</code>",parse_mode=ParseMode.HTML)
    try: await context.bot.send_message(uid,"❌ <b>আপনার Deposit request reject করা হয়েছে।</b>\nপ্রয়োজনে Support-এর সাথে যোগাযোগ করুন।",parse_mode=ParseMode.HTML)
    except Exception: pass
# ============================================================
# LEGACY ADMIN STOCK COMMANDS
# ============================================================

STOCK_COMMANDS = {
    "addstock_abc": "abc",
    "addstock_dataimpulse": "dataimpulse",
    "addstock_cli": "cli",
    "addstock_owl200mb": "owl_200mb",
    "addstock_owl1g": "owl_1gb",
    "addstock_mesh": "mesh",
    "addstock_rocket": "rocket",
    "addstock_rapid": "rapid",
    "addstock_rapid_200mb": "rapid_200mb",
    "addstock_9proxy": "9proxy_mb",
    "addstock_9proxy_gb": "9proxy_gb",
    "addstock_cdkey": "cdkey",
    "addstock_vpn_nord7": "vpn_nord_7",
    "addstock_vpn_nord30": "vpn_nord_30",
    "addstock_vpn_proton14": "vpn_proton_14",
    "addstock_vpn_proton30": "vpn_proton_30",
    "addstock_vpn_express3": "vpn_express_3",
    "addstock_vpn_surfshark7": "vpn_surfshark_7",
    "addstock_vpn_hma7": "vpn_hma_7",
    "addstock_vpn_bitdefender6": "vpn_bitdefender_6",
}

CLEAR_COMMANDS = {
    "clearstock_abc": "abc",
    "clear_dataimpulse": "dataimpulse",
    "clearstock_cli": "cli",
    "clearstock_owl200mb": "owl_200mb",
    "clearstock_owl1g": "owl_1gb",
    "clearstock_mesh": "mesh",
    "clearstock_rocket": "rocket",
    "clear_rapid": "rapid",
    "clearstock_rapid_200mb": "rapid_200mb",
    "clearstock_9proxy": "9proxy_mb",
    "clearstock_9proxy_gb": "9proxy_gb",
    "clearstock_cdkey": "cdkey",
    "clearstock_vpn_nord7": "vpn_nord_7",
    "clearstock_vpn_nord30": "vpn_nord_30",
    "clearstock_vpn_proton14": "vpn_proton_14",
    "clearstock_vpn_proton30": "vpn_proton_30",
    "clearstock_vpn_express3": "vpn_express_3",
    "clearstock_vpn_surfshark7": "vpn_surfshark_7",
    "clearstock_vpn_hma7": "vpn_hma_7",
    "clearstock_vpn_bitdefender6": "vpn_bitdefender_6",
}


async def legacy_addstock(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    if not await admin_guard(update):
        return
    raw = update.message.text or ""
    # command itself removed
    raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
    if not raw:
        context.user_data["stock_input_key"] = key
        await update.message.reply_text(
            "📥 Stock text পাঠান অথবা পরের message-এ .txt file পাঠান।"
        )
        return
    items = parse_stock_text(raw)
    count = await db.add_stock(key, items)
    await update.message.reply_text(
        f"✅ <b>{count} টি stock যোগ হয়েছে!</b>\n"
        f"📦 Current Stock: <b>{await db.stock_count(key)}</b>",
        parse_mode=ParseMode.HTML
    )


async def legacy_clearstock(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    if not await admin_guard(update):
        return
    await db.clear_stock(key)
    name = VPN_PRODUCTS[key]["name"] if key in VPN_PRODUCTS else PRODUCTS[key]["name"]
    await update.message.reply_text(
        f"🗑️ <b>{clean_html(name)} এর সকল stock মুছে ফেলা হয়েছে!</b>",
        parse_mode=ParseMode.HTML
    )


# ============================================================
# CALLBACK ROUTER
# ============================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await ensure_user_from_callback(q)

    if q.data == "ignore":
        await q.answer()
        return

    if q.data == "verify_join":
        return await verify_join(update, context)

    if q.data == "main_menu":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            q.from_user.id,
            await get_welcome_message(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(await is_admin(q.from_user.id)),
        )
        return

    if q.data == "buy_proxy":
        await q.answer()
        await send_proxy_menu(q.message)
        return

    if q.data == "buy_vpn":
        await q.answer()
        await send_vpn_menu(q.message)
        return

    if q.data.startswith("mailqty:"):
        _, key, qty = q.data.split(":",2); await show_mail_quantity(q,key,int(qty)); return
    if q.data.startswith("mailqtyenter:"):
        await mail_quantity_enter(q,context,q.data.split(":",1)[1]); return
    if q.data.startswith("mailbuy:"):
        _, key, qty = q.data.split(":",2); await mail_buy_confirm(q,context,key,int(qty)); return
    if q.data == "mail_qty_cancel":
        context.user_data.pop("mail_qty_key",None); context.user_data.pop("mail_qty_input",None); await q.answer("Cancelled"); return
    if q.data.startswith("mail:"):
        await mail_product_menu_callback(update,context,q.data.split(":",1)[1]); return

    if q.data.startswith("vpn_buy:"):
        await buy_vpn_product_callback(update, context, q.data.split(":", 1)[1])
        return

    if q.data.startswith("vpn:"):
        await vpn_product_callback(update, context, q.data.split(":", 1)[1])
        return

    if q.data == "add_balance":
        await q.answer()
        await send_add_balance_message(q.message, context)
        return

    if q.data.startswith("zini_verify:"):
        await zini_verify_callback(update, context, q.data.split(":", 1)[1])
        return

    if q.data.startswith("paygw:"):
        await q.answer()
        gateway=q.data.split(":",1)[1]
        if gateway=="manual": await manual_payment_menu(q)
        else: await begin_gateway_amount(q,context,gateway)
        return

    if q.data.startswith("manualmethod:"):
        await q.answer(); await manual_method_start(q,context,q.data.split(":",1)[1]); return

    if q.data.startswith("manual:approve:"):
        await approve_manual_callback(q,context,int(q.data.rsplit(":",1)[1])); return
    if q.data.startswith("manual:reject:"):
        await reject_manual_callback(q,context,int(q.data.rsplit(":",1)[1])); return

    if q.data == "payment_history":
        await q.answer()
        # Reuse a tiny fake route through a direct function.
        deps = await db.deposits(q.from_user.id)
        row = await db.get_user(q.from_user.id)
        total = float(row["total_deposit"]) if row else 0.0
        if not deps:
            await q.message.reply_text(
                f"❌ <b>কোনো payment history নেই!</b>\n\n💰 Total: {total:.2f} ৳",
                parse_mode=ParseMode.HTML
            )
            return
        msg = f"🧾 <b>Payment History</b>\n\n💰 <b>Total Deposit:</b> {total:.2f} ৳\n━━━━━━━━━━━━━━\n\n"
        for i, d in enumerate(deps, 1):
            msg += (
                f"{i}. ✅ <b>Amount:</b> {d['amount']} ৳\n"
                f"   💳 <b>Method:</b> {clean_html(d['method'])}\n"
                f"   🆔 <b>TrxID:</b> <code>{clean_html(d['trxid'])}</code>\n"
                f"   📅 <b>Date:</b> {clean_html(d['created_at'])}\n\n"
            )
        await send_long(q.message, msg, ParseMode.HTML)
        return

    if q.data == "proxy_qty_cancel":
        context.user_data.pop("proxy_qty_key", None); context.user_data.pop("admin_action", None)
        await q.answer("Cancelled")
        try: await q.message.delete()
        except Exception: pass
        return

    if q.data.startswith("pqenter:"):
        await proxy_quantity_enter(update, context, q.data.split(":", 1)[1]); return
    if q.data.startswith("pqconfirm:"):
        _, key, qty = q.data.split(":", 2)
        await proxy_quantity_confirm(update, context, key, int(qty)); return
    if q.data.startswith("pq:"):
        _, key, qty = q.data.split(":", 2)
        await proxy_quantity_callback(update, context, key, int(qty)); return

    if q.data.startswith("promo:apply:"):
        await promo_apply_start(update, context, q.data.split(":", 2)[2])
        return

    if q.data == "promo:cancel":
        context.user_data.pop("promo_wait", None)
        await q.answer("Cancelled")
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    if q.data.startswith("product:"):
        await product_menu_callback(update, context, q.data.split(":", 1)[1])
        return

    if q.data.startswith("buy:"):
        await buy_product_callback(update, context, q.data.split(":", 1)[1])
        return

    if q.data.startswith("9qty:"):
        try:
            qty = int(q.data.split(":", 1)[1])
        except Exception:
            qty = 1
        await nine_qty_callback(update, context, qty)
        return

    if q.data.startswith("manualorder:send:"):
        await manual_order_send(q,context,q.data.rsplit(":",1)[1]); return
    if q.data.startswith("manualorder:confirm:"):
        await manual_order_confirm(q,context,q.data.rsplit(":",1)[1]); return
    if q.data.startswith("manualorder:edit:"):
        await manual_order_edit(q,context,q.data.rsplit(":",1)[1]); return
    if q.data == "manualorder:cancel_details":
        await manual_order_cancel_details(q,context); return
    if q.data.startswith("manualorder:reject:"):
        await manual_order_reject(q,context,q.data.rsplit(":",1)[1]); return

    if q.data.startswith("9confirm:"):
        try:
            qty = int(q.data.split(":", 1)[1])
        except Exception:
            qty = 1
        await nine_confirm_callback(update, context, qty)
        return

    if q.data == "admin:cancel_action":
        for _k in ["admin_action","edit_price_key","stock_input_key","emoji_key","emoji_product_key","addproxy_name","addproxy_size","addproxy_price","addproxy_checker","addproxy_style","promo_key","promo_percent","promo_code","addvpn_name","addvpn_days","addvpn_price","addvpn_emoji","addmail_name","addmail_price","addmail_emoji","mail_edit_key","product_edit_key","mail_qty_key","mail_qty_input","support_add","gateway_key","gateway_min","manual_min","manual_num","binance_api","binance_sec","binance_rate","gateway_target","manual_target","menu_button_key"]:
            context.user_data.pop(_k, None)
        await q.answer("Cancelled")
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    if q.data == "cancel_add_balance":
        context.user_data["deposit_step"] = None
        context.user_data["deposit_amount"] = None
        await q.answer("Cancelled")
        try:
            await q.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            q.from_user.id,
            await get_welcome_message(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(await is_admin(q.from_user.id)),
        )
        return

    if q.data == "cancel":
        context.user_data["deposit_step"] = None
        context.user_data["deposit_amount"] = None
        context.user_data["broadcast_mode"] = False
        context.user_data["stock_input_key"] = None
        context.user_data["edit_price_key"] = None
        await q.answer("Cancelled")
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    if q.data.startswith("admin:") or q.data.startswith(("emoji:", "editproxy:", "editvpn:", "editmail:", "mailstockedit:", "mailhide:", "maileditname:", "mailemoji:", "deletemail:", "deletemailconfirm:", "togglevpn:", "vpnstockedit:", "stockedit:", "togglehide:", "proxyeditname:", "vpneditname:", "editprice:", "addstock:", "clearstock:", "deliverymode:", "gatewaycfg:", "gatewaytoggle:", "gatewaykey:", "gatewaymin:", "manualtoggle", "manualmin", "manualcfg:", "manualnum:", "manualhide:", "manualdelete:", "binancecfg:", "binancetoggle", "binancetest", "menutoggle:", "admin:menubuttonedit", "admin:textemoji", "menubtnedit:", "menubtnname:", "menubtnemoji:", "textemoji:set", "textemoji:remove")):
        return await admin_callback(update, context)


# ============================================================
# TEXT ROUTER
# ============================================================

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    await ensure_user(update)

    if await banned_guard(update):
        return

    # Quantity entry for Q-Style Proxy.
    if context.user_data.get("admin_action") == "proxy_quantity_input":
        raw_qty = (update.message.text or "").strip()
        if raw_qty.lower() == "/cancel":
            context.user_data.pop("proxy_qty_key", None); context.user_data.pop("admin_action", None)
            await update.message.reply_text("❌ Quantity selection cancelled.")
            return
        if not raw_qty.isdigit() or int(raw_qty) < 1:
            await update.message.reply_text("❌ শুধু ১ বা তার বেশি সংখ্যা লিখুন।")
            return
        key=context.user_data.get("proxy_qty_key")
        context.user_data.pop("proxy_qty_key", None); context.user_data.pop("admin_action", None)
        p=await db.get_product(key) if key else None
        if not p:
            await update.message.reply_text("❌ Product পাওয়া যায়নি।"); return
        # Reuse a temporary message-like flow by editing the last available message is not reliable on text input.
        manual_mode=str(p["delivery_mode"] or "auto")=="manual"
        stock=await db.stock_count(key); qty=int(raw_qty) if manual_mode else (min(int(raw_qty), stock) if stock else 1)
        base_price=float(p["price"]); price,pct,_pcode=await promo_effective(context,key,base_price); total=price*qty
        rate_text=f"{price:.1f}" if pct<=0 else f"<s>{base_price:.1f}</s> {price:.1f} ({pct:g}% OFF)"
        text=(pe("6104734070627900577","🛍")+" <b>Purchase Summary</b>\n\n"+pe("6269368817147711866","🏷")+" <b>Category:</b> Proxy\n"+pe(product_emoji_id(p),product_emoji_fallback(p))+f" <b>Package:</b> {clean_html(p['name'])}\n"+pe("6105185226877574701","🎁")+f" <b>Sub Package:</b> {clean_html(proxy_size(p))}\n\n"+pe("6269368817147711866","🏷")+("<b>Delivery:</b> Manual Delivery\n" if manual_mode else f"<b>Available Stock:</b> {stock} pcs\n")+pe("6267008582294705964","🔢")+f" <b>Quantity:</b> {qty}\n"+pe("6001287064589439895","💵")+f" <b>Rate:</b> {rate_text} Tk\n"+pe("6190336264940559752","💰")+f" <b>Total Price:</b> {total:.1f} Tk")
        rows=[[btn("−","danger",callback_data=f"pq:{key}:{max(1,qty-1)}"),btn(str(qty),"primary","cart",callback_data="ignore"),btn("+","success",callback_data=f"pq:{key}:{min(stock,qty+1)}")],[btn("Enter quantity","primary","cart",callback_data=f"pqenter:{key}")],[btn("Back","primary","main_menu",callback_data="buy_proxy"),btn("Cancel","danger","cross",callback_data="main_menu")],[btn("Confirm","success","check3",callback_data=f"pqconfirm:{key}:{qty}")]]
        await update.message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(rows)); return

    # Mail quantity text entry is a normal user flow, not an admin action.
    if context.user_data.get("mail_qty_input"):
        key=context.user_data.get("mail_qty_key"); raw=(update.message.text or "").strip()
        if raw=="/cancel":
            context.user_data.pop("mail_qty_key",None); context.user_data.pop("mail_qty_input",None)
            await update.message.reply_text("❌ Quantity selection cancelled."); return
        if not raw.isdigit() or int(raw)<1:
            await update.message.reply_text("❌ শুধু ১ বা তার বেশি সংখ্যা লিখুন।"); return
        context.user_data.pop("mail_qty_key",None); context.user_data.pop("mail_qty_input",None)
        p=await db.get_product(key) if key else None
        if not p or product_kind(p)!="mail": await update.message.reply_text("❌ Mail product পাওয়া যায়নি।"); return
        stock=await db.stock_count(key); qty=min(int(raw),stock) if stock else 1
        price=float(p["price"]); total=price*qty
        text2=pe("6104734070627900577","🛍")+" <b>Mail Purchase Summary</b>\n\n"+product_title_with_emoji(p)+f"\n📦 <b>Available Stock:</b> {stock}\n🔢 <b>Quantity:</b> {qty}\n💵 <b>Rate:</b> {price:.2f}৳\n💰 <b>Total:</b> {total:.2f}৳"
        await update.message.reply_text(text2,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("Confirm","success","check3",callback_data=f"mailbuy:{key}:{qty}")],[btn("Back","danger","main_menu",callback_data="buy_mail")]])); return

    # Admin actions have priority.
    if await handle_admin_text_action(update, context):
        return

    # Edit price flow.
    edit_key = context.user_data.get("edit_price_key")
    if edit_key and await is_admin(update.effective_user.id):
        price = parse_amount(update.message.text or "")
        if price is None:
            await update.message.reply_text("❌ সঠিক price দিন।")
            return
        await db.set_product_price(edit_key, price)
        context.user_data.pop("edit_price_key", None)
        p = await db.get_product(edit_key)
        await update.message.reply_text(
            f"✅ <b>{clean_html(p['name'])}</b> এর নতুন price: <b>{price:.2f}৳</b>",
            parse_mode=ParseMode.HTML
        )
        return

    # Stock text flow.
    stock_key = context.user_data.get("stock_input_key")
    if stock_key and await is_admin(update.effective_user.id):
        if update.message.text and update.message.text.strip() != "/cancel":
            items = parse_stock_text(update.message.text)
            count = await db.add_stock(stock_key, items)
            context.user_data.pop("stock_input_key", None)
            await update.message.reply_text(
                f"✅ <b>{count} টি stock যোগ হয়েছে!</b>\n"
                f"📦 Current Stock: <b>{await db.stock_count(stock_key)}</b>",
                parse_mode=ParseMode.HTML
            )
            return

    # Broadcast flow.
    if context.user_data.get("broadcast_mode") and await is_admin(update.effective_user.id):
        return await broadcast_message(update, context)

    # Admin stock text entry (supports 1/, 2/, 3/ blocks for proxy/VPN/mail).
    if context.user_data.get("stock_input_key") and await is_admin(update.effective_user.id):
        raw_stock=(update.message.text or "").strip()
        if raw_stock.lower()=="/cancel":
            context.user_data.pop("stock_input_key",None)
            await update.message.reply_text("❌ Stock input cancelled.")
            return
        key=context.user_data.get("stock_input_key")
        items=parse_stock_text(raw_stock)
        if not items:
            await update.message.reply_text("❌ Stock details পাওয়া যায়নি।")
            return
        count=await db.add_stock(key,items)
        context.user_data.pop("stock_input_key",None)
        await update.message.reply_text(f"✅ <b>{count} টি stock যোগ হয়েছে!</b>\n📦 Current Stock: <b>{await db.stock_count(key)}</b>",parse_mode=ParseMode.HTML)
        return

    # User promo code entry.
    if context.user_data.get("promo_wait"):
        _ptxt = (update.message.text or "").strip()
        if _ptxt in {menu_label(k) for k in MENU_BUTTON_LABELS}:
            context.user_data.pop("promo_wait", None)
        else:
            return await promo_apply_submit(update, context, _ptxt)

    # Main-menu buttons must always remain usable while a deposit input is open.
    # Only the explicit Add Balance button keeps/restarts the deposit session.
    text = (update.message.text or "").strip()
    menu_texts = {menu_label(k) for k in MENU_BUTTON_LABELS}
    if text in menu_texts and text != menu_label("add_balance"):
        context.user_data["deposit_step"] = None
        context.user_data.pop("deposit_gateway", None)
        context.user_data.pop("manual_method", None)
        context.user_data.pop("deposit_amount", None)
        context.user_data.pop("zini_method", None)

    # Deposit flows.
    step = context.user_data.get("deposit_step")
    if step == "gateway_amount":
        amount=parse_amount(update.message.text or ""); gateway=context.user_data.get("deposit_gateway")
        minimum=await gateway_minimum(gateway or "elitepay")
        if amount is None or amount<minimum:
            await update.message.reply_text(f"❌ <b>Minimum deposit is {minimum:.2f}৳.</b>",parse_mode=ParseMode.HTML); return
        context.user_data["deposit_step"]=None; context.user_data["deposit_gateway"]=None
        if gateway=="zini": return await start_zinipay_payment(update,context,amount)
        return await start_elitepay_payment(update,context,amount)

    if step == "manual_amount":
        amount=parse_amount(update.message.text or ""); method=context.user_data.get("manual_method","bKash")
        if amount is None or amount<=0:
            await update.message.reply_text("❌ Enter a valid amount."); return
        minimum=await db.get_setting("manual:min_deposit","10")
        try: minimum=float(minimum)
        except: minimum=10
        if amount<minimum: await update.message.reply_text(f"❌ Minimum deposit is {minimum:.2f}৳."); return
        return await manual_deposit_amount(update,context,amount)

    if step == "manual_binance_amount":
        amount=parse_amount(update.message.text or "")
        minimum=float(await db.get_setting("manual:min_deposit","10") or 10)
        if amount is None or amount<minimum: await update.message.reply_text(f"❌ Minimum deposit is {minimum:.2f}৳."); return
        context.user_data["deposit_step"]=None
        return await start_binance_payment(update,context,amount)

    if step == "elite_amount":
        amount = parse_amount(update.message.text or "")
        if amount is None or amount < await gateway_minimum("elitepay"):
            await update.message.reply_text(
                "❌ <b>সঠিক অ্যামাউন্ট লিখুন! সর্বনিম্ন ডিপোজিট gateway setting অনুযায়ী।</b>",
                parse_mode=ParseMode.HTML
            )
            return
        context.user_data["deposit_step"] = None
        await start_elitepay_payment(update, context, amount)
        return

    if step == "manual_trx":
        return await handle_manual_trx(update, context)

    # Main menu buttons.
    text = (update.message.text or "").strip()

    # Menu click cancels any stale deposit state, matching original * logic.
    if any(x in text for x in [
        "Balance", "Buy", "Support", "Broadcast", "Statistics",
        "Proxy", "VPN", "Mail", "History", "Pannel"
    ]):
        if text not in ("Add Balance",):
            context.user_data["deposit_step"] = None

    if text == menu_label("my_balance"):
        return await my_balance(update, context)

    if text == menu_label("add_balance"):
        return await send_add_balance_message(update.message, context)

    if text == menu_label("cart"):
        return await my_purchases(update, context)

    if text == menu_label("hourglass"):
        return await payment_history(update, context)

    if text == menu_label("link"):
        return await proxy_checker(update, context)

    if text == menu_label("chat"):
        return await support(update, context)

    if text == menu_label("buy_proxy"):
        return await buy_proxy(update, context)

    if text == menu_label("buy_vpn"):
        return await buy_vpn(update, context)

    if text == menu_label("buy_mail"):
        return await buy_mail(update, context)

    if text == menu_label("bot"):
        return await admin_panel(update, context)


# ============================================================
# DOCUMENT ROUTER
# ============================================================

async def sticker_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update)
    if await banned_guard(update):
        return
    if not await is_admin(update.effective_user.id):
        return
    if context.user_data.get("admin_action")!="text_emoji":
        return
    sticker=update.message.sticker
    eid=getattr(sticker,"custom_emoji_id",None) if sticker else None
    emoji=getattr(sticker,"emoji",None) if sticker else None
    if not eid:
        await update.message.reply_text("❌ এটি Premium Custom Emoji sticker নয়।")
        return
    global TEXT_EMOJI_ID, TEXT_EMOJI_MAP
    TEXT_EMOJI_ID=str(eid)
    if emoji:
        TEXT_EMOJI_MAP[str(emoji)] = str(eid)
        await db.set_setting("text_emoji_map", json.dumps(TEXT_EMOJI_MAP, ensure_ascii=False))
    await db.set_setting("text_emoji_id",TEXT_EMOJI_ID)
    context.user_data.pop("admin_action",None)
    await update.message.reply_text("✅ <b>Premium Emoji saved.</b>" + (f"\nMapping: {clean_html(str(emoji))} → <code>{eid}</code>" if emoji else ""),parse_mode=ParseMode.HTML)


async def document_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update)
    if await banned_guard(update):
        return
    if context.user_data.get("admin_action")=="text_emoji":
        document = update.message.document
        if not document:
            await update.message.reply_text("❌ Emoji ID file পাওয়া যায়নি।")
            return
        try:
            tg_file = await context.bot.get_file(document.file_id)
            data = await tg_file.download_as_bytearray()
            raw = bytes(data).decode("utf-8-sig", errors="replace")
            mapping = parse_text_emoji_mapping(raw)
            if not mapping:
                await update.message.reply_text(
                    "❌ কোনো valid Emoji ID পাওয়া যায়নি।\n\n"
                    "প্রতি লাইনে এভাবে দিন:\n<code>⚠️6206174450765796040</code>",
                    parse_mode=ParseMode.HTML
                )
                return

            global TEXT_EMOJI_MAP, TEXT_EMOJI_ID
            TEXT_EMOJI_MAP = mapping
            TEXT_EMOJI_ID = next(iter(mapping.values()), "")
            await db.set_setting("text_emoji_map", json.dumps(mapping, ensure_ascii=False))
            await db.set_setting("text_emoji_id", TEXT_EMOJI_ID)
            context.user_data.pop("admin_action", None)

            preview = "\n".join(f"{e} → <code>{eid}</code>" for e, eid in list(mapping.items())[:8])
            more = "" if len(mapping) <= 8 else f"\n… +{len(mapping)-8} more"
            await update.message.reply_text(
                f"✅ <b>Emoji ID file successfully uploaded!</b>\n\n"
                f"📦 Total mappings: <b>{len(mapping)}</b>\n\n{preview}{more}\n\n"
                "এখন Bot-এর outgoing text-এ এই normal emoji থাকলে automatically Premium Emoji হবে।",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.exception("Text emoji mapping upload failed")
            await update.message.reply_text(
                "❌ Emoji ID file process করতে সমস্যা হয়েছে।\n<code>" + clean_html(str(e)[:500]) + "</code>",
                parse_mode=ParseMode.HTML
            )
        return
    if await process_stock_file(update, context):
        return

    if context.user_data.get("broadcast_mode") and await is_admin(update.effective_user.id):
        await broadcast_message(update, context)


# ============================================================
# LEGACY COMMAND REGISTRATION
# ============================================================

def register_legacy_commands(app: Application):
    for cmd, key in STOCK_COMMANDS.items():
        app.add_handler(CommandHandler(
            cmd, lambda u, c, k=key: legacy_addstock(u, c, k)
        ))

    for cmd, key in CLEAR_COMMANDS.items():
        app.add_handler(CommandHandler(
            cmd, lambda u, c, k=key: legacy_clearstock(u, c, k)
        ))


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update, context):
    log.exception("Unhandled error: %s", context.error)


# ============================================================
# WEB SERVER
# ============================================================

async def health(request):
    return web.json_response({
        "ok": True,
        "service": "Secret Proxy Shop",
        "time": now_bd(),
    })


async def start_web_server(bot: Bot):
    app = web.Application()
    app["telegram_bot"] = bot
    app.router.add_get("/", health)
    app.router.add_get("/elitepay_webhook", elitepay_webhook)
    app.router.add_post("/elitepay_webhook", elitepay_webhook)
    app.router.add_get("/zinipay_webhook", zinipay_webhook)
    app.router.add_post("/zinipay_webhook", zinipay_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web server started on port %s", PORT)
    return runner


# ============================================================
# MAIN
# ============================================================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing. "
            "Set BOT_TOKEN before starting the bot."
        )

    global TEXT_EMOJI_ID
    for _k in list(MENU_BUTTON_LABELS.keys()):
        _v=await db.get_setting("menu:name:"+_k)
        if _v: MENU_BUTTON_NAMES[_k]=str(_v)
    TEXT_EMOJI_ID=str(await db.get_setting("text_emoji_id","") or "")
    _text_map_raw = await db.get_setting("text_emoji_map", "{}")
    try:
        TEXT_EMOJI_MAP = json.loads(_text_map_raw or "{}")
        if not isinstance(TEXT_EMOJI_MAP, dict):
            TEXT_EMOJI_MAP = {}
    except Exception:
        TEXT_EMOJI_MAP = {}

    # Restore admin-customised button emoji IDs from SQLite.
    for _k in list(MENU_VISIBLE.keys()):
        _v=await db.get_setting("menu:hidden:"+_k)
        if _v is not None: MENU_VISIBLE[_k]=(_v!="1")
    for _k in list(BTN_EMO.keys()):
        _v = await db.get_setting("emoji:button:" + _k)
        if _v and str(_v).isdigit():
            BTN_EMO[_k] = str(_v)

    # Use a Bot subclass for the optional global Text Emoji feature.
    # Telegram Bot objects are immutable with respect to method assignment, so
    # the mapping is applied in this subclass instead of monkey-patching.
    class TextEmojiBot(Bot):
        async def send_message(self, *args, **kwargs):
            if isinstance(kwargs.get("text"), str):
                kwargs["text"] = premiumize_text(kwargs["text"], kwargs.get("parse_mode"))
            elif len(args) >= 2 and isinstance(args[1], str):
                args = list(args)
                args[1] = premiumize_text(args[1], kwargs.get("parse_mode"))
                args = tuple(args)
            return await super().send_message(*args, **kwargs)

        async def edit_message_text(self, *args, **kwargs):
            if isinstance(kwargs.get("text"), str):
                kwargs["text"] = premiumize_text(kwargs["text"], kwargs.get("parse_mode"))
            elif len(args) >= 1 and isinstance(args[0], str):
                args = list(args)
                args[0] = premiumize_text(args[0], kwargs.get("parse_mode"))
                args = tuple(args)
            return await super().edit_message_text(*args, **kwargs)

        async def send_photo(self, *args, **kwargs):
            premiumize_caption(kwargs)
            return await super().send_photo(*args, **kwargs)

        async def send_video(self, *args, **kwargs):
            premiumize_caption(kwargs)
            return await super().send_video(*args, **kwargs)

        async def send_document(self, *args, **kwargs):
            premiumize_caption(kwargs)
            return await super().send_document(*args, **kwargs)

        async def send_animation(self, *args, **kwargs):
            premiumize_caption(kwargs)
            return await super().send_animation(*args, **kwargs)

        async def send_audio(self, *args, **kwargs):
            premiumize_caption(kwargs)
            return await super().send_audio(*args, **kwargs)


    application = (
        Application.builder()
        .bot(TextEmojiBot(BOT_TOKEN))
        .concurrent_updates(True)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("reset_balance", reset_balance_command))
    application.add_handler(CommandHandler("approve", approve_command))
    application.add_handler(CommandHandler("reject", reject_command))

    register_legacy_commands(application)

    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(
        filters.Sticker.ALL, sticker_router
    ))
    application.add_handler(MessageHandler(
        filters.Document.ALL, document_router
    ))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, text_router
    ))

    application.add_error_handler(error_handler)

    # Initialize Telegram application. TextEmojiBot handles the optional
    # global text emoji without modifying the immutable Bot instance.
    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES
    )

    web_runner = await start_web_server(application.bot)
    zini_poller_task = asyncio.create_task(zini_payment_poller(application.bot))
    log.info("Secret Proxy Shop bot is running.")
    log.info("Database: %s", Path(DB_PATH).resolve())
    log.info("Admins: %s", await db.admin_ids())
    log.info("ZiniPay webhook: %s", f"{WEBHOOK_URL}/zinipay_webhook" if WEBHOOK_URL else "not configured; background invoice polling is active")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        zini_poller_task.cancel()
        try:
            await zini_poller_task
        except asyncio.CancelledError:
            pass
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await web_runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
