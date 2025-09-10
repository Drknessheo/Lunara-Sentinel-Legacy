import json
from typing import Any, Dict, Optional, Tuple

import redis
from telegram.ext import BasePersistence

# python-telegram-bot v22 uses different internal typing locations; avoid
# importing private package internals. Define a lightweight alias matching
# the expected shape used by the bot's conversation storage code.
ConversationDict = Dict[Tuple[int, ...], Any]


class RedisPersistence(BasePersistence):
    def __init__(self, redis_url: str):
        super().__init__()
        self.redis = redis.from_url(redis_url)

    def _get_key(self, key_type: str, key_id: int) -> str:
        return f"telegram_bot:{key_type}:{key_id}"

    def get_user_data(self) -> Dict[int, Dict[Any, Any]]:
        # Not efficient to get all user data at once, so we'll handle it on a per-user basis.
        # This method is called once on startup to populate application.user_data
        return {}

    def get_chat_data(self) -> Dict[int, Dict[Any, Any]]:
        # Similar to user_data, we handle this per-chat.
        return {}

    def get_bot_data(self) -> Dict[Any, Any]:
        key = "telegram_bot:bot_data"
        data = self.redis.get(key)
        return json.loads(data) if data else {}

    def get_conversations(self, name: str) -> ConversationDict:
        key = f"telegram_bot:conversations:{name}"
        data = self.redis.get(key)
        if data:
            raw_conversations = json.loads(data)
            return {
                tuple(map(int, k.split(","))): v for k, v in raw_conversations.items()
            }
        return {}

    def update_user_data(self, user_id: int, data: Dict[Any, Any]) -> None:
        key = self._get_key("user_data", user_id)
        self.redis.set(key, json.dumps(data))

    def update_chat_data(self, chat_id: int, data: Dict[Any, Any]) -> None:
        key = self._get_key("chat_data", chat_id)
        self.redis.set(key, json.dumps(data))

    def update_bot_data(self, data: Dict[Any, Any]) -> None:
        key = "telegram_bot:bot_data"
        self.redis.set(key, json.dumps(data))

    def update_conversation(
        self, name: str, key: Tuple[int, ...], new_state: Optional[object]
    ) -> None:
        conversations = self.get_conversations(name)
        if new_state is None:
            conversations.pop(key, None)
        else:
            conversations[key] = new_state

        # Convert tuple keys to strings for JSON serialization
        serializable_conversations = {
            ",".join(map(str, k)): v for k, v in conversations.items()
        }
        redis_key = f"telegram_bot:conversations:{name}"
        self.redis.set(redis_key, json.dumps(serializable_conversations))

    def flush(self) -> None:
        # Data is written to Redis immediately, so flush is not strictly necessary.
        pass
