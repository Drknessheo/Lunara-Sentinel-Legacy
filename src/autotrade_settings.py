import json
from typing import Optional
from .core.redis_client import get_redis_client

# --- Key Definitions for Autotrade Settings ---
# This is the single source of truth for all user-configurable settings.
KEY_DEFINITIONS = {
    # Grand Campaign Goal
    "portfolio_target_usdt": {
        "name": "Portfolio Target (USDT)",
        "default": 0.0,  # 0.0 means the campaign is continuous and has no end target.
        "type": float,
        "min": 0.0,
        "max": 10000000.0,
        "description": "The autotrader will run until your portfolio value reaches this target. Set to 0 to run continuously."
    },
    # Core Trading Strategy
    "profit_target_percentage": {
        "name": "Initial Profit Target (%)",
        "default": 3.0,
        "type": float,
        "min": 0.5,
        "max": 100.0,
        "description": "The profit percentage that arms the trailing stop loss."
    },
    "stop_loss_percentage": {
        "name": "Stop Loss (%)",
        "default": 5.0,
        "type": float,
        "min": 0.5,
        "max": 50.0,
        "description": "The absolute maximum loss before a position is sold."
    },
    # Trailing Stop (The Dragon)
    "trailing_activation_percentage": {
        "name": "Trailing Activation (%)",
        "default": 1.0,
        "type": float,
        "min": 0.1,
        "max": 50.0,
        "description": "Percentage the price must rise above the initial profit target to activate the trailing stop."
    },
    "trailing_stop_drop_percentage": {
        "name": "Trailing Stop Drop (%)",
        "default": 0.5,
        "type": float,
        "min": 0.1,
        "max": 20.0,
        "description": "The percentage the price can drop from its peak before selling to lock in profit."
    },
    # Tactical Controls
    "trade_size_usdt": {
        "name": "Trade Size (USDT)",
        "default": 10.0,
        "type": float,
        "min": 5.0,
        "max": 100000.0,
        "description": "The amount of USDT to use for each individual trade."
    },
    "max_hold_time": {
        "name": "Max Hold Time (seconds)",
        "default": 86400,  # 24 hours
        "type": int,
        "min": 300,  # 5 minutes
        "max": 2592000,  # 30 days
        "description": "Maximum time to hold a position before a tactical retreat (sell)."
    },
}

def get_user_settings(user_id: int) -> dict:
    """Fetches a user's settings from Redis."""
    client = get_redis_client()
    if not client:
        return {}
    key = f"autotrade:settings:{user_id}"
    stored_settings = client.get(key)
    if stored_settings:
        return json.loads(stored_settings)
    return {}

def get_effective_settings(user_id: int) -> dict:
    """Merges user-specific settings with system defaults to get the final active settings."""
    defaults = {key: details['default'] for key, details in KEY_DEFINITIONS.items()}
    user_specific = get_user_settings(user_id)
    return {**defaults, **user_specific}

def validate_and_set(user_id: int, key: str, value_str: str) -> tuple[bool, str]:
    """Validates a new setting and, if valid, saves it for the user."""
    key = key.lower()
    if key not in KEY_DEFINITIONS:
        return False, f"Unknown setting '{key}'."

    spec = KEY_DEFINITIONS[key]
    try:
        # Coerce value to the correct type
        if spec['type'] is float:
            coerced_value = float(value_str)
        elif spec['type'] is int:
            coerced_value = int(value_str)
        else:
            coerced_value = value_str
    except ValueError:
        return False, f"Invalid value for {spec['name']}. Expected a {spec['type'].__name__}."

    # Validate range
    if 'min' in spec and coerced_value < spec['min']:
        return False, f"{spec['name']} cannot be less than {spec['min']}."
    if 'max' in spec and coerced_value > spec['max']:
        return False, f"{spec['name']} cannot be more than {spec['max']}."

    # Persist the validated setting
    client = get_redis_client()
    if not client:
        return False, "Error: Could not connect to settings database."

    redis_key = f"autotrade:settings:{user_id}"
    current_settings = get_user_settings(user_id)
    current_settings[key] = coerced_value

    # Inter-field validation for the trailing stop
    trailing_activation = current_settings.get('trailing_activation_percentage', KEY_DEFINITIONS['trailing_activation_percentage']['default'])
    trailing_drop = current_settings.get('trailing_stop_drop_percentage', KEY_DEFINITIONS['trailing_stop_drop_percentage']['default'])

    if trailing_drop >= trailing_activation:
        return False, "Validation Error: Trailing Stop Drop must be less than the Trailing Activation percentage."

    try:
        client.set(redis_key, json.dumps(current_settings))
        return True, f"✅ {spec['name']} has been set to {coerced_value}."
    except Exception as e:
        return False, f"Error saving setting: {e}"
