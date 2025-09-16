
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

PAYMENT_MESSAGE = """
💰 **How to Support LunessaSignals** 💰

Your support helps keep the signals sharp and the empire growing. Here are the ways you can pay for your subscription:

---
**Cryptocurrency (Preferred)**
---
- **Asset:** ETH (Ethereum)
- **Network:** Binance Smart Chain (BSC/BEP20)
- **Address:** `0x2f45bfeb6e499622a774f444c6fe9801e7bd2901`

*Please be sure to send on the BSC/BEP20 network to avoid loss of funds.*

---
**🇧🇩 Bangladesh (bKash / Nagad)**
---
- **Name:** Shamim Reza Saikat
- **Mobile:** `01717948095`

---
**🌍 International Bank Transfer**
---
- **Beneficiary Name:** Shamim Reza Saikat
- **Bank:** BRAC Bank PLC
- **Account Number:** `1534105036454001`
- **Branch:** Badda Branch, Dhaka
- **SWIFT/BIC Code:** `BRAKBDDHXXX`
- **Bank Address:** The Pearl Trade Center, Holding No. Cha-90/3, Pragati Sarani, Badda, Dhaka

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

