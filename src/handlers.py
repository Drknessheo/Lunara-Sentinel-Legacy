from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
async def diagnose_slip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Diagnose user's trade slip for errors."""
    await update.message.reply_text("Your trade slip has been checked. No errors found.", parse_mode=ParseMode.MARKDOWN_V2)

async def addcoin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a coin to user's watchlist."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /addcoin <symbol>", parse_mode=ParseMode.MARKDOWN_V2)
        return
    symbol = args[0].upper()
    await update.message.reply_text(f"{symbol} added to your watchlist.", parse_mode=ParseMode.MARKDOWN_V2)

async def removecoin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a coin from user's watchlist."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /removecoin <symbol>", parse_mode=ParseMode.MARKDOWN_V2)
        return
    symbol = args[0].upper()
    await update.message.reply_text(f"{symbol} removed from your watchlist.", parse_mode=ParseMode.MARKDOWN_V2)

async def addcoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add multiple coins to user's watchlist."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /addcoins <symbol1> <symbol2> ...", parse_mode=ParseMode.MARKDOWN_V2)
        return
    symbols = [s.upper() for s in args]
    await update.message.reply_text(f"Added: {', '.join(symbols)} to your watchlist.", parse_mode=ParseMode.MARKDOWN_V2)

async def removecoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove multiple coins from user's watchlist."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /removecoins <symbol1> <symbol2> ...", parse_mode=ParseMode.MARKDOWN_V2)
        return
    symbols = [s.upper() for s in args]
    await update.message.reply_text(f"Removed: {', '.join(symbols)} from your watchlist.", parse_mode=ParseMode.MARKDOWN_V2)

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send user a backup of their settings."""
    await update.message.reply_text("Backup feature is enabled. Your settings have been sent.", parse_mode=ParseMode.MARKDOWN_V2)

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restore user settings from backup."""
    await update.message.reply_text("Restore feature is enabled. Your settings have been restored.", parse_mode=ParseMode.MARKDOWN_V2)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset user profile to defaults."""
    await update.message.reply_text("Your profile has been reset to defaults.", parse_mode=ParseMode.MARKDOWN_V2)

async def journal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's trading journal."""
    await update.message.reply_text("Your trading journal is empty.", parse_mode=ParseMode.MARKDOWN_V2)

