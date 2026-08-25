from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup

from bot.common import user_language
from campus.directory import BUILDINGS, CAMPUS_MAP_URL
from i18n.messages import get_msg
from jobs.reminders import REMINDER_LESSON_TYPES, temporary_notifications_are_muted


class KeyboardMixin:
    @staticmethod
    def get_language_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="set_language:uk")],
                [InlineKeyboardButton(text="🇬🇧 English", callback_data="set_language:en")],
            ]
        )

    @staticmethod
    def get_bot_commands(language: str = "uk") -> list[BotCommand]:
        return [
            BotCommand(command="start", description=get_msg("commands.start", language=language)),
            BotCommand(command="settings", description=get_msg("commands.settings", language=language)),
            BotCommand(command="campus", description=get_msg("commands.campus", language=language)),
        ]

    @staticmethod
    def get_main_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_msg("keyboard.show_schedule", language=language), callback_data="nav_schedule:0"
                    ),
                    InlineKeyboardButton(
                        text=get_msg("keyboard.show_week", language=language), callback_data="nav_week:0"
                    ),
                ],
                [InlineKeyboardButton(text=get_msg("keyboard.campus", language=language), callback_data="show_campus")],
                [
                    InlineKeyboardButton(
                        text=get_msg("keyboard.settings", language=language), callback_data="show_settings"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=get_msg("keyboard.change_group", language=language), callback_data="change_group"
                    )
                ],
            ]
        )

    @staticmethod
    def get_campus_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        buttons = [
            InlineKeyboardButton(text=f"🏫 К{number}", callback_data=f"campus_building:{number}")
            for number in BUILDINGS
        ]
        rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
        rows.extend(
            [
                [InlineKeyboardButton(text=get_msg("campus.map", language=language), url=CAMPUS_MAP_URL)],
                [InlineKeyboardButton(text=get_msg("keyboard.back", language=language), callback_data="back_to_main")],
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def get_building_keyboard(language: str = "uk", back_callback: str = "show_campus") -> InlineKeyboardMarkup:
        back_text = get_msg(
            "campus.all" if back_callback == "show_campus" else "calendar.back",
            language=language,
        )
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=get_msg("campus.map", language=language), url=CAMPUS_MAP_URL)],
                [InlineKeyboardButton(text=back_text, callback_data=back_callback)],
                [InlineKeyboardButton(text=get_msg("keyboard.menu", language=language), callback_data="back_to_main")],
            ]
        )

    @staticmethod
    def get_building_shortcuts(
        buildings: list[int], back_callback: str | None = None
    ) -> list[list[InlineKeyboardButton]]:
        buttons = [
            InlineKeyboardButton(
                text=f"🏫 К{number}",
                callback_data=f"campus_building:{number}" + (f":{back_callback}" if back_callback else ""),
            )
            for number in buildings
        ]
        return [buttons[index : index + 3] for index in range(0, len(buttons), 3)]

    @staticmethod
    def get_settings_keyboard(user_data) -> InlineKeyboardMarkup:
        user_dict = dict(user_data)
        language = user_language(user_dict)
        notify_enabled = user_dict.get("notify_10_min", 1)
        offset = user_dict.get("reminder_offset", 10)
        first_offset = user_dict.get("first_class_reminder_offset")
        digest_enabled = user_dict.get("morning_digest", 0)
        digest_hour = int(user_dict.get("morning_digest_hour") or 7)
        quiet_start = user_dict.get("quiet_hours_start")
        quiet_end = user_dict.get("quiet_hours_end")
        enabled_types = sum(bool(user_dict.get(setting, 1)) for setting in REMINDER_LESSON_TYPES.values())

        if not notify_enabled:
            remind_text = get_msg("settings.reminder_off", language=language)
        elif offset == 60:
            remind_text = get_msg("settings.reminder_hour", language=language)
        elif offset == 90:
            remind_text = get_msg("settings.reminder_hour_half", language=language)
        else:
            remind_text = get_msg("settings.reminder_minutes", language=language, minutes=offset)

        if first_offset is None:
            first_remind_text = get_msg("settings.first_reminder_default", language=language)
        else:
            first_remind_text = get_msg("settings.first_reminder_minutes", language=language, minutes=first_offset)
        digest_text = get_msg(
            "settings.digest_enabled" if digest_enabled else "settings.digest_disabled",
            language=language,
            hour=digest_hour,
        )
        temporarily_muted = temporary_notifications_are_muted(user_dict)
        mute_text = get_msg(
            "settings.unmute_today" if temporarily_muted else "settings.mute_today",
            language=language,
        )
        mute_callback = "settings_unmute_today" if temporarily_muted else "settings_mute_today"

        if enabled_types == len(REMINDER_LESSON_TYPES):
            lesson_types_text = get_msg("settings.lesson_types_all", language=language)
        elif enabled_types == 0:
            lesson_types_text = get_msg("settings.lesson_types_none", language=language)
        else:
            lesson_types_text = get_msg(
                "settings.lesson_types_selected",
                language=language,
                enabled=enabled_types,
                total=len(REMINDER_LESSON_TYPES),
            )
        quiet_text = get_msg(
            "settings.quiet_hours_range"
            if quiet_start is not None and quiet_end is not None
            else "settings.quiet_hours_off",
            language=language,
            start=f"{int(quiet_start or 0):02d}",
            end=f"{int(quiet_end or 0):02d}",
        )

        rows = [
            [InlineKeyboardButton(text=remind_text, callback_data="settings_reminder")],
            [InlineKeyboardButton(text=first_remind_text, callback_data="settings_first_reminder")],
            [InlineKeyboardButton(text=lesson_types_text, callback_data="settings_lesson_types")],
            [InlineKeyboardButton(text=digest_text, callback_data="settings_digest")],
            [InlineKeyboardButton(text=quiet_text, callback_data="settings_quiet_hours")],
            [InlineKeyboardButton(text=mute_text, callback_data=mute_callback)],
            [
                InlineKeyboardButton(
                    text=f"{'✅' if user_dict.get('notify_evening', 1) else '❌'} "
                    f"{get_msg('keyboard.evening', language=language)}",
                    callback_data="toggle_evening",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'⏸' if user_dict.get('is_paused', 0) else '▶️'} "
                    f"{get_msg('keyboard.pause', language=language)}",
                    callback_data="toggle_pause",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅' if user_dict.get('notify_schedule_update', 1) else '❌'} "
                    f"{get_msg('keyboard.schedule_update', language=language)}",
                    callback_data="toggle_notify_schedule_update",
                )
            ],
            [
                InlineKeyboardButton(
                    text=get_msg("keyboard.language", language=language), callback_data="settings_language"
                )
            ],
            [InlineKeyboardButton(text=get_msg("keyboard.back", language=language), callback_data="back_to_main")],
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def get_reminder_settings_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_msg("settings.disable", language=language), callback_data="set_remind:0"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=get_msg("settings.minutes", language=language, minutes=10),
                        callback_data="set_remind:10",
                    ),
                    InlineKeyboardButton(
                        text=get_msg("settings.minutes", language=language, minutes=15),
                        callback_data="set_remind:15",
                    ),
                    InlineKeyboardButton(
                        text=get_msg("settings.minutes", language=language, minutes=30),
                        callback_data="set_remind:30",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=get_msg("settings.one_hour", language=language), callback_data="set_remind:60"
                    ),
                    InlineKeyboardButton(
                        text=get_msg("settings.hour_half", language=language), callback_data="set_remind:90"
                    ),
                ],
                [InlineKeyboardButton(text=get_msg("calendar.back", language=language), callback_data="show_settings")],
            ]
        )

    @staticmethod
    def get_first_reminder_settings_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_msg("settings.same_as_other", language=language),
                        callback_data="set_first_remind:default",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=get_msg("settings.minutes", language=language, minutes=15),
                        callback_data="set_first_remind:15",
                    ),
                    InlineKeyboardButton(
                        text=get_msg("settings.minutes", language=language, minutes=30),
                        callback_data="set_first_remind:30",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=get_msg("settings.one_hour", language=language),
                        callback_data="set_first_remind:60",
                    ),
                    InlineKeyboardButton(
                        text=get_msg("settings.hour_half", language=language),
                        callback_data="set_first_remind:90",
                    ),
                ],
                [InlineKeyboardButton(text=get_msg("calendar.back", language=language), callback_data="show_settings")],
            ]
        )

    @staticmethod
    def get_digest_settings_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_msg("settings.digest_off", language=language), callback_data="set_digest:off"
                    )
                ],
                [
                    InlineKeyboardButton(text="06:00", callback_data="set_digest:6"),
                    InlineKeyboardButton(text="07:00", callback_data="set_digest:7"),
                ],
                [
                    InlineKeyboardButton(text="08:00", callback_data="set_digest:8"),
                    InlineKeyboardButton(text="09:00", callback_data="set_digest:9"),
                ],
                [InlineKeyboardButton(text=get_msg("calendar.back", language=language), callback_data="show_settings")],
            ]
        )

    @staticmethod
    def get_lesson_type_settings_keyboard(user_data, language: str = "uk") -> InlineKeyboardMarkup:
        user_dict = dict(user_data)
        rows = []
        for category, setting in REMINDER_LESSON_TYPES.items():
            enabled = bool(user_dict.get(setting, 1))
            label = get_msg(f"settings.lesson_type_{category}", language=language)
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{'✅' if enabled else '❌'} {label}",
                        callback_data=f"toggle_reminder_type:{category}",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text=get_msg("calendar.back", language=language), callback_data="show_settings")]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def get_quiet_hours_keyboard(user_data, language: str = "uk") -> InlineKeyboardMarkup:
        user_dict = dict(user_data)
        start = user_dict.get("quiet_hours_start")
        end = user_dict.get("quiet_hours_end")
        enabled = start is not None and end is not None
        start = int(start) if start is not None else 22
        end = int(end) if end is not None else 7
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_msg(
                            "settings.quiet_disable" if enabled else "settings.quiet_enable",
                            language=language,
                        ),
                        callback_data="toggle_quiet_hours",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=get_msg("settings.quiet_start", language=language, hour=f"{start:02d}"),
                        callback_data="choose_quiet_hour:start",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=get_msg("settings.quiet_end", language=language, hour=f"{end:02d}"),
                        callback_data="choose_quiet_hour:end",
                    )
                ],
                [InlineKeyboardButton(text=get_msg("calendar.back", language=language), callback_data="show_settings")],
            ]
        )

    @staticmethod
    def get_quiet_hour_keyboard(boundary: str, language: str = "uk") -> InlineKeyboardMarkup:
        buttons = [
            InlineKeyboardButton(text=f"{hour:02d}:00", callback_data=f"set_quiet_hour:{boundary}:{hour}")
            for hour in range(24)
        ]
        rows = [buttons[index : index + 4] for index in range(0, len(buttons), 4)]
        rows.append(
            [
                InlineKeyboardButton(
                    text=get_msg("calendar.back", language=language), callback_data="settings_quiet_hours"
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def get_schedule_nav_keyboard(
        offset: int, extra_buttons: list | None = None, language: str = "uk"
    ) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(text="⬅️", callback_data=f"nav_schedule:{offset - 1}"),
                InlineKeyboardButton(
                    text=get_msg("keyboard.refresh", language=language),
                    callback_data=f"nav_schedule:{offset}",
                ),
                InlineKeyboardButton(text="➡️", callback_data=f"nav_schedule:{offset + 1}"),
            ],
            [
                InlineKeyboardButton(
                    text=get_msg("keyboard.custom_date", language=language),
                    callback_data="ask_custom_date",
                )
            ],
            [
                InlineKeyboardButton(
                    text=get_msg("share.day_button", language=language),
                    callback_data=f"share_day:{offset}",
                )
            ],
        ]
        if extra_buttons:
            rows.extend(extra_buttons)
        rows.append(
            [InlineKeyboardButton(text=get_msg("keyboard.menu", language=language), callback_data="back_to_main")]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def get_week_nav_keyboard(
        offset: int, extra_buttons: list | None = None, language: str = "uk"
    ) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=get_msg("keyboard.previous_week", language=language),
                    callback_data=f"nav_week:{offset - 1}",
                ),
                InlineKeyboardButton(text="🔄", callback_data=f"nav_week:{offset}"),
                InlineKeyboardButton(
                    text=get_msg("keyboard.next_week", language=language),
                    callback_data=f"nav_week:{offset + 1}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=get_msg("keyboard.current_week", language=language), callback_data="nav_week:0"
                )
            ],
            [
                InlineKeyboardButton(
                    text=get_msg("keyboard.export_ics", language=language),
                    callback_data=f"export_ics:{offset}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=get_msg("share.week_button", language=language),
                    callback_data=f"share_week:{offset}",
                )
            ],
        ]
        if extra_buttons:
            rows.extend(extra_buttons)
        rows.append(
            [InlineKeyboardButton(text=get_msg("keyboard.menu", language=language), callback_data="back_to_main")]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def get_admin_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Огляд", callback_data="admin_stats")],
                [InlineKeyboardButton(text="👥 Користувачі", callback_data="admin_users")],
                [InlineKeyboardButton(text="🔔 Сповіщення", callback_data="admin_notifications")],
                [InlineKeyboardButton(text="🩺 Стан системи", callback_data="admin_system")],
                [InlineKeyboardButton(text="🗑 Видалити дані", callback_data="admin_delete_user")],
                [InlineKeyboardButton(text="🧪 Тестові дії", callback_data="admin_tests")],
                [InlineKeyboardButton(text="🔙 Закрити", callback_data="back_to_main")],
            ]
        )

    @staticmethod
    def get_admin_delete_user_keyboard(user_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 Підтвердити видалення",
                        callback_data=f"admin_confirm_delete:{user_id}",
                    )
                ],
                [InlineKeyboardButton(text="↩️ Скасувати", callback_data="admin_home")],
            ]
        )

    @staticmethod
    def get_admin_section_keyboard(refresh_callback: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Оновити", callback_data=refresh_callback)],
                [InlineKeyboardButton(text="🔙 Адмін-панель", callback_data="admin_home")],
            ]
        )

    @staticmethod
    def get_admin_tests_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🧪 Тест: Вечірній розклад", callback_data="admin_test_evening")],
                [InlineKeyboardButton(text="🧪 Тест: Перевірка змін", callback_data="admin_test_update")],
                [InlineKeyboardButton(text="🧪 Тест: Нагадування", callback_data="admin_test_reminder")],
                [InlineKeyboardButton(text="🧪 Dry-run: Переведення груп", callback_data="admin_test_promote")],
                [InlineKeyboardButton(text="🔙 Адмін-панель", callback_data="admin_home")],
            ]
        )
