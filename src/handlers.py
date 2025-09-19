
"""
Asynchronous command handlers for the Telegram bot.
All handlers must be `async` and use the async `db` module.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from . import db
from . import autotrade_settings as settings_manager

logger = logging.getLogger(__name__)

# === Utility Functions ===

async def get_user_id(update: Update) -> int | None:
    """Extracts user ID from an update."""
    if update.effective_user:
        return update.effective_user.id
    return None

def build_settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    """Builds the dynamic settings keyboard."""
    # ... (Implementation from your previous reference, adapted for clarity) ...
    return InlineKeyboardMarkup([[]]) # Placeholder

# === Core Command Handlers ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greets the user and sets up their account."""
    user_id = await get_user_id(update)
    if not user_id: return

    logger.info(f"User {user_id} ({update.effective_user.full_name}) started the bot.")
    _, created = await db.get_or_create_user(user_id)
    
    welcome_message = (
        "Welcome to the Empire, Commander. Your command center is ready."
        if created else
        "Welcome back, Commander. Your legions await your command."
    )
    await update.message.reply_text(f"{welcome_message}\n\nUse /status to see your current configuration.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's current settings and open trades."""
    user_id = await get_user_id(update)
    if not user_id: return

    settings = await db.get_user_effective_settings(user_id)
    open_trades = await db.get_open_trades_by_user(user_id)

    status_text = "*Your Imperial Command Center*\n\n"
    status_text += "*Strategic Settings:*\n"
    for key, value in settings.items():
        status_text += f"- `{key.replace('_', ' ').title()}`: {value}\n"

    if open_trades:
        status_text += "\n*Active Campaigns (Open Trades):*\n"
        for trade in open_trades:
             status_text += f"- `{trade['symbol']}` @ ${trade['buy_price']:,.4f}\n"
    else:
        status_text += "\n*No active campaigns at this time.*\n"

    await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN_V2)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the settings management keyboard."""
    user_id = await get_user_id(update)
    if not user_id: return

    settings = await db.get_user_effective_settings(user_id)
    keyboard = build_settings_keyboard(settings)
    await update.message.reply_text("Choose a setting to adjust:", reply_markup=keyboard)


async def set_setting_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline keyboard button presses for settings."""
    query = update.callback_query
    await query.answer()
    
    user_id = await get_user_id(update)
    if not user_id: return

    setting, value = query.data.split(':')
    
    await db.update_user_setting(user_id, setting, value)
    
    await query.edit_message_text(f"Setting '{setting}' updated to '{value}'.")
    settings = await db.get_user_effective_settings(user_id)
    keyboard = build_settings_keyboard(settings)
    await query.edit_message_reply_markup(reply_markup=keyboard)

# ... (Watchlist command handlers would go here, async as well) ...

PAYMENT_MESSAGE = """...""" # Your existing payment message

async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the payment information."""
    if update.effective_chat.type != 'private':
        await update.message.reply_text("For your security, please use this command in a private chat with me.")
        return
    await update.message.reply_html(PAYMENT_MESSAGE)

# === Error Handler ===

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    if isinstance(update, Update):
        await update.effective_message.reply_text("An internal error occurred. The Imperial Guard has been notified.")
