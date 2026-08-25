import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from i18n.messages import normalize_language
from infrastructure import database as db


class UserActivityMiddleware(BaseMiddleware):
    """Persist the last interaction time without making activity tracking user-facing."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get('event_from_user')
        if user is not None:
            try:
                await db.record_user_activity(
                    user.id,
                    normalize_language(getattr(user, 'language_code', None)),
                )
            except Exception as error:
                logging.warning('Не вдалося оновити активність користувача %s: %s', user.id, error)
        return await handler(event, data)
