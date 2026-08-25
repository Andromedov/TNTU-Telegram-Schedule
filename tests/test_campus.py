import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from bot import router as handlers  # noqa: E402
from bot.router import ScheduleBotHandlers  # noqa: E402
from campus.directory import (  # noqa: E402
    BUILDINGS,
    CAMPUS_MAP_URL,
    building_card,
    building_number,
    schedule_buildings,
)


class CampusDirectoryTests(unittest.TestCase):
    def test_contains_all_buildings_and_source_addresses(self):
        self.assertEqual(list(range(1, 12)), list(BUILDINGS))
        self.assertEqual("вул. Руська, 56", BUILDINGS[1]["uk"][0])
        self.assertEqual("вул. Старий Поділ, 2", BUILDINGS[5]["uk"][0])
        self.assertEqual("вул. Білогірська, 50", BUILDINGS[10]["uk"][0])
        self.assertEqual("вул. Лук'яновича, 8", BUILDINGS[11]["uk"][0])

    def test_normalizes_building_codes_and_extracts_schedule_buildings(self):
        self.assertEqual(2, building_number("К2"))
        self.assertEqual(10, building_number("K10"))
        self.assertIsNone(building_number("ATutor"))
        self.assertEqual(
            [1, 2, 10],
            schedule_buildings(
                [
                    [
                        {"building": "К2"},
                        {"building": "K10"},
                        {"building": "К1"},
                        {"building": "К2"},
                        {"building": None},
                    ]
                ]
            ),
        )

    def test_building_card_is_localized(self):
        ukrainian = building_card(2, "uk")
        english = building_card(2, "en")
        self.assertIn("вул. Руська, 56", ukrainian)
        self.assertIn("К2-101", ukrainian)
        self.assertIn("56 Ruska Street", english)
        self.assertIn("Building 2, room 101", english)
        self.assertIsNone(building_card(99, "uk"))


class CampusKeyboardTests(unittest.TestCase):
    def test_campus_command_is_localized(self):
        ukrainian = {command.command: command.description for command in ScheduleBotHandlers.get_bot_commands("uk")}
        english = {command.command: command.description for command in ScheduleBotHandlers.get_bot_commands("en")}
        self.assertEqual("Адреси корпусів ТНТУ", ukrainian["campus"])
        self.assertEqual("TNTU building addresses", english["campus"])

    def test_main_menu_contains_campus_entry(self):
        keyboard = ScheduleBotHandlers.get_main_keyboard("en")
        callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row if button.callback_data}
        self.assertIn("show_campus", callbacks)

    def test_directory_keyboard_contains_buildings_map_and_back(self):
        keyboard = ScheduleBotHandlers.get_campus_keyboard("en")
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        callbacks = {button.callback_data for button in buttons if button.callback_data}
        urls = {button.url for button in buttons if button.url}

        self.assertTrue({f"campus_building:{number}" for number in range(1, 12)} <= callbacks)
        self.assertIn("back_to_main", callbacks)
        self.assertEqual({CAMPUS_MAP_URL}, urls)

    def test_context_shortcuts_are_chunked_by_three(self):
        rows = ScheduleBotHandlers.get_building_shortcuts([1, 2, 10, 11])
        self.assertEqual([3, 1], [len(row) for row in rows])
        self.assertEqual("campus_building:10", rows[0][2].callback_data)


class CampusHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_campus_command_works_without_registered_group(self):
        router = SimpleNamespace(
            message=SimpleNamespace(register=lambda *args, **kwargs: None),
            callback_query=SimpleNamespace(register=lambda *args, **kwargs: None),
        )
        handler = ScheduleBotHandlers(router)
        sent_message = SimpleNamespace(message_id=99)
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=1, language_code="en"),
            delete=AsyncMock(),
            answer=AsyncMock(return_value=sent_message),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={}),
            set_state=AsyncMock(),
            update_data=AsyncMock(),
        )

        with patch.object(handlers.db, "get_user", new=AsyncMock(return_value=None)):
            await handler.cmd_campus(message, state)

        text = message.answer.await_args.args[0]
        self.assertIn("TNTU buildings", text)
        state.set_state.assert_awaited_once_with(None)

    async def test_building_callback_shows_localized_card(self):
        router = SimpleNamespace(
            message=SimpleNamespace(register=lambda *args, **kwargs: None),
            callback_query=SimpleNamespace(register=lambda *args, **kwargs: None),
        )
        handler = ScheduleBotHandlers(router)
        callback = SimpleNamespace(
            data="campus_building:10",
            from_user=SimpleNamespace(id=1, language_code="en"),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

        with patch.object(handler, "_get_user_language", new=AsyncMock(return_value="en")):
            await handler.process_campus_building(callback)

        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("Building 10", text)
        self.assertIn("50 Bilohirska Street", text)
        callback.answer.assert_awaited_once()

    async def test_daily_schedule_shows_only_relevant_building_shortcuts(self):
        router = SimpleNamespace(
            message=SimpleNamespace(register=lambda *args, **kwargs: None),
            callback_query=SimpleNamespace(register=lambda *args, **kwargs: None),
        )
        handler = ScheduleBotHandlers(router)
        schedule = [
            {"time": "08:00-09:20", "name": "Online", "building": None, "is_pdf": False},
            {"time": "09:30-10:50", "name": "Lab", "building": "К2", "is_pdf": False},
            {"time": "11:10-12:30", "name": "Sport", "building": "К10", "is_pdf": False},
        ]

        with (
            patch.object(
                handlers.db,
                "get_user",
                new=AsyncMock(
                    return_value={
                        "user_id": 1,
                        "group_name": "СТ-11",
                        "language": "en",
                    }
                ),
            ),
            patch.object(handlers.scraper, "_get_schedule_for_date", new=AsyncMock(return_value=schedule)),
        ):
            _, keyboard = await handler._generate_schedule_ui(1, 0)

        callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row if button.callback_data}
        self.assertIn("campus_building:2:nav_schedule:0", callbacks)
        self.assertIn("campus_building:10:nav_schedule:0", callbacks)
        self.assertNotIn("campus_building:1", callbacks)

    async def test_context_building_callback_returns_to_original_schedule(self):
        router = SimpleNamespace(
            message=SimpleNamespace(register=lambda *args, **kwargs: None),
            callback_query=SimpleNamespace(register=lambda *args, **kwargs: None),
        )
        handler = ScheduleBotHandlers(router)
        callback = SimpleNamespace(
            data="campus_building:2:nav_week:-1",
            from_user=SimpleNamespace(id=1, language_code="uk"),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

        with patch.object(handler, "_get_user_language", new=AsyncMock(return_value="uk")):
            await handler.process_campus_building(callback)

        keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
        callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row if button.callback_data}
        self.assertIn("nav_week:-1", callbacks)
