"""
handlers/escrow.py
--------------------
The core escrow state machine: a seller joining a deal, simulated payment
that moves funds into escrow, releasing funds to the seller, raising and
resolving disputes, and cancellation.

Every transition is guarded so only the authorized party (buyer, seller,
or admin -- as appropriate) can trigger it, and every DB write is
conditioned on the deal's *expected current status* so two people tapping
buttons at the same instant can't corrupt state (see
database.update_deal_status / database.assign_seller).
"""

import os
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from database import DealStatus
from keyboards import payment_keyboard, funds_held_keyboard, admin_dispute_keyboard, deal_detail_keyboard
from handlers.deal import format_deal_card

logger = logging.getLogger(__name__)

# Comma-separated Telegram user IDs with dispute-resolution authority, e.g. "111,222"
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()
}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _answer_safely(update: Update, text: str, show_alert: bool = False) -> None:
    """Answers a callback query, swallowing 'query too old / invalid' errors from Telegram."""
    try:
        await update.callback_query.answer(text=text, show_alert=show_alert)
    except Exception:
        logger.debug("Failed to answer callback query (likely expired).", exc_info=True)


async def query_edit(update: Update, deal: dict, keyboard) -> None:
    """Rewrites the original inline message with the latest deal state."""
    try:
        await update.callback_query.edit_message_text(
            format_deal_card(deal), parse_mode="Markdown", reply_markup=keyboard
        )
    except Exception:
        logger.debug("Could not edit message (likely unchanged or expired).", exc_info=True)