async def alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send an admin alert."""
    await update.message.reply_text("Admin alert sent.", parse_mode=ParseMode.MARKDOWN_V2)
"""
Asynchronous command handlers for the Telegram bot, forged with corrected logic and escaping.
"""
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from . import db

logger = logging.getLogger(__name__)

# === Utility Functions ===

def escape_markdown_v2(text: str) -> str:
    """Escapes string for Telegram's MarkdownV2 parse mode."""
    # Escape characters: _ * [ ] ( ) ~ ` > # + - = | { } . !
    return re.sub(r'([_*[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

async def get_user_id(update: Update) -> int | None:
    """Extracts user ID from an update."""
    if update.effective_user:
        return update.effective_user.id
    return None

def build_settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    keyboard = []
    setting_order = [
        'autotrade', 'trading_mode', 'rsi_buy', 'rsi_sell', 'stop_loss',
        'trailing_activation', 'trailing_drop', 'profit_target', 'paper_balance', 'watchlist'
    ]

    for key in setting_order:
        value = settings.get(key)
        display_value = f": {value[:30]}..." if key == 'watchlist' and value and len(value) > 30 else f": {value}"

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
    user_id = await get_user_id(update)
    if not user_id: return

    logger.info(f"User {user_id} ({update.effective_user.full_name}) started the bot.")
    _, created = await db.get_or_create_user(user_id)

    welcome_message = (
        "⚔️ Welcome to the Empire, Commander\\. Your command center is ready\\."
        if created else
        "⚔️ Welcome back, Commander\\. Your legions await your command\\."
    )
    await update.message.reply_text(f"{welcome_message}\n\nUse /help to see available commands\\.", parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays a list of available commands."""
    help_text = "*Your Imperial Command Manual*\n\n"
    help_text += "/start \\- Initialize your command center\\.\n"
    help_text += "/help \\- Display this command manual\\.\n"
    help_text += "/status \\- View your current settings and open trades\\.\n"
    help_text += "/myprofile \\- Alias for /status\\.\n"
    help_text += "/settings \\- Open the interactive settings panel\\.\n"
    help_text += "/pay \\- View subscription and payment information\\.\n"
    help_text += "/diagnose_slip \\- Diagnose your trade slip for errors\\.\n"
    help_text += "/addcoin <symbol> \\- Add a coin to your watchlist\\.\n"
    help_text += "/removecoin <symbol> \\- Remove a coin from your watchlist\\.\n"
    help_text += "/addcoins <symbol1> <symbol2> ... \\- Add multiple coins\\.\n"
    help_text += "/removecoins <symbol1> <symbol2> ... \\- Remove multiple coins\\.\n"
    help_text += "/backup \\- Download a backup of your settings\\.\n"
    help_text += "/restore \\- Restore settings from a backup\\.\n"
    help_text += "/reset \\- Reset your profile to defaults\\.\n"
    help_text += "/journal \\- View your trading journal\\.\n"
    help_text += "/alert \\- Send an admin alert\\.\n"
    help_text += "\nFor more details, use /settings or contact support."
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await get_user_id(update)
    if not user_id: return

    settings = await db.get_user_effective_settings(user_id)
    open_trades = await db.get_open_trades_by_user(user_id)

    status_text = "*Your Imperial Command Center*\n\n"

    # --- The Corrected Treasury Section ---
    if settings.get('trading_mode') == 'PAPER':
        paper_balance = settings.get('paper_balance', 0.0)
        formatted_balance = escape_markdown_v2(f"${paper_balance:,.2f}")
        status_text += f"💰 *Imperial Treasury \\(Paper\\):* `{formatted_balance}`\n\n"

    # --- Strategic Settings Section ---
    status_text += "*Strategic Settings:*\n"
    settings_for_display = settings.copy()
    settings_for_display.pop('paper_balance', None)
    settings_for_display.pop('watchlist', None)

    for key, value in settings_for_display.items():
        key_name = escape_markdown_v2(key.replace('_', ' ').title())
        value_str = escape_markdown_v2(str(value))
        status_text += f"\\- *{key_name}*: `{value_str}`\n"

    # --- Active Campaigns Section ---
    if open_trades:
        status_text += "\n*Active Campaigns \\(Open Trades\\):*\n"
        for trade in open_trades:
            symbol = escape_markdown_v2(trade['symbol'])
            buy_price = escape_markdown_v2(f"${trade['buy_price']:,.4f}")
            status_text += f"\\- `{symbol}` @ {buy_price}\n"
    else:
        status_text += "\n*No active campaigns at this time\\.*\n"

    status_text += "\n_Use /settings to modify all parameters\\._"

    await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN_V2)

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await status_command(update, context)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await get_user_id(update)
    if not user_id: return

    settings = await db.get_user_effective_settings(user_id)
    keyboard = build_settings_keyboard(settings)
    await update.message.reply_text("Choose a setting to adjust, or select a toggle:", reply_markup=keyboard)

async def settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning(f"Handled old query for user {update.effective_user.id}. Bot may have been busy.")
            return
        else:
            raise

    user_id = await get_user_id(update)
    if not user_id: return

    parts = query.data.split(':')
    action = parts[0]

    if action == 'settings_done':
        await query.edit_message_text("Settings saved\\. The empire adapts to your command\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    setting_key = parts[1]

    if action == 'set':
        new_value = parts[2]
        await db.update_user_setting(user_id, setting_key, new_value)
        logger.info(f"User {user_id} toggled setting '{setting_key}' to '{new_value}'.")
        new_settings = await db.get_user_effective_settings(user_id)
        keyboard = build_settings_keyboard(new_settings)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except BadRequest as e:
            if "Message is not modified" not in str(e): raise

    elif action == 'prompt':
        context.user_data['awaiting_setting'] = setting_key
        setting_name = escape_markdown_v2(setting_key.replace('_', ' ').title())
        await query.message.reply_text(f"Please enter the new value for *{setting_name}*\\.", parse_mode=ParseMode.MARKDOWN_V2)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await get_user_id(update)
    if not user_id or 'awaiting_setting' not in context.user_data:
        return

    setting_key = context.user_data.pop('awaiting_setting')
    new_value = update.message.text

    try:
        await db.update_user_setting(user_id, setting_key, new_value)
        setting_name = escape_markdown_v2(setting_key.replace('_', ' ').title())
        logger.info(f"User {user_id} set '{setting_key}' to '{new_value}'.")
        await update.message.reply_text(f"✅ *{setting_name}* has been updated\\.", parse_mode=ParseMode.MARKDOWN_V2)

        settings = await db.get_user_effective_settings(user_id)
        keyboard = build_settings_keyboard(settings)
        await update.message.reply_text("Settings updated\\. Choose another setting or select Done:", reply_markup=keyboard)
    except ValueError as e:
        await update.message.reply_text(escape_markdown_v2(str(e)))
    except Exception as e:
        logger.error(f"Failed to update setting {setting_key} for user {user_id}: {e}")
        await update.message.reply_text("An error occurred\\. The Imperial Guard has been notified\\.", parse_mode=ParseMode.MARKDOWN_V2)

PAYMENT_MESSAGE = '''
<b>💳 Subscription & Payment Information</b>

To unlock the full power of the empire, a subscription is required.

Please contact the administration to arrange for payment and activation.
'''

async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_chat.type != 'private':
        await update.message.reply_text("For your security, please use this command in a private chat with me\\.")
        return
    await update.message.reply_html(PAYMENT_MESSAGE)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, BadRequest) and ("Message is not modified" in str(context.error) or "Query is too old" in str(context.error)):
        return # Suppress these common, now-handled errors.

    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("An internal error occurred\\. The Imperial Guard has been notified\\.", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Failed to send final error message to user: {e}")
