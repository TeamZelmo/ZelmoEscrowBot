"""
keyboards.py
------------
Defines the permanent bottom Reply Keyboard (the main menu) and the
dynamic Inline Keyboards attached to individual deal messages.
"""

from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ---------------------------------------------------------------------------
# Reply keyboard (persistent bottom menu)
# ---------------------------------------------------------------------------

BTN_CREATE_DEAL = "🤝 Create Deal"
BTN_MY_DEALS = "📦 My Deals"
BTN_HELP = "❓ Help / Support"
BTN_PROFILE = "👤 Profile"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """The persistent bottom menu shown after /start."""
    keyboard = [
        [KeyboardButton(BTN_CREATE_DEAL), KeyboardButton(BTN_MY_DEALS)],
        [KeyboardButton(BTN_HELP), KeyboardButton(BTN_PROFILE)],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


# ---------------------------------------------------------------------------
# Inline keyboards (attached to individual deal messages)
# ---------------------------------------------------------------------------

def join_deal_keyboard(deal_id: str) -> InlineKeyboardMarkup:
    """Shown while a deal has no seller yet -- lets someone claim it, or the buyer cancel it."""
    buttons = [
        [InlineKeyboardButton("🙋 Join as Seller", callback_data=f"join:{deal_id}")],
        [InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cancel:{deal_id}")],
    ]
    return InlineKeyboardMarkup(buttons)


def payment_keyboard(deal_id: str) -> InlineKeyboardMarkup:
    """Shown to the buyer once a seller has joined and payment is due."""
    buttons = [
        [InlineKeyboardButton("💳 Pay Now", callback_data=f"pay:{deal_id}")],
        [InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cancel:{deal_id}")],
    ]
    return InlineKeyboardMarkup(buttons)


def funds_held_keyboard(deal_id: str) -> InlineKeyboardMarkup:
    """Shown to both buyer and seller while funds sit in escrow."""
    buttons = [
        [InlineKeyboardButton("✅ Release Funds", callback_data=f"release:{deal_id}")],
        [InlineKeyboardButton("⚠️ Raise Dispute", callback_data=f"dispute:{deal_id}")],
    ]
    return InlineKeyboardMarkup(buttons)


def deal_detail_keyboard(deal: dict, viewer_id: int) -> InlineKeyboardMarkup:
    """
    Picks the correct inline keyboard for a deal based on its current status
    AND who is viewing it (buyer vs. seller vs. an outsider). Used by
    "My Deals" and /view_deal so every card shows only actions the viewer
    is actually authorized to take.
    """
    from database import DealStatus  # local import avoids a circular import at module load time

    status = deal["status"]
    deal_id = deal["deal_id"]
    is_buyer = viewer_id == deal.get("buyer_id")
    is_seller = viewer_id == deal.get("seller_id")

    if status == DealStatus.AWAITING_SELLER:
        if is_buyer:
            return InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cancel:{deal_id}")]]
            )
        return join_deal_keyboard(deal_id)

    if status == DealStatus.AWAITING_PAYMENT and is_buyer:
        return payment_keyboard(deal_id)

    if status == DealStatus.FUNDS_HELD and (is_buyer or is_seller):
        return funds_held_keyboard(deal_id)

    # Terminal states (RELEASED / CANCELLED / REFUNDED / DISPUTED) or a
    # viewer with no active role -- just offer a refresh.
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"view:{deal_id}")]])


def admin_dispute_keyboard(deal_id: str) -> InlineKeyboardMarkup:
    """Shown to admins on a disputed deal so they can pick a winner inline."""
    buttons = [
        [
            InlineKeyboardButton("👤 Favor Buyer (Refund)", callback_data=f"resolve:{deal_id}:buyer"),
            InlineKeyboardButton("🛍️ Favor Seller (Release)", callback_data=f"resolve:{deal_id}:seller"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)