# ---------------------------------------------------------------------------
# Single entry point for every inline button in the bot
# ---------------------------------------------------------------------------

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    callback_data is formatted as '<action>:<deal_id>[:extra]', e.g.
    'join:A1B2C3D4' or 'resolve:A1B2C3D4:seller'.
    """
    query = update.callback_query
    data = query.data or ""
    parts = data.split(":")
    action = parts[0] if parts else ""

    try:
        if action == "join" and len(parts) == 2:
            await _handle_join(update, context, parts[1])
        elif action == "pay" and len(parts) == 2:
            await _handle_pay(update, context, parts[1])
        elif action == "release" and len(parts) == 2:
            await _handle_release(update, context, parts[1])
        elif action == "dispute" and len(parts) == 2:
            await _handle_dispute(update, context, parts[1])
        elif action == "cancel" and len(parts) == 2:
            await _handle_cancel(update, context, parts[1])
        elif action == "resolve" and len(parts) == 3:
            await _handle_resolve(update, context, parts[1], parts[2])
        elif action == "view" and len(parts) == 2:
            await _handle_view(update, context, parts[1])
        else:
            await _answer_safely(update, "Unknown action.", show_alert=True)
    except Exception:
        logger.exception("Unhandled error in callback_router for data=%s", data)
        await _answer_safely(update, "⚠️ Something went wrong. Please try again.", show_alert=True)


# ---------------------------------------------------------------------------
# Seller joins an open deal
# ---------------------------------------------------------------------------

async def _handle_join(update: Update, context: ContextTypes.DEFAULT_TYPE, deal_id: str) -> None:
    user = update.effective_user
    deal = await db.get_deal(deal_id)
    if deal is None:
        await _answer_safely(update, "❌ Deal not found.", show_alert=True)
        return
    if deal["buyer_id"] == user.id:
        await _answer_safely(update, "🚫 You can't join your own deal as the seller.", show_alert=True)
        return
    if deal["status"] != DealStatus.AWAITING_SELLER:
        await _answer_safely(update, "This deal is no longer open for a seller.", show_alert=True)
        return

    updated = await db.assign_seller(deal_id, user.id)
    if updated is None:
        # Someone else joined in the split-second between our check and the write.
        await _answer_safely(update, "😕 Someone else already joined this deal.", show_alert=True)
        return

    await _answer_safely(update, "✅ You joined as the seller!")
    await query_edit(update, updated, payment_keyboard(deal_id))

    await context.bot.send_message(
        chat_id=updated["buyer_id"],
        text=f"🙋 A seller has joined Deal #{deal_id}. You can now pay to fund escrow.",
        reply_markup=payment_keyboard(deal_id),
    )


# ---------------------------------------------------------------------------
# Buyer pays -> funds move into escrow (SIMULATED payment)
# ---------------------------------------------------------------------------

async def simulate_payment(deal: dict) -> bool:
    """
    --------------------------------------------------------------------
    PAYMENT INTEGRATION POINT
    --------------------------------------------------------------------
    Replace this function with a real check against your payment
    gateway or blockchain node, for example:
      - Stripe: confirm a PaymentIntent / PaymentMethod for deal['amount']
      - Crypto (USDT/BTC/ETH): verify an on-chain deposit to a
        deal-specific address matches deal['amount'] and deal['currency']
      - Bank transfer: verify a webhook/reference number
    Return True only once funds have actually and irreversibly arrived.
    For now this always succeeds so the rest of the escrow flow can be
    exercised end-to-end without a live payment provider.
    """
    logger.info(
        "Simulating payment of %s %s for deal %s", deal["amount"], deal["currency"], deal["deal_id"]
    )
    return True


async def _handle_pay(update: Update, context: ContextTypes.DEFAULT_TYPE, deal_id: str) -> None:
    user = update.effective_user
    deal = await db.get_deal(deal_id)
    if deal is None:
        await _answer_safely(update, "❌ Deal not found.", show_alert=True)
        return
    if user.id != deal["buyer_id"]:
        await _answer_safely(update, "🚫 Only the buyer can pay for this deal.", show_alert=True)
        return
    if deal["status"] != DealStatus.AWAITING_PAYMENT:
        await _answer_safely(update, "This deal isn't awaiting payment right now.", show_alert=True)
        return

    payment_ok = await simulate_payment(deal)
    if not payment_ok:
        await _answer_safely(update, "❌ Payment failed. Please try again.", show_alert=True)
        return

    updated = await db.update_deal_status(
        deal_id, DealStatus.FUNDS_HELD, expected_status=DealStatus.AWAITING_PAYMENT
    )
    if updated is None:
        await _answer_safely(update, "⚠️ Deal state changed, please refresh.", show_alert=True)
        return

    await _answer_safely(update, "💳 Payment received! Funds are now held in escrow.")
    await query_edit(update, updated, funds_held_keyboard(deal_id))

    await context.bot.send_message(
        chat_id=updated["seller_id"],
        text=(
            f"💰 The buyer has funded Deal #{deal_id} "
            f"({updated['amount']} {updated['currency']}). You may now proceed with delivery."
        ),
        reply_markup=funds_held_keyboard(deal_id),
    )


# ---------------------------------------------------------------------------
# Buyer releases funds to the seller
# ---------------------------------------------------------------------------

async def _handle_release(update: Update, context: ContextTypes.DEFAULT_TYPE, deal_id: str) -> None:
    user = update.effective_user
    deal = await db.get_deal(deal_id)
    if deal is None:
        await _answer_safely(update, "❌ Deal not found.", show_alert=True)
        return
    if user.id != deal["buyer_id"]:
        await _answer_safely(update, "🚫 Only the buyer can release funds.", show_alert=True)
        return
    if deal["status"] != DealStatus.FUNDS_HELD:
        await _answer_safely(update, "Funds aren't currently held in escrow for this deal.", show_alert=True)
        return

    updated = await db.update_deal_status(deal_id, DealStatus.RELEASED, expected_status=DealStatus.FUNDS_HELD)
    if updated is None:
        await _answer_safely(update, "⚠️ Deal state changed, please refresh.", show_alert=True)
        return

    await _answer_safely(update, "✅ Funds released to the seller. Deal complete!")
    await query_edit(update, updated, None)

    await context.bot.send_message(
        chat_id=updated["seller_id"],
        text=f"🎉 Funds for Deal #{deal_id} have been released to you by the buyer!",
    )


# ---------------------------------------------------------------------------
# Either party raises a dispute
# ---------------------------------------------------------------------------

async def _handle_dispute(update: Update, context: ContextTypes.DEFAULT_TYPE, deal_id: str) -> None:
    user = update.effective_user
    deal = await db.get_deal(deal_id)
    if deal is None:
        await _answer_safely(update, "❌ Deal not found.", show_alert=True)
        return
    if user.id not in (deal["buyer_id"], deal["seller_id"]):
        await _answer_safely(update, "🚫 Only the buyer or seller can raise a dispute.", show_alert=True)
        return
    if deal["status"] != DealStatus.FUNDS_HELD:
        await _answer_safely(update, "Disputes can only be raised while funds are held in escrow.", show_alert=True)
        return

    updated = await db.update_deal_status(
        deal_id,
        DealStatus.DISPUTED,
        extra_fields={"dispute_reason": f"Raised by user {user.id}"},
        expected_status=DealStatus.FUNDS_HELD,
    )
    if updated is None:
        await _answer_safely(update, "⚠️ Deal state changed, please refresh.", show_alert=True)
        return

    await _answer_safely(update, "⚠️ Dispute raised. An admin will review this deal.", show_alert=True)
    await query_edit(update, updated, None)

    other_party = updated["seller_id"] if user.id == updated["buyer_id"] else updated["buyer_id"]
    await context.bot.send_message(
        chat_id=other_party,
        text=f"⚠️ A dispute has been raised on Deal #{deal_id}. An admin will step in shortly.",
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🚨 *Dispute on Deal #{deal_id}*\n\n" + format_deal_card(updated),
                parse_mode="Markdown",
                reply_markup=admin_dispute_keyboard(deal_id),
            )
        except Exception:
            logger.warning("Could not notify admin %s about dispute on %s", admin_id, deal_id)


# ---------------------------------------------------------------------------
# Admin resolves a dispute -- inline button version
# ---------------------------------------------------------------------------

async def _handle_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE, deal_id: str, winner: str) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await _answer_safely(update, "🚫 Only an admin can resolve disputes.", show_alert=True)
        return

    deal = await db.get_deal(deal_id)
    if deal is None:
        await _answer_safely(update, "❌ Deal not found.", show_alert=True)
        return
    if deal["status"] != DealStatus.DISPUTED:
        await _answer_safely(update, "This deal isn't currently disputed.", show_alert=True)
        return

    updated = await _resolve_dispute(deal_id, winner, user.id)
    if updated is None:
        await _answer_safely(update, "⚠️ Deal state changed, please refresh.", show_alert=True)
        return

    await _answer_safely(update, f"✅ Resolved in favor of the {winner}.")
    await query_edit(update, updated, None)
    await _notify_resolution(context, updated, winner)


# ---------------------------------------------------------------------------
# Admin resolves a dispute -- /resolve_dispute command fallback
# ---------------------------------------------------------------------------

async def resolve_dispute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /resolve_dispute <deal_id> <buyer|seller>"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return

    args = context.args or []
    if len(args) != 2 or args[1] not in ("buyer", "seller"):
        await update.message.reply_text(
            "Usage: `/resolve_dispute <deal_id> <buyer|seller>`", parse_mode="Markdown"
        )
        return

    deal_id, winner = args[0], args[1]
    deal = await db.get_deal(deal_id)
    if deal is None:
        await update.message.reply_text("❌ Deal not found.")
        return
    if deal["status"] != DealStatus.DISPUTED:
        await update.message.reply_text("This deal isn't currently disputed.")
        return

    updated = await _resolve_dispute(deal_id, winner, user.id)
    if updated is None:
        await update.message.reply_text("⚠️ Deal state changed, please try again.")
        return

    await update.message.reply_text(f"✅ Deal #{deal_id} resolved in favor of the {winner}.")
    await _notify_resolution(context, updated, winner)


async def _resolve_dispute(deal_id: str, winner: str, admin_id: int):
    """Shared logic: seller wins -> release funds; buyer wins -> refund."""
    new_status = DealStatus.RELEASED if winner == "seller" else DealStatus.REFUNDED
    return await db.update_deal_status(
        deal_id,
        new_status,
        extra_fields={"dispute_reason": f"Resolved by admin {admin_id} in favor of {winner}"},
        expected_status=DealStatus.DISPUTED,
    )


async def _notify_resolution(context: ContextTypes.DEFAULT_TYPE, updated: dict, winner: str) -> None:
    verdict_text = f"⚖️ Deal #{updated['deal_id']} dispute has been resolved by an admin in favor of the {winner}."
    for party_id in (updated["buyer_id"], updated["seller_id"]):
        if not party_id:
            continue
        try:
            await context.bot.send_message(chat_id=party_id, text=verdict_text)
        except Exception:
            logger.warning("Could not notify party %s of resolution on %s", party_id, updated["deal_id"])


# ---------------------------------------------------------------------------
# Cancel (only allowed while no funds are held in escrow)
# ---------------------------------------------------------------------------

async def _handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, deal_id: str) -> None:
    user = update.effective_user
    deal = await db.get_deal(deal_id)
    if deal is None:
        await _answer_safely(update, "❌ Deal not found.", show_alert=True)
        return
    if user.id != deal["buyer_id"] and not is_admin(user.id):
        await _answer_safely(update, "🚫 Only the buyer (or an admin) can cancel this deal.", show_alert=True)
        return
    if deal["status"] not in (DealStatus.AWAITING_SELLER, DealStatus.AWAITING_PAYMENT):
        await _answer_safely(
            update, "This deal can no longer be cancelled (funds may already be in escrow).", show_alert=True
        )
        return

    updated = await db.update_deal_status(deal_id, DealStatus.CANCELLED, expected_status=deal["status"])
    if updated is None:
        await _answer_safely(update, "⚠️ Deal state changed, please refresh.", show_alert=True)
        return

    await _answer_safely(update, "❌ Deal cancelled.")
    await query_edit(update, updated, None)

    if updated.get("seller_id"):
        await context.bot.send_message(
            chat_id=updated["seller_id"], text=f"❌ Deal #{deal_id} was cancelled by the buyer."
        )


# ---------------------------------------------------------------------------
# Refresh / view (also used for the generic 🔄 Refresh button on terminal deals)
# ---------------------------------------------------------------------------

async def _handle_view(update: Update, context: ContextTypes.DEFAULT_TYPE, deal_id: str) -> None:
    deal = await db.get_deal(deal_id)
    if deal is None:
        await _answer_safely(update, "❌ Deal not found.", show_alert=True)
        return
    await _answer_safely(update, "🔄 Refreshed.")
    await query_edit(update, deal, deal_detail_keyboard(deal, update.effective_user.id))
