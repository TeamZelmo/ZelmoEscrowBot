"""
handlers/deal.py
-----------------
Deal creation (both `/create_deal <amount> <currency>` and a guided,
step-by-step conversation triggered by the "🤝 Create Deal" button),
plus browsing deals via "📦 My Deals" / `/view_deal`.

Payment / release / dispute / cancellation logic lives in handlers/escrow.py.
"""

import logging
from typing import List

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import database as db
from database import DealStatus
from keyboards import deal_detail_keyboard, join_deal_keyboard

logger = logging.getLogger(__name__)

# Conversation states for the guided "Create Deal" flow
ASK_AMOUNT, ASK_CURRENCY, ASK_DESCRIPTION = range(3)

# Multi-currency support: extend this set (and plug in a real FX/rate
# lookup where needed) to support more fiat or crypto currencies.
SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "INR", "USDT", "BTC", "ETH"}

MAX_DESCRIPTION_LEN = 300


def format_deal_card(deal: dict) -> str:
    """Renders a human-readable summary of a deal for chat messages."""
    status_labels = {
        DealStatus.AWAITING_SELLER: "🟡 Awaiting Seller",
        DealStatus.AWAITING_PAYMENT: "🟠 Awaiting Payment",
        DealStatus.FUNDS_HELD: "🟢 Funds in Escrow",
        DealStatus.DISPUTED: "🔴 Disputed",
        DealStatus.RELEASED: "✅ Completed (Released)",
        DealStatus.CANCELLED: "⚪ Cancelled",
        DealStatus.REFUNDED: "🔵 Refunded",
    }
    lines = [
        f"*Deal #{deal['deal_id']}*",
        f"Amount: `{deal['amount']} {deal['currency']}`",
        f"Status: {status_labels.get(deal['status'], deal['status'])}",
    ]
    if deal.get("description"):
        lines.append(f"Description: {deal['description']}")
    lines.append(f"Buyer: `{deal['buyer_id']}`")
    lines.append(f"Seller: `{deal['seller_id'] or 'Not joined yet'}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /create_deal <amount> <currency> [description...]   (direct command form)
# ---------------------------------------------------------------------------

async def create_deal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles `/create_deal 150 USDT Selling a gaming laptop`."""
    user = update.effective_user
    args: List[str] = context.args or []

    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/create_deal <amount> <currency> [description]`\n"
            "Example: `/create_deal 150 USDT Selling a gaming laptop`",
            parse_mode="Markdown",
        )
        return

    amount_raw, currency_raw = args[0], args[1]
    description = " ".join(args[2:])[:MAX_DESCRIPTION_LEN]

    try:
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError("Amount must be positive.")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Amount must be a positive number, e.g. `150.00`.", parse_mode="Markdown"
        )
        return

    currency = currency_raw.upper()
    if currency not in SUPPORTED_CURRENCIES:
        await update.message.reply_text(
            f"⚠️ Unsupported currency `{currency}`.\nSupported: {', '.join(sorted(SUPPORTED_CURRENCIES))}",
            parse_mode="Markdown",
        )
        return

    await _persist_and_announce_deal(update, context, user.id, amount, currency, description)


async def _persist_and_announce_deal(
    update: Update, context: ContextTypes.DEFAULT_TYPE, buyer_id: int, amount: float, currency: str, description: str
) -> None:
    """Shared tail-end for both the direct command and the guided conversation."""
    try:
        deal = await db.create_deal(buyer_id, amount, currency, description)
    except Exception:
        logger.exception("Failed to create deal for buyer %s", buyer_id)
        await update.effective_message.reply_text(
            "⚠️ Something went wrong creating your deal. Please try again."
        )
        return

    text = (
        "✅ *Deal created!*\n\n" + format_deal_card(deal) + "\n\n"
        "Share this Deal ID with the seller, or forward this message to them so "
        "they can tap *Join as Seller* below."
    )
    await update.effective_message.reply_text(
        text, parse_mode="Markdown", reply_markup=join_deal_keyboard(deal["deal_id"])
    )


# ---------------------------------------------------------------------------
# Guided conversation triggered by the "🤝 Create Deal" reply-keyboard button
# ---------------------------------------------------------------------------

async def guided_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Let's set up a new deal.\n\n💰 How much is it for? (numbers only, e.g. `150`)",
        parse_mode="Markdown",
    )
    return ASK_AMOUNT


async def guided_create_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please send a valid positive number, e.g. `150`.", parse_mode="Markdown"
        )
        return ASK_AMOUNT

    context.user_data["new_deal_amount"] = amount
    await update.message.reply_text(
        f"💱 Which currency? Supported: {', '.join(sorted(SUPPORTED_CURRENCIES))}"
    )
    return ASK_CURRENCY


async def guided_create_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    currency = update.message.text.strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        await update.message.reply_text(
            f"⚠️ Unsupported currency. Choose one of: {', '.join(sorted(SUPPORTED_CURRENCIES))}"
        )
        return ASK_CURRENCY

    context.user_data["new_deal_currency"] = currency
    await update.message.reply_text(
        "📝 Add a short description of the goods/service (or send /skip)."
    )
    return ASK_DESCRIPTION


async def guided_create_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    description = "" if raw == "/skip" else raw[:MAX_DESCRIPTION_LEN]

    amount = context.user_data.pop("new_deal_amount")
    currency = context.user_data.pop("new_deal_currency")
    user = update.effective_user

    await _persist_and_announce_deal(update, context, user.id, amount, currency, description)
    return ConversationHandler.END


async def guided_create_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_deal_amount", None)
    context.user_data.pop("new_deal_currency", None)
    await update.message.reply_text("Deal creation cancelled.")
    return ConversationHandler.END


def build_create_deal_conversation(entry_text: str) -> ConversationHandler:
    """Factory so main.py can wire this to the exact reply-keyboard button text."""
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{entry_text}$"), guided_create_start)],
        states={
            ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, guided_create_amount)],
            ASK_CURRENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, guided_create_currency)],
            ASK_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, guided_create_description)],
        },
        fallbacks=[CommandHandler("cancel", guided_create_cancel)],
        name="create_deal_conversation",
        persistent=False,
    )


# ---------------------------------------------------------------------------
# "📦 My Deals" and /view_deal
# ---------------------------------------------------------------------------

async def my_deals_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    deals = await db.get_user_deals(user.id)

    if not deals:
        await update.message.reply_text(
            "You have no deals yet. Tap *🤝 Create Deal* to start one!", parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"📦 You have {len(deals)} deal(s):")
    for deal in deals:
        await update.message.reply_text(
            format_deal_card(deal),
            parse_mode="Markdown",
            reply_markup=deal_detail_keyboard(deal, user.id),
        )


async def view_deal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/view_deal <deal_id>` -- quick lookup by ID for anyone who has it."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: `/view_deal <deal_id>`", parse_mode="Markdown")
        return

    deal = await db.get_deal(args[0])
    if deal is None:
        await update.message.reply_text("❌ No deal found with that ID.")
        return

    user = update.effective_user
    await update.message.reply_text(
        format_deal_card(deal), parse_mode="Markdown", reply_markup=deal_detail_keyboard(deal, user.id)
    )
