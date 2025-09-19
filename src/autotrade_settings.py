
import logging
from . import db

logger = logging.getLogger(__name__)

async def get_effective_settings(user_id: int) -> dict:
    """
    Asynchronously fetches the user's effective settings directly from the database.
    """
    try:
        # CORRECT: Awaiting the asynchronous database call.
        settings = await db.get_user_effective_settings(user_id)
        return settings
    except Exception as e:
        logger.error(f"Error fetching effective settings for user {user_id} from DB: {e}", exc_info=True)
        return {}

async def validate_and_set(user_id: int, key: str, value_str: str) -> tuple[bool, str]:
    """
    Asynchronously validates and saves a new setting to the database.
    """
    key = key.lower()
    
    setting_name_map = {
        'rsi_buy': 'RSI Buy Threshold', 'rsi_sell': 'RSI Sell Threshold', 'stop_loss': 'Stop Loss %',
        'trailing_activation': 'Trailing Activation %', 'trailing_drop': 'Trailing Drop %',
        'profit_target': 'Profit Target %', 'autotrade': 'Autotrade',
        'trading_mode': 'Trading Mode', 'paper_balance': 'Paper Balance',
        'watchlist': 'Watchlist'
    }

    try:
        # CORRECT: Awaiting the asynchronous database call.
        await db.update_user_setting(user_id, key, value_str)
        
        display_name = setting_name_map.get(key, key)
        
        if key == 'autotrade':
            status = "enabled" if str(value_str).lower() in ['on', 'true', '1', 'enabled'] else "disabled"
            return True, f"✅ Autotrade has been {status}."

        return True, f"✅ {display_name} has been updated to {value_str}."

    except ValueError as e:
        logger.warning(f"Validation failed for user {user_id} setting '{key}': {e}")
        return False, str(e)
    except Exception as e:
        logger.error(f"Unexpected error updating setting '{key}' for user {user_id}: {e}", exc_info=True)
        return False, "An unexpected error occurred while saving your setting."
