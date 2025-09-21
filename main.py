import os
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv

# Basic logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


# Load environment variables
load_dotenv()

# Use TELEGRAM_BOT_TOKEN for secret key
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = f"https://lunessasignels.onrender.com/webhook"

# --- Your Existing Bot Handlers (Example) --
# (You'll need to define these functions based on your bot's logic)
async def start(update, context):
    """Sends a welcome message when the /start command is issued."""
    await update.message.reply_text('Hello! I am your friendly Lunara bot.')

async def echo(update, context):
    """Echoes the user's message."""
    await update.message.reply_text(update.message.text)

def main() -> None:
    """Starts the bot and keeps it running."""
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    from src.handlers import (
        help_command, diagnose_slip_command, addcoin_command, removecoin_command,
        addcoins_command, removecoins_command, backup_command, restore_command,
        reset_command, journal_command, alert_command
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("diagnose_slip", diagnose_slip_command))
    application.add_handler(CommandHandler("addcoin", addcoin_command))
    application.add_handler(CommandHandler("removecoin", removecoin_command))
    application.add_handler(CommandHandler("addcoins", addcoins_command))
    application.add_handler(CommandHandler("removecoins", removecoins_command))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CommandHandler("restore", restore_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("journal", journal_command))
    application.add_handler(CommandHandler("alert", alert_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Start the Webhook for Render
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=WEBHOOK_URL
    )
    logging.info(f"Bot is running and listening on port {PORT}...")

if __name__ == "__main__":
    main()