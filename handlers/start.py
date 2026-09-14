"""
handlers/start.py
------------------
Handles /start, /help, and /profile -- registering the user in MongoDB on
first contact and attaching the persistent bottom Reply Keyboard.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from keyboards import main_menu_keyboard

logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "👋 *Welcome to EscrowGuard Bot!*\n\n"
    "I act as a trusted middleman between buyers and sellers so both sides "
    "can trade with confidence.\n\n"
    "• Buyers create a deal and fund it through me.\n"
    "• Sellers join the deal and deliver the goods/service.\n"
    "• I hold the funds until the buyer confirms delivery, then release "
    "them to the seller.\n\n"
    "Use the menu below to get started, or type `/create_deal <amount> <currency>` "
    "to create a deal right now."
)

HELP_TEXT = (
    "*How EscrowGuard works*\n\n"
    "1️⃣ Buyer creates a deal, e.g. `/create_deal 100 USDT`.\n"
    "2️⃣ Seller taps *Join as Seller* on the deal message.\n"
    "3️⃣ Buyer taps *Pay Now* -- funds move into escrow.\n"
    "4️⃣ Seller delivers the goods/service off-platform.\n"
    "5️⃣ Buyer taps *Release Funds* once satisfied.\n"
    "6️⃣ If something goes wrong, either party can *Raise Dispute* and an "
    "admin will review and resolve it.\n\n"
    "Commands:\n"
    "`/create_deal <amount> <currency> [description]`\n"
    "`/my_deals` -- list your deals\n"
    "`/view_deal <deal_id>` -- look up a specific deal\n\n"
    "Need a human? Contact @your_support_handle."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registers/refreshes the user in MongoDB and shows the welcome message."""
    user = update.effective_user
    if user is None:
        return
    try:
        await db.upsert_user(user.id, user.username, user.full_name)
    except Exception:
        logger.exception("Failed to upsert user %s during /start", user.id)
        await update.message.reply_text(
            "⚠️ We hit a temporary issue connecting to our database. Please try again shortly."
        )
        return

    await update.message.reply_text(
        WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles both /help and the '❓ Help / Support' reply-keyboard button."""
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles both /profile and the '👤 Profile' reply-keyboard button."""
    user = update.effective_user
    if user is None:
        return

    record = await db.get_user(user.id)
    if record is None:
        # Shouldn't normally happen since /start registers the user, but
        # handle gracefully in case someone taps Profile before /start.
        record = await db.upsert_user(user.id, user.username, user.full_name)

    created_at = record.get("created_at")
    member_since = created_at.strftime("%Y-%m-%d") if created_at else "N/A"

    text = (
        "👤 *Your Profile*\n\n"
        f"Name: {record.get('full_name', user.full_name)}\n"
        f"Username: @{record.get('username') or 'N/A'}\n"
        f"Telegram ID: `{user.id}`\n"
        f"Total deals created: {record.get('deals_count', 0)}\n"
        f"Member since: {member_since}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
