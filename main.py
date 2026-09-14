"""
main.py
--------
Entry point for the Telegram Escrow Bot. Loads configuration from .env,
connects to MongoDB, wires up every command / message / callback handler,
and starts polling for updates.
"""

import os
import logging

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import database as db
from keyboards import BTN_CREATE_DEAL, BTN_MY_DEALS, BTN_HELP, BTN_PROFILE
from handlers import start as start_handlers
from handlers import deal as deal_handlers
from handlers import escrow as escrow_handlers

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)  # quiet noisy per-request HTTP logs
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")


async def post_init(application: Application) -> None:
    """Runs once after the Application is built: connect to MongoDB, register bot commands."""
    await db.init_db()
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Register / show the main menu"),
            BotCommand("create_deal", "Create a new escrow deal: /create_deal 100 USDT"),
            BotCommand("my_deals", "List your active and past deals"),
            BotCommand("view_deal", "View a deal by ID: /view_deal ABC123"),
            BotCommand("help", "How EscrowGuard works"),
            BotCommand("profile", "View your profile"),
        ]
    )
    logger.info("Bot initialized and ready.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler so a bug in one update never crashes the whole bot."""
    logger.error("Unhandled exception while processing update: %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. Our team has been notified."
            )
        except Exception:
            pass


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Add it to your .env file (see .env.example).")

    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # --- Commands ---
    application.add_handler(CommandHandler("start", start_handlers.start_command))
    application.add_handler(CommandHandler("help", start_handlers.help_command))
    application.add_handler(CommandHandler("profile", start_handlers.profile_command))
    application.add_handler(CommandHandler("create_deal", deal_handlers.create_deal_command))
    application.add_handler(CommandHandler("my_deals", deal_handlers.my_deals_command))
    application.add_handler(CommandHandler("view_deal", deal_handlers.view_deal_command))
    application.add_handler(CommandHandler("resolve_dispute", escrow_handlers.resolve_dispute_command))

    # --- Guided "Create Deal" conversation, triggered by the reply-keyboard button ---
    application.add_handler(deal_handlers.build_create_deal_conversation(BTN_CREATE_DEAL))

    # --- Reply-keyboard button routing (buttons that aren't conversation entry points) ---
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_MY_DEALS}$"), deal_handlers.my_deals_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_HELP}$"), start_handlers.help_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_PROFILE}$"), start_handlers.profile_command))

    # --- Inline button callbacks: join / pay / release / dispute / cancel / resolve / view ---
    application.add_handler(CallbackQueryHandler(escrow_handlers.callback_router))

    # --- Global error handler ---
    application.add_error_handler(error_handler)

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
