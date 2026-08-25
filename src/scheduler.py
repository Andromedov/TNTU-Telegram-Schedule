import asyncio
import hashlib
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database as db
import scraper
import logging
import re
from html import escape
from datetime import datetime, timedelta
from messages import get_msg, normalize_language
from reminder_utils import (
    kyiv_now,
    notifications_are_muted,
    reminder_enabled_for_lesson,
)
from schedule_formatting import lesson_html


GROUP_CHECK_CONCURRENCY = 8
GROUP_CHECK_FAILED = "CHECK_FAILED"


def _next_group_candidate(group_name: str) -> str | None:
    """Збільшує цифру курсу, зберігаючи номер підгрупи: СТс-21 -> СТс-31."""
    match = re.fullmatch(r"([А-ЯІЇЄA-Zа-яіїєa-z]+-?)(\d{1,2})(.*)", group_name)
    if not match:
        return None

    prefix, number, suffix = match.groups()
    course = int(number[0])
    if course < 1 or course >= 6:
        return "GRADUATED"

    next_number = f"{course + 1}{number[1:]}"
    return f"{prefix}{next_number}{suffix}"


def _get_dismiss_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
    """Генерує клавіатуру з однією кнопкою для видалення повідомлення."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_msg("keyboard.dismiss", language=language), callback_data="delete_msg")]
    ])


def _get_reminder_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=get_msg("reminders.snooze_button", language=language),
            callback_data="snooze_reminder",
        )],
        [InlineKeyboardButton(
            text=get_msg("reminders.mute_today_button", language=language),
            callback_data="reminder_mute_today",
        )],
        [InlineKeyboardButton(
            text=get_msg("keyboard.dismiss", language=language),
            callback_data="delete_msg",
        )],
    ])


def _format_reminder_offset(offset: int, language: str) -> str:
    if offset >= 60:
        hours = offset // 60
        minutes = offset % 60
        if minutes:
            return get_msg("reminders.hours_minutes", language=language, hours=hours, minutes=minutes)
        return get_msg("reminders.hours", language=language, value=hours)
    return get_msg("reminders.minutes", language=language, value=offset)


def _reminder_job_id(user_id: int, group_name: str, class_time: datetime, subject_name: str) -> str:
    subject_hash = hashlib.sha256(subject_name.encode("utf-8")).hexdigest()[:12]
    return f"class-reminder:{user_id}:{group_name}:{class_time.strftime('%Y%m%d%H%M')}:{subject_hash}"


def _format_schedule_changes(changes: list, language: str) -> str:
    weekdays = get_msg("schedule.weekdays", language=language).split("|")
    lines = [get_msg("schedule.changed", language=language), ""]
    truncated = False

    for change in changes:
        lesson = change['lesson']
        weekday_index = int(lesson.get('weekday', 0))
        weekday = weekdays[weekday_index] if 0 <= weekday_index < len(weekdays) else str(weekday_index + 1)
        context = get_msg(
            "schedule.change_context",
            language=language,
            weekday=weekday,
            week=lesson.get('week', '?'),
            time=lesson.get('time', '?'),
        )
        subject = lesson_html(lesson)
        key = {
            'added': 'schedule.change_added',
            'removed': 'schedule.change_removed',
            'changed': 'schedule.change_updated',
        }.get(change.get('kind'), 'schedule.change_updated')
        block = [get_msg(key, language=language, subject=subject), f"<i>{escape(context)}</i>"]

        for field, values in change.get('fields', {}).items():
            label = get_msg(f"schedule.change_fields.{field}", default=field, language=language)
            empty = get_msg("schedule.change_none", language=language)
            old_value = escape(str(values.get('old') or empty))
            new_value = escape(str(values.get('new') or empty))
            block.append("  • " + get_msg(
                "schedule.change_value",
                language=language,
                field=escape(label),
                old=old_value,
                new=new_value,
            ))

        candidate = "\n".join([*lines, *block, ""])
        if len(candidate) > 3800:
            truncated = True
            break
        lines.extend([*block, ""])

    if truncated:
        lines.append(f"<i>{escape(get_msg('schedule.change_truncated', language=language))}</i>")
    return "\n".join(lines).rstrip()


async def is_active_study_period(target_date: datetime) -> bool:
    """Перевіряє, чи припадає дата на період активного навчання."""
    semester_dates = await scraper.get_semester_dates()

    if semester_dates:
        start_date, end_date = semester_dates
        if start_date.date() <= target_date.date() <= end_date.date():
            return True
        else:
            return False

    # Fallback: провсяк випадок :)
    month = target_date.month
    day = target_date.day

    if (month == 6 and day > 14) or month == 7 or month == 8:
        return False

    if month == 1 or (month == 2 and day < 9):
        return False

    return True


async def process_promotion(bot: Bot, dry_run: bool = False):
    """Ядро логіки переведення студентів. dry_run=True лише повертає звіт."""
    promoted_count = 0
    graduated_count = 0
    report = []

    group_counts = {}

    # Крок 1: Збираємо всі групи та кількість користувачів батчами
    limit = 500
    offset = 0
    while True:
        users_batch = await db.get_users_batch(limit, offset)
        if not users_batch:
            break
        for u in users_batch:
            g = u['group_name']
            if g:
                group_counts[g] = group_counts.get(g, 0) + 1
        offset += limit

    unique_groups = set(group_counts.keys())
    group_mapping = {}
    candidates = {}

    for group in unique_groups:
        candidate = _next_group_candidate(group)
        if candidate == "GRADUATED":
            group_mapping[group] = "GRADUATED"
        elif candidate:
            candidates[group] = candidate

    semaphore = asyncio.Semaphore(GROUP_CHECK_CONCURRENCY)

    async def check_group(g_name: str) -> bool | None:
        async with semaphore:
            for attempt in range(3):
                result = await scraper.check_group_exists_status(g_name)
                if result is not None:
                    return result
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
            return None

    async def find_valid_group(g_name: str) -> str | None:
        """Перевіряє, чи існує група, якщо ні — пробує підігнати регістр (СТС-31 -> СТс-31)."""
        exists = await check_group(g_name)
        if exists is True:
            return g_name
        if exists is None:
            return GROUP_CHECK_FAILED

        if "-" in g_name:
            parts = g_name.split("-")
            if len(parts[0]) > 1 and parts[0][-1].isalpha():
                alt_prefix = parts[0][:-1] + parts[0][-1].lower()
                alt_g = f"{alt_prefix}-{'-'.join(parts[1:])}"
                alt_exists = await check_group(alt_g)
                if alt_exists is True:
                    return alt_g
                if alt_exists is None:
                    return GROUP_CHECK_FAILED
        return None

    if candidates:
        exist_results = await asyncio.gather(
            *[find_valid_group(new_g) for new_g in candidates.values()]
        )
        for old_g, valid_new_g in zip(candidates.keys(), exist_results):
            group_mapping[old_g] = valid_new_g if valid_new_g else "GRADUATED"

    if dry_run:
        for group, new_g in group_mapping.items():
            count = group_counts.get(group, 0)
            report.append(f"{group} -> {new_g} (користувачів: {count})")
        return "\n".join(report) if report else "Немає груп для переведення."

    offset = 0
    while True:
        users_batch = await db.get_users_batch(limit, offset)
        if not users_batch:
            break

        for user in users_batch:
            old_group = user['group_name']
            if old_group in group_mapping:
                new_group = group_mapping[old_group]

                if new_group == GROUP_CHECK_FAILED:
                    logging.warning(f"Пропущено переведення групи {old_group}: не вдалося перевірити сайт")
                    continue
                if new_group == "GRADUATED":
                    await db.clear_user_group(user['user_id'])
                    graduated_count += 1
                    language = normalize_language(dict(user).get("language"))
                    try:
                        await bot.send_message(
                            user['user_id'],
                            get_msg("promotion.graduated", language=language, group=old_group),
                            parse_mode="HTML",
                            reply_markup=_get_dismiss_keyboard(language)
                        )
                    except Exception as e:
                        logging.warning(f"Не вдалося повідомити випускника {user['user_id']}: {e}")
                else:
                    await db.add_or_update_user(user['user_id'], new_group)
                    language = normalize_language(dict(user).get("language"))
                    try:
                        await bot.send_message(
                            user['user_id'],
                            get_msg("promotion.promoted", language=language, old_group=old_group, new_group=new_group),
                            parse_mode="HTML",
                            reply_markup=_get_dismiss_keyboard(language)
                        )
                        promoted_count += 1
                    except Exception:
                        pass

        offset += limit

    logging.info(f"Переведення завершено. Оновлено: {promoted_count}, Випущено: {graduated_count}.")
    return None


async def promote_groups(bot: Bot):
    logging.info("Запуск автоматичного переведення груп на новий навчальний рік...")
    await process_promotion(bot, dry_run=False)


async def promote_groups_dry_run(bot: Bot) -> str | None:
    return await process_promotion(bot, dry_run=True)


async def send_evening_schedule(bot: Bot):
    """Відправляє розклад на завтра кожного вечора."""
    tomorrow = datetime.now() + timedelta(days=1)
    is_active_semester = await is_active_study_period(tomorrow)
    is_weekend = tomorrow.weekday() in [5, 6]

    users = await db.get_active_users()
    groups = {}

    for user in users:
        user_dict = dict(user)
        g = user_dict['group_name']
        if g and user_dict['notify_evening'] and not notifications_are_muted(user_dict):
            if g not in groups:
                groups[g] = []
            groups[g].append(user)

    for group_name, group_users in groups.items():
        schedule = await scraper.parse_schedule_for_tomorrow(group_name)

        if not schedule:
            continue

        has_actual_classes = any(not item.get('is_pdf') for item in schedule)

        if not has_actual_classes:
            if is_weekend or not is_active_semester:
                continue

        for user in group_users:
            language = normalize_language(dict(user).get("language"))
            text = get_msg("schedule.evening_title", language=language) + "\n"
            has_pdf = False
            for item in schedule:
                if item.get('is_pdf'):
                    if not has_pdf:
                        text += "\n" + f"<s>{'—' * 25}</s>" + "\n\n"
                        has_pdf = True
                    text += f"📄 <a href='{item['viewer_url']}'>{item['name']}</a>\n"
                else:
                    text += f"⏰ <b>{item['time']}</b> - {lesson_html(item)}\n"
            try:
                await bot.send_message(
                    user['user_id'],
                    text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=_get_dismiss_keyboard(language)
                )
            except Exception as e:
                logging.error(f"Не вдалося відправити повідомлення користувачу {user['user_id']}: {e}")


async def send_class_reminder(bot: Bot, user_id: int, lesson: dict | str, scheduled_group: str, offset: int):
    """Відправляє нагадування про конкретну пару."""
    user = await db.get_user(user_id)

    if not user:
        return
    user_dict = dict(user)
    if (notifications_are_muted(user_dict) or not user_dict['notify_10_min']
            or user_dict['group_name'] != scheduled_group
            or not reminder_enabled_for_lesson(user_dict, lesson)):
        return

    language = normalize_language(user_dict.get("language"))
    time_str = _format_reminder_offset(offset, language)

    subject_html = lesson_html(lesson) if isinstance(lesson, dict) else escape(lesson)
    text = get_msg("reminders.class_starts", language=language,
                   time_str=time_str, subject_name=subject_html)

    try:
        await bot.send_message(
            user_id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=_get_reminder_keyboard(language)
        )
    except Exception as e:
        logging.error(f"Помилка відправки нагадування: {e}")


async def schedule_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler):
    users = await db.get_active_users()
    groups = {}

    for user in users:
        user_dict = dict(user)
        g = user_dict['group_name']
        # Тимчасово muted користувачів теж плануємо: вони можуть увімкнути
        # сповіщення пізніше цього дня. Стан повторно перевіряється перед відправкою.
        if g and user_dict.get('notify_10_min', 1):
            groups.setdefault(g, []).append(user_dict)

    for group_name, group_users in groups.items():
        schedule = await scraper.parse_schedule_for_today(group_name)
        actual_classes = [item for item in schedule if not item.get('is_pdf', False)]

        for class_index, item in enumerate(actual_classes):
            time_parts = item['time'].split('-')[0].split(':')
            try:
                now = kyiv_now()
                class_time = now.replace(
                    hour=int(time_parts[0]), minute=int(time_parts[1]), second=0, microsecond=0
                )
                for user in group_users:
                    if not reminder_enabled_for_lesson(user, item):
                        continue
                    first_offset = user.get('first_class_reminder_offset')
                    offset = int(first_offset) if class_index == 0 and first_offset is not None \
                        else int(user.get('reminder_offset', 10))
                    reminder_time = class_time - timedelta(minutes=offset)
                    if reminder_time > now:
                        uid = user['user_id']
                        scheduler.add_job(
                            send_class_reminder,
                            'date',
                            run_date=reminder_time,
                            args=[bot, uid, item, group_name, offset],
                            id=_reminder_job_id(uid, group_name, class_time, item['name']),
                            replace_existing=True,
                        )
            except Exception as e:
                logging.error(f"Помилка створення задачі: {e}")


async def send_snoozed_reminder(bot: Bot, user_id: int, html_text: str):
    """Повторно надсилає нагадування, якщо сповіщення досі активні."""
    user = await db.get_user(user_id)
    if not user or notifications_are_muted(dict(user)) or not user['notify_10_min']:
        return
    language = normalize_language(dict(user).get("language"))
    try:
        await bot.send_message(
            user_id,
            html_text,
            parse_mode="HTML",
            reply_markup=_get_reminder_keyboard(language),
        )
    except Exception as e:
        logging.error(f"Помилка повторного нагадування користувачу {user_id}: {e}")


async def send_morning_digest(bot: Bot, digest_hour: int):
    """Надсилає ранковий розклад користувачам із вибраною годиною дайджесту."""
    users = await db.get_active_users()
    groups = {}
    for user in users:
        user_dict = dict(user)
        if (user_dict.get('group_name') and user_dict.get('morning_digest')
                and int(user_dict.get('morning_digest_hour') or 7) == digest_hour
                and not notifications_are_muted(user_dict)):
            groups.setdefault(user_dict['group_name'], []).append(user_dict)

    for group_name, group_users in groups.items():
        schedule = await scraper.parse_schedule_for_today(group_name)
        classes = [item for item in schedule if not item.get('is_pdf', False)]
        for user in group_users:
            language = normalize_language(user.get("language"))
            text = get_msg("reminders.digest_title", language=language, group=escape(group_name)) + "\n"
            if classes:
                for item in classes:
                    text += f"⏰ <b>{escape(str(item['time']))}</b> — {lesson_html(item)}\n"
            else:
                text += get_msg("reminders.digest_empty", language=language)
            try:
                await bot.send_message(
                    user['user_id'],
                    text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=_get_dismiss_keyboard(language),
                )
            except Exception as e:
                logging.error(f"Помилка ранкового дайджесту для {user['user_id']}: {e}")


async def check_schedule_updates_task(bot: Bot):
    """Перевіряє, чи не змінився розклад на сайті для кожної групи."""
    users = await db.get_active_users()
    groups = {}
    for user in users:
        user_dict = dict(user)
        g = user_dict['group_name']
        if g and user_dict['notify_schedule_update'] and not notifications_are_muted(user_dict):
            if g not in groups:
                groups[g] = []
            groups[g].append(user)

    for group_name, group_users in groups.items():
        changes = await scraper.get_schedule_changes(group_name)
        if changes:
            for user in group_users:
                language = normalize_language(dict(user).get("language"))
                try:
                    await bot.send_message(
                        user['user_id'],
                        _format_schedule_changes(changes, language),
                        parse_mode="HTML",
                        reply_markup=_get_dismiss_keyboard(language)
                    )
                except Exception as error:
                    logging.warning("Не вдалося повідомити %s про зміну розкладу: %s",
                                    user['user_id'], error)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
    # Щоденне нагадування на завтра (20:00)
    scheduler.add_job(send_evening_schedule, 'cron', hour=20, minute=0, args=[bot])
    # Нагадування за 10 хвилин до кожної пари (створюється щодня о 6:00 для поточного дня)
    scheduler.add_job(schedule_daily_reminders, 'cron', hour=6, minute=0, args=[bot, scheduler])
    # Ранковий дайджест у вибрану користувачем годину
    for digest_hour in (6, 7, 8, 9):
        scheduler.add_job(send_morning_digest, 'cron', hour=digest_hour, minute=0, args=[bot, digest_hour])
    # Перевірка на оновлення розкладу кожні 2 години
    scheduler.add_job(check_schedule_updates_task, 'interval', hours=2, args=[bot])
    # Переведення на наступний курс (1 серпня о 12:00)
    scheduler.add_job(promote_groups, 'cron', month=8, day=1, hour=12, minute=0, args=[bot])
    return scheduler
