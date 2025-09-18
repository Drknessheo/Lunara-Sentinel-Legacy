
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

PAYMENT_MESSAGE = """
<b>💰 How to Support LunessaSignals</b> 💰

Your support helps keep the signals sharp and the empire growing. Here are the ways you can pay for your subscription:

---
<b>Cryptocurrency (Preferred)</b>
---
- <b>Asset:</b> ETH (Ethereum)
- <b>Network:</b> Binance Smart Chain (BSC/BEP20)
- <b>Address:</b> <code>0x2f45bfeb6e499622a774f444c6fe9801e7bd2901</code>

<i>Please be sure to send on the BSC/BEP20 network to avoid loss of funds.</i>

---
<b>🇧🇩 Bangladesh (bKash / Nagad)</b>
---
- <b>Name:</b> Shamim Reza Saikat
- <b>Mobile:</b> <code>01717948095</code>

---
<b>🌍 International Bank Transfer</b>
---
- <b>Beneficiary Name:</b> Shamim Reza Saikat
- <b>Bank:</b> BRAC Bank PLC
- <b>Account Number:</b> <code>1534105036454001</code>
- <b>Branch:</b> Badda Branch, Dhaka
- <b>SWIFT/BIC Code:</b> <code>BRAKBDDHXXX</code>
- <b>Bank Address:</b> The Pearl Trade Center, Holding No. Cha-90/3, Pragati Sarani, Badda, Dhaka

After making a payment, please contact the admin with a screenshot or transaction ID.
"""

async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the payment information."""
    if update.effective_chat.type != 'private':
        await update.message.reply_text("For your security, please use this command in a private chat with me.")
        return
        
    try:
        await update.message.reply_html(PAYMENT_MESSAGE)
    except Exception as e:
        logger.error(f"Failed to send payment info: {e}")
        await update.message.reply_text("Sorry, I couldn't retrieve the payment information at the moment. Please try again later.")
