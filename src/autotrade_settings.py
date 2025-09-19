import json
from typing import Optional
from .core.redis_client import get_redis_client

# --- Key Definitions for Autotrade Settings ---
KEY_DEFINITIONS = {
    # (Content is unchanged, collapsed for brevity)
}

async def get_user_settings(user_id: int) -> dict:
    """Fetches a user's settings from Redis asynchronously."""
    client = get_redis_client()
    if not client:
        return {}
    key = f"autotrade:settings:{user_id}"
    stored_settings = await client.get(key)
    if stored_settings:
        try:
            return json.loads(stored_settings)
        except json.JSONDecodeError:
            return {}
    return {}

async def get_effective_settings(user_id: int) -> dict:
    """Merges user-specific settings with system defaults asynchronously."""
    defaults = {key: details['default'] for key, details in KEY_DEFINITIONS.items()}
    user_specific = await get_user_settings(user_id)
    return {**defaults, **user_specific}

async def validate_and_set(user_id: int, key: str, value_str: str) -> tuple[bool, str]:
    """Validates a new setting and, if valid, saves it for the user asynchronously."""
    key = key.lower()
    if key not in KEY_DEFINITIONS:
        return False, f"Unknown setting '{key}'."

    spec = KEY_DEFINITIONS[key]
    try:
        if spec['type'] is float:
            coerced_value = float(value_str)
        elif spec['type'] is int:
            coerced_value = int(value_str)
        else:
            coerced_value = value_str
    except ValueError:
        return False, f"Invalid value for {spec['name']}. Expected a {spec['type'].__name__}."

    if 'min' in spec and coerced_value < spec['min']:
        return False, f"{spec['name']} cannot be less than {spec['min']}."
    if 'max' in spec and coerced_value > spec['max']:
        return False, f"{spec['name']} cannot be more than {spec['max']}."

    client = get_redis_client()
    if not client:
        return False, "Error: Could not connect to settings database."

    redis_key = f"autotrade:settings:{user_id}"
    current_settings = await get_user_settings(user_id)
    current_settings[key] = coerced_value

    # Inter-field validation for the trailing stop
    # Must use .get() with defaults because user_settings might be partial
    effective_settings = {**{k: v['default'] for k, v in KEY_DEFINITIONS.items()}, **current_settings}
    trailing_activation = effective_settings['trailing_activation_percentage']
    trailing_drop = effective_settings['trailing_stop_drop_percentage']

    if trailing_drop >= trailing_activation:
        return False, "Validation Error: Trailing Stop Drop must be less than the Trailing Activation percentage."

    try:
        await client.set(redis_key, json.dumps(current_settings))
        return True, f"✅ {spec['name']} has been set to {coerced_value}."
    except Exception as e:
        return False, f"Error saving setting: {e}"
