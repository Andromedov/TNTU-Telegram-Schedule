import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from infrastructure import database as db
from schedule import service as scraper
from jobs.formatting import (
    format_reminder_offset,
    format_schedule_changes,
    get_dismiss_keyboard,
    get_reminder_keyboard,
    reminder_job_id,
)
from i18n.messages import get_html_msg, get_msg, normalize_language, trusted_html
from jobs.reminders import kyiv_now, notifications_are_muted, reminder_enabled_for_lesson
from schedule.formatting import html_link, lesson_html


async def is_active_study_period(target_date: datetime) -> bool:
    semester_dates = await scraper.get_semester_dates()
    if semester_dates:
        start_date, end_date = semester_dates
        return start_date.date() <= target_date.date() <= end_date.date()
    month, day = target_date.month, target_date.day
    if (month == 6 and day > 14) or month in (7, 8):
        return False
    if month == 1 or (month == 2 and day < 9):
        return False
    return True


async def send_evening_schedule(bot: Bot):
    tomorrow = datetime.now() + timedelta(days=1)
    active_semester = await is_active_study_period(tomorrow)
    is_weekend = tomorrow.weekday() in (5, 6)
    groups = {}
    for user in await db.get_active_users():
        user_dict = dict(user)
        group = user_dict["group_name"]
        if group and user_dict["notify_evening"] and not notifications_are_muted(user_dict):
            groups.setdefault(group, []).append(user)

    for group, users in groups.items():
        schedule = await scraper.parse_schedule_for_tomorrow(group)
        if not schedule:
            continue
        if not any(not item.get("is_pdf") for item in schedule) and (is_weekend or not active_semester):
            continue
        for user in users:
            language = normalize_language(dict(user).get("language"))
            text = get_msg("schedule.evening_title", language=language) + "\n"
            has_pdf = False
            for item in schedule:
                if item.get("is_pdf"):
                    if not has_pdf:
                        text += "\n" + f"<s>{'—' * 25}</s>\n\n"
                        has_pdf = True
                    text += f"📄 {html_link(item['name'], item.get('viewer_url'))}\n"
                else:
                    text += f"⏰ <b>{escape(str(item['time']))}</b> - {lesson_html(item)}\n"
            try:
                await bot.send_message(user["user_id"], text, parse_mode="HTML",
                                       disable_web_page_preview=True,
                                       reply_markup=get_dismiss_keyboard(language))
            except Exception as error:
                logging.error("Не вдалося відправити повідомлення користувачу %s: %s", user["user_id"], error)


async def send_class_reminder(bot: Bot, user_id: int, lesson: dict | str,
                              scheduled_group: str, offset: int):
    user = await db.get_user(user_id)
    if not user:
        return
    user_dict = dict(user)
    if (notifications_are_muted(user_dict) or not user_dict["notify_10_min"]
            or user_dict["group_name"] != scheduled_group
            or not reminder_enabled_for_lesson(user_dict, lesson)):
        return
    language = normalize_language(user_dict.get("language"))
    subject = lesson_html(lesson) if isinstance(lesson, dict) else escape(lesson)
    text = get_html_msg("reminders.class_starts", language=language,
                        time_str=format_reminder_offset(offset, language),
                        subject_name=trusted_html(subject))
    try:
        await bot.send_message(user_id, text, parse_mode="HTML", disable_web_page_preview=True,
                               reply_markup=get_reminder_keyboard(language))
    except Exception as error:
        logging.error("Помилка відправки нагадування: %s", error)


async def schedule_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler, now_provider=kyiv_now):
    groups = {}
    for user in await db.get_active_users():
        user_dict = dict(user)
        group = user_dict["group_name"]
        if group and user_dict.get("notify_10_min", 1):
            groups.setdefault(group, []).append(user_dict)

    for group, users in groups.items():
        schedule = await scraper.parse_schedule_for_today(group)
        classes = [item for item in schedule if not item.get("is_pdf", False)]
        for class_index, item in enumerate(classes):
            time_parts = item["time"].split("-")[0].split(":")
            try:
                now = now_provider()
                class_time = now.replace(hour=int(time_parts[0]), minute=int(time_parts[1]),
                                         second=0, microsecond=0)
                for user in users:
                    if not reminder_enabled_for_lesson(user, item):
                        continue
                    first_offset = user.get("first_class_reminder_offset")
                    offset = (int(first_offset) if class_index == 0 and first_offset is not None
                              else int(user.get("reminder_offset", 10)))
                    reminder_time = class_time - timedelta(minutes=offset)
                    if reminder_time > now:
                        user_id = user["user_id"]
                        scheduler.add_job(
                            send_class_reminder, "date", run_date=reminder_time,
                            args=[bot, user_id, item, group, offset],
                            id=reminder_job_id(user_id, group, class_time, item["name"]),
                            replace_existing=True,
                        )
            except Exception as error:
                logging.error("Помилка створення задачі: %s", error)


async def send_snoozed_reminder(bot: Bot, user_id: int, html_text: str):
    user = await db.get_user(user_id)
    if not user or notifications_are_muted(dict(user)) or not user["notify_10_min"]:
        return
    language = normalize_language(dict(user).get("language"))
    try:
        await bot.send_message(user_id, html_text, parse_mode="HTML",
                               reply_markup=get_reminder_keyboard(language))
    except Exception as error:
        logging.error("Помилка повторного нагадування користувачу %s: %s", user_id, error)


async def send_morning_digest(bot: Bot, digest_hour: int):
    groups = {}
    for user in await db.get_active_users():
        user_dict = dict(user)
        if (user_dict.get("group_name") and user_dict.get("morning_digest")
                and int(user_dict.get("morning_digest_hour") or 7) == digest_hour
                and not notifications_are_muted(user_dict)):
            groups.setdefault(user_dict["group_name"], []).append(user_dict)
    for group, users in groups.items():
        classes = [item for item in await scraper.parse_schedule_for_today(group)
                   if not item.get("is_pdf", False)]
        for user in users:
            language = normalize_language(user.get("language"))
            text = get_html_msg("reminders.digest_title", language=language, group=group) + "\n"
            if classes:
                for item in classes:
                    text += f"⏰ <b>{escape(str(item['time']))}</b> — {lesson_html(item)}\n"
            else:
                text += get_msg("reminders.digest_empty", language=language)
            try:
                await bot.send_message(user["user_id"], text, parse_mode="HTML",
                                       disable_web_page_preview=True,
                                       reply_markup=get_dismiss_keyboard(language))
            except Exception as error:
                logging.error("Помилка ранкового дайджесту для %s: %s", user["user_id"], error)


async def check_schedule_updates_task(bot: Bot):
    groups = {}
    for user in await db.get_active_users():
        user_dict = dict(user)
        group = user_dict["group_name"]
        if group and user_dict["notify_schedule_update"] and not notifications_are_muted(user_dict):
            groups.setdefault(group, []).append(user)
    for group, users in groups.items():
        changes = await scraper.get_schedule_changes(group)
        if not changes:
            continue
        for user in users:
            language = normalize_language(dict(user).get("language"))
            try:
                await bot.send_message(user["user_id"], format_schedule_changes(changes, language),
                                       parse_mode="HTML", reply_markup=get_dismiss_keyboard(language))
            except Exception as error:
                logging.warning("Не вдалося повідомити %s про зміну розкладу: %s",
                                user["user_id"], error)
