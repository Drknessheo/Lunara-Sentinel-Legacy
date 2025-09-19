
import logging
from . import db as new_db

logger = logging.getLogger(__name__)

async def get_effective_settings(user_id: int) -> dict:
    """
    Fetches the user's effective settings directly from the main database.
    This acts as an async wrapper for the synchronous DB call.
    """
    try:
        settings = new_db.get_user_effective_settings(user_id)
        return settings
    except Exception as e:
        logger.error(f"Error fetching effective settings for user {user_id} from DB: {e}")
        return {}

async def validate_and_set(user_id: int, key: str, value_str: str) -> tuple[bool, str]:
    """
    Validates a new setting and saves it to the main SQLite database by calling
    the centralized logic in the `db` module.
    """
    key = key.lower()
    
    # Mapping from the command-line key to a more user-friendly name for messages.
    setting_name_map = {
        'rsi_buy': 'RSI Buy Threshold', 'rsi_sell': 'RSI Sell Threshold', 'stop_loss': 'Stop Loss %',
        'trailing_activation': 'Trailing Activation %', 'trailing_drop': 'Trailing Drop %',
        'profit_target': 'Profit Target %', 'autotrade': 'Autotrade',
        'trading_mode': 'Trading Mode', 'paper_balance': 'Paper Balance',
        'watchlist': 'Watchlist'
    }

    try:
        # The update_user_setting function in db.py handles all validation and conversion.
        # It will raise ValueError for invalid inputs.
        new_db.update_user_setting(user_id, key, value_str)
        
        display_name = setting_name_map.get(key, key)
        
        # For autotrade, provide a more descriptive message
        if key == 'autotrade':
            status = "enabled" if value_str.lower() in ['on', 'true', '1', 'enabled'] else "disabled"
            return True, f"✅ Autotrade has been {status}."

        return True, f"✅ {display_name} has been updated to {value_str}."

    except ValueError as e:
        # Catches validation errors from the db.py module (e.g., invalid trading_mode)
        logger.warning(f"Validation failed for user {user_id} setting '{key}': {e}")
        return False, str(e)
    except Exception as e:
        logger.error(f"Unexpected error updating setting '{key}' for user {user_id}: {e}")
        return False, "An unexpected error occurred while saving your setting."

