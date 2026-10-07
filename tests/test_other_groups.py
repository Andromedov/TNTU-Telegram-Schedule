import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from bot import lifecycle, schedule as schedule_handlers  # noqa: E402
from bot.common import UserState  # noqa: E402
from bot.router import ScheduleBotHandlers  # noqa: E402


def make_state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=100, chat_id=42, user_id=42),
    )


def make_callback(data: str):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=42, language_code="uk"),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


class OtherGroupTests(unittest.IsolatedAsyncioTestCase):
    def test_main_menu_contains_temporary_group_view(self):
        callbacks = [
            button.callback_data
            for row in ScheduleBotHandlers.get_main_keyboard("uk").inline_keyboard
            for button in row
        ]

        self.assertIn("view_other_group", callbacks)

    async def test_valid_group_is_kept_only_in_fsm_and_not_saved_to_database(self):
        handler = object.__new__(ScheduleBotHandlers)
        handler._generate_schedule_ui = AsyncMock(return_value=("OTHER GROUP SCHEDULE", SimpleNamespace()))
        state = make_state()
        await state.set_state(UserState.waiting_for_view_group)
        processing_message = SimpleNamespace(message_id=200, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="кн-31",
            from_user=SimpleNamespace(id=42, language_code="uk"),
            delete=AsyncMock(),
            answer=AsyncMock(return_value=processing_message),
        )
        saved_user = {"user_id": 42, "group_name": "СТс-21", "language": "uk"}

        with (
            patch.object(lifecycle.db, "get_user", new=AsyncMock(return_value=saved_user)),
            patch.object(lifecycle.db, "add_or_update_user", new=AsyncMock()) as save_user,
            patch.object(lifecycle.scraper, "check_group_exists", new=AsyncMock(return_value=True)),
        ):
            await handler.process_view_group_name_fsm(message, state)

        self.assertIsNone(await state.get_state())
        self.assertEqual("КН-31", (await state.get_data())["view_group"])
        save_user.assert_not_awaited()
        handler._generate_schedule_ui.assert_awaited_once_with(42, 0, "КН-31")
        processing_message.edit_text.assert_awaited_once()

    async def test_navigation_uses_temporary_group(self):
        handler = object.__new__(ScheduleBotHandlers)
        handler._generate_schedule_ui = AsyncMock(return_value=("schedule", SimpleNamespace()))
        state = make_state()
        await state.update_data(view_group="КН-31")
        callback = make_callback("nav_schedule:1")
        saved_user = {"user_id": 42, "group_name": "СТс-21", "language": "uk"}

        with patch.object(schedule_handlers.db, "get_user", new=AsyncMock(return_value=saved_user)):
            await handler.process_nav_schedule(callback, state)

        handler._generate_schedule_ui.assert_awaited_once_with(42, 1, "КН-31")

    async def test_week_navigation_uses_temporary_group(self):
        handler = object.__new__(ScheduleBotHandlers)
        handler._generate_week_schedule_ui = AsyncMock(return_value=("schedule", SimpleNamespace()))
        state = make_state()
        await state.update_data(view_group="КН-31")
        callback = make_callback("nav_week:1")
        saved_user = {"user_id": 42, "group_name": "СТс-21", "language": "uk"}

        with patch.object(schedule_handlers.db, "get_user", new=AsyncMock(return_value=saved_user)):
            await handler.process_nav_week(callback, state)

        handler._generate_week_schedule_ui.assert_awaited_once_with(42, 1, "КН-31")

    async def test_sharing_uses_temporary_group(self):
        handler = object.__new__(ScheduleBotHandlers)
        state = make_state()
        await state.update_data(view_group="КН-31")
        callback = make_callback("share_day:0")
        callback.message.answer = AsyncMock()
        saved_user = {"user_id": 42, "group_name": "СТс-21", "language": "uk"}
        schedule = [{"time": "08:00", "name": "Math", "is_pdf": False}]

        with (
            patch.object(schedule_handlers.db, "get_user", new=AsyncMock(return_value=saved_user)),
            patch.object(
                schedule_handlers.scraper,
                "_get_schedule_for_date",
                new=AsyncMock(return_value=schedule),
            ) as get_schedule,
        ):
            await handler.process_share_day(callback, state)

        self.assertEqual("КН-31", get_schedule.await_args.args[0])
        self.assertIn("КН-31", callback.message.answer.await_args.args[0])

    async def test_calendar_export_uses_temporary_group(self):
        handler = object.__new__(ScheduleBotHandlers)
        handler._get_user_language = AsyncMock(return_value="uk")
        state = make_state()
        await state.update_data(view_group="КН-31")
        callback = make_callback("export_ics:0")
        callback.message.answer = AsyncMock()
        callback.message.answer_document = AsyncMock()
        saved_user = {"user_id": 42, "group_name": "СТс-21", "language": "uk"}
        schedule = [{"time": "08:00-09:20", "name": "Math", "is_pdf": False}]
        schedule_handlers.ics_cooldown.pop(42, None)

        with (
            patch.object(schedule_handlers.db, "get_user", new=AsyncMock(return_value=saved_user)),
            patch.object(
                schedule_handlers.scraper,
                "_get_schedule_for_date",
                new=AsyncMock(return_value=schedule),
            ),
            patch.object(
                schedule_handlers,
                "generate_week_ics",
                return_value="BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
            ) as generate_ics,
        ):
            await handler.process_export_ics(callback, state)

        self.assertEqual("КН-31", generate_ics.call_args.args[0])
        document = callback.message.answer_document.await_args.kwargs["document"]
        self.assertIn("Schedule_КН-31_", document.filename)

    async def test_return_to_menu_restores_saved_group(self):
        handler = object.__new__(ScheduleBotHandlers)
        handler._get_next_class_text = AsyncMock(return_value="")
        state = make_state()
        await state.update_data(view_group="КН-31")
        callback = make_callback("back_to_main")
        saved_user = {"user_id": 42, "group_name": "СТс-21", "language": "uk"}

        with patch.object(lifecycle.db, "get_user", new=AsyncMock(return_value=saved_user)):
            await handler.process_back_to_main(callback, state)

        self.assertIsNone((await state.get_data()).get("view_group"))
        self.assertIn("СТс-21", callback.message.edit_text.await_args.args[0])
