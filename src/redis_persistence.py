
import json
from typing import Any, Dict, Optional, Tuple, cast

import redis.asyncio as redis
from telegram.ext import BasePersistence
from telegram.ext._utils.types import BD, CD, UD


class RedisPersistence(BasePersistence):
    """A class to implement persistence using Redis."""

    def __init__(self, redis_url: str):
        super().__init__()
        self.redis = redis.from_url(redis_url, decode_responses=True)

    def _get_key(self, key_type: str, key: Any) -> str:
        """Generate a redis key."""
        return f"telegram_bot:{key_type}:{key}"

    async def get_bot_data(self) -> BD:
        key = self._get_key("bot_data", "bot")
        data_str = await self.redis.get(key)
        if data_str:
            return cast(BD, json.loads(data_str))
        return cast(BD, {})

    async def update_bot_data(self, data: BD) -> None:
        key = self._get_key("bot_data", "bot")
        await self.redis.set(key, json.dumps(data))

    async def get_chat_data(self) -> Dict[int, CD]:
        keys = await self.redis.keys(self._get_key("chat_data", "*"))
        chat_data: Dict[int, CD] = {}
        for key in keys:
            chat_id_str = key.split(":")[-1]
            if chat_id_str.isdigit():
                chat_id = int(chat_id_str)
                data_str = await self.redis.get(key)
                if data_str:
                    chat_data[chat_id] = json.loads(data_str)
        return chat_data

    async def update_chat_data(self, chat_id: int, data: CD) -> None:
        key = self._get_key("chat_data", chat_id)
        await self.redis.set(key, json.dumps(data))

    async def get_user_data(self) -> Dict[int, UD]:
        keys = await self.redis.keys(self._get_key("user_data", "*"))
        user_data: Dict[int, UD] = {}
        for key in keys:
            user_id_str = key.split(":")[-1]
            if user_id_str.isdigit():
                user_id = int(user_id_str)
                data_str = await self.redis.get(key)
                if data_str:
                    user_data[user_id] = json.loads(data_str)
        return user_data

    async def update_user_data(self, user_id: int, data: UD) -> None:
        key = self._get_key("user_data", user_id)
        await self.redis.set(key, json.dumps(data))

    async def get_callback_data(self) -> Optional[Any]:
        key = self._get_key("callback_data", "callback")
        data_str = await self.redis.get(key)
        if data_str:
            return json.loads(data_str)
        return None

    async def update_callback_data(self, data: Any) -> None:
        key = self._get_key("callback_data", "callback")
        await self.redis.set(key, json.dumps(data))

    async def drop_chat_data(self, chat_id: int) -> None:
        key = self._get_key("chat_data", chat_id)
        await self.redis.delete(key)

    async def drop_user_data(self, user_id: int) -> None:
        key = self._get_key("user_data", user_id)
        await self.redis.delete(key)

    async def refresh_bot_data(self, bot_data: BD) -> None:
        key = self._get_key("bot_data", "bot")
        data_str = await self.redis.get(key)
        if data_str:
            bot_data.update(json.loads(data_str))

    async def refresh_chat_data(self, chat_id: int, chat_data: CD) -> None:
        key = self._get_key("chat_data", chat_id)
        data_str = await self.redis.get(key)
        if data_str:
            chat_data.update(json.loads(data_str))

    async def refresh_user_data(self, user_id: int, user_data: UD) -> None:
        key = self._get_key("user_data", user_id)
        data_str = await self.redis.get(key)
        if data_str:
            user_data.update(json.loads(data_str))

    async def get_conversations(self, name: str) -> Dict:
        key = self._get_key("conversations", name)
        data_str = await self.redis.get(key)
        if data_str:
            try:
                return json.loads(data_str)
            except json.JSONDecodeError:
                return {}
        return {}

    async def update_conversation(
        self, name: str, key: Tuple[int, ...], new_state: Optional[object]
    ) -> None:
        conversations = await self.get_conversations(name)
        conversations[str(key)] = new_state
        redis_key = self._get_key("conversations", name)
        await self.redis.set(redis_key, json.dumps(conversations))

    async def flush(self) -> None:
        """Flushes all data in redis."""
        await self.redis.flushdb()
