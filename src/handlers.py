"""
Asynchronous command handlers for the Telegram bot.
All handlers must be `async` and use the async `db` module.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from . import db

logger = logging.getLogger(__name__)

# === Utility Functions ===

async def get_user_id(update: Update) -> int | None:
    """Extracts user ID from an update."""
    if update.effective_user:
        return update.effective_user.id
    return None

def build_settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    """Builds the dynamic settings keyboard with current values."""
    keyboard = []
    setting_order = [
        'autotrade', 'trading_mode', 'rsi_buy', 'rsi_sell', 'stop_loss',
        'trailing_activation', 'trailing_drop', 'profit_target', 'paper_balance', 'watchlist'
    ]

    for key in setting_order:
        value = settings.get(key)
        # Truncate long watchlist for display
        if key == 'watchlist':
            display_value = f": {value[:30]}..." if value and len(value) > 30 else f": {value}"
        else:
            display_value = f': {value}' if value is not None else ''

        if key == 'autotrade':
            action = 'off' if value == 'on' else 'on'
            button_text = f"Auto-Trading: {'✅ ON' if value == 'on' else '❌ OFF'}"
            callback_data = f"set:{key}:{action}"
        elif key == 'trading_mode':
            action = 'PAPER' if value == 'LIVE' else 'LIVE'
            button_text = f"Mode: {'💵 LIVE' if value == 'LIVE' else '📄 PAPER'}"
            callback_data = f"set:{key}:{action}"
        else:
            button_text = f"{key.replace('_', ' ').title()}{display_value}"
            callback_data = f"prompt:{key}"

        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    keyboard.append([InlineKeyboardButton("Done", callback_data="settings_done")])
    return InlineKeyboardMarkup(keyboard)

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
    await update.message.reply_text(f"{welcome_message}\n\nUse /status or /myprofile to see your configuration.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's current settings and open trades."""
    user_id = await get_user_id(update)
    if not user_id: return

    settings = await db.get_user_effective_settings(user_id)
    open_trades = await db.get_open_trades_by_user(user_id)

    status_text = "*Your Imperial Command Center*\n\n"
    status_text += "*Strategic Settings:*\n"
    for key, value in settings.items():
        key_name = key.replace('_', ' ').title()
        # Escape characters for MarkdownV2.
        value_str = str(value)
        for char in ['.', '-', '!', '(', ')', '[', ']', '{', '}', '+', '#']:
             value_str = value_str.replace(char, f'\{char}')
        status_text += f"- *{key_name}*: `{value_str}`\n"


    if open_trades:
        status_text += "\n*Active Campaigns (Open Trades):*\n"
        for trade in open_trades:
             status_text += f"- `{trade['symbol']}` @ ${trade['buy_price']:,.4f}\n"
    else:
        status_text += "\n*No active campaigns at this time.*\n"

    await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN_V2)

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for the /status command."""
    await status_command(update, context)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the settings management keyboard."""
    user_id = await get_user_id(update)
    if not user_id: return

    settings = await db.get_user_effective_settings(user_id)
    keyboard = build_settings_keyboard(settings)
    await update.message.reply_text("Choose a setting to adjust, or select a toggle:", reply_markup=keyboard)

async def settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles all inline keyboard interactions for settings."""
    query = update.callback_query
    await query.answer()
    user_id = await get_user_id(update)
    if not user_id: return

    parts = query.data.split(':')
    action = parts[0]

    if action == 'settings_done':
        await query.edit_message_text("Settings saved. The empire adapts to your command.")
        return

    setting_key = parts[1]

    if action == 'set':  # For toggles
        new_value = parts[2]
        await db.update_user_setting(user_id, setting_key, new_value)
        logger.info(f"User {user_id} updated setting '{setting_key}' to '{new_value}'.")

        new_settings = await db.get_user_effective_settings(user_id)
        keyboard = build_settings_keyboard(new_settings)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except BadRequest as e:
            if "Message is not modified" in str(e):
                logger.warning("Settings keyboard did not change; suppressing error.")
            else:
                raise

    elif action == 'prompt':
        context.user_data['awaiting_setting'] = setting_key
        await query.message.reply_text(f"Please enter the new value for *{setting_key.replace('_', ' ').title()}*\.", parse_mode=ParseMode.MARKDOWN_V2)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles text messages, specifically for updating settings."""
    user_id = await get_user_id(update)
    if not user_id or 'awaiting_setting' not in context.user_data:
        return

    setting_key = context.user_data.pop('awaiting_setting')
    new_value = update.message.text

    try:
        await db.update_user_setting(user_id, setting_key, new_value)
        logger.info(f"User {user_id} set '{setting_key}' to '{new_value}'.")
        await update.message.reply_text(f"✅ *{setting_key.replace('_', ' ').title()}* has been updated\.", parse_mode=ParseMode.MARKDOWN_V2)

        settings = await db.get_user_effective_settings(user_id)
        keyboard = build_settings_keyboard(settings)
        await update.message.reply_text("Settings updated. Choose another setting or select Done:", reply_markup=keyboard)
    except ValueError as e:
        await update.message.reply_text(str(e))
    except Exception as e:
        logger.error(f"Failed to update setting {setting_key} for user {user_id}: {e}")
        await update.message.reply_text("An error occurred. The Imperial Guard has been notified.")

PAYMENT_MESSAGE = '''
<b>💳 Subscription & Payment Information</b>

To unlock the full power of the empire, a subscription is required.

<b>Available Tiers:</b>
- <b>Centurion:</b> Access to all core features.
- <b>Legate:</b> Priority access and advanced analytics.
- <b>Emperor:</b> Direct line to the architects for feature requests.

Please contact the administration to arrange for payment and activation.
'''

async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the payment information."""
    if update.effective_chat and update.effective_chat.type != 'private':
        await update.message.reply_text("For your security, please use this command in a private chat with me.")
        return
    await update.message.reply_html(PAYMENT_MESSAGE)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors and handle them gracefully."""
    if isinstance(context.error, BadRequest) and "Message is not modified" in str(context.error):
        logger.warning(f"Suppressing 'Message is not modified' error for update: {update}")
        return

    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("An internal error occurred. The Imperial Guard has been notified.")
        except Exception as e:
            logger.error(f"Failed to send error message to user: {e}")
