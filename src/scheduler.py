from typing import LiteralString

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database as db
import scraper
import logging
import re
from datetime import datetime, timedelta
from messages import get_msg


def _get_dismiss_keyboard() -> InlineKeyboardMarkup:
    """Генерує клавіатуру з однією кнопкою для видалення повідомлення."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_msg("keyboard.dismiss", "✅ Прочитано"), callback_data="delete_msg")]
    ])


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
    users = await db.get_all_users()
    promoted_count = 0
    graduated_count = 0
    report = []

    group_counts = {}
    for u in users:
        g = u['group_name']
        if g:
            group_counts[g] = group_counts.get(g, 0) + 1

    unique_groups = set(group_counts.keys())
    group_mapping = {}

    pattern = re.compile(r"^([А-ЯІЇЄA-Zа-яіїєa-z]+-?)(\d)(.*)$")

    for group in unique_groups:
        match = pattern.match(group)
        if match:
            prefix = match.group(1)
            year = int(match.group(2))
            suffix = match.group(3)

            new_year = year + 1
            if new_year > 6:
                group_mapping[group] = "GRADUATED"
                continue

            new_group = f"{prefix}{new_year}{suffix}"

            exists = await scraper.check_group_exists(new_group)
            if exists:
                group_mapping[group] = new_group
            else:
                # Якщо нова група не знайдена (напр. бакалаври 4 курс -> 5 курс), вважаємо випускниками
                group_mapping[group] = "GRADUATED"

    if dry_run:
        for group, new_g in group_mapping.items():
            count = group_counts.get(group, 0)
            report.append(f"{group} -> {new_g} (користувачів: {count})")
        return "\n".join(report) if report else "Немає груп для переведення."

    for user in users:
        old_group = user['group_name']
        if old_group in group_mapping:
            new_group = group_mapping[old_group]

            if new_group == "GRADUATED":
                try:
                    await bot.send_message(
                        user['user_id'],
                        f"🎓 <b>Вітаємо із завершенням навчального етапу!</b>\n\n"
                        f"Група <b>{old_group}</b> більше не доступна на сайті розкладу.\n"
                        f"<i>Якщо ви продовжуєте навчання в іншій групі (наприклад, магістратурі), змініть групу в налаштуваннях бота.</i>",
                        parse_mode="HTML",
                        reply_markup=_get_dismiss_keyboard()
                    )
                    # Очищаємо групу, щоб не спамити помилками надалі
                    await db.add_or_update_user(user['user_id'], None)
                    graduated_count += 1
                except Exception:
                    pass
            else:
                await db.add_or_update_user(user['user_id'], new_group)
                try:
                    await bot.send_message(
                        user['user_id'],
                        f"🎓 <b>Вітаємо з новим навчальним роком!</b>\n\n"
                        f"Вашу групу було автоматично переведено з <b>{old_group}</b> на <b>{new_group}</b>.",
                        parse_mode="HTML",
                        reply_markup=_get_dismiss_keyboard()
                    )
                    promoted_count += 1
                except Exception:
                    pass

    logging.info(f"Переведення завершено. Оновлено: {promoted_count}, Випущено: {graduated_count}.")
    return None


async def promote_groups(bot: Bot):
    logging.info("Запуск автоматичного переведення груп на новий навчальний рік...")
    await process_promotion(bot, dry_run=False)


async def promote_groups_dry_run(bot: Bot) -> LiteralString | str | None:
    return await process_promotion(bot, dry_run=True)


async def send_evening_schedule(bot: Bot):
    """Відправляє розклад на завтра кожного вечора."""
    tomorrow = datetime.now() + timedelta(days=1)
    is_active_semester = await is_active_study_period(tomorrow)
    is_weekend = tomorrow.weekday() in [5, 6]

    users = await db.get_active_users()
    groups = {}

    for user in users:
        g = user['group_name']
        if g and user['notify_evening']:
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

        text = get_msg("schedule.evening_title", "🌙 <b>Розклад на завтра:</b>") + "\n"
        has_pdf = False
        for item in schedule:
            if item.get('is_pdf'):
                if not has_pdf:
                    text += "\n" + f"<s>{'—' * 25}</s>" + "\n\n"
                    has_pdf = True
                text += f"📄 <a href='{item['viewer_url']}'>{item['name']}</a>\n"
            else:
                text += f"⏰ <b>{item['time']}</b> - {item['name']}\n"

        for user in group_users:
            try:
                await bot.send_message(
                    user['user_id'],
                    text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=_get_dismiss_keyboard()
                )
            except Exception as e:
                logging.error(f"Не вдалося відправити повідомлення користувачу {user['user_id']}: {e}")


async def send_class_reminder(bot: Bot, user_id: int, subject_name: str, scheduled_group: str, offset: int):
    """Відправляє нагадування про конкретну пару."""
    user = await db.get_user(user_id)

    if not user or user['is_paused'] or not user['notify_10_min'] or user['group_name'] != scheduled_group:
        return

    time_str = f"{offset // 60} год" + (f" {offset % 60} хв" if offset % 60 else "") if offset >= 60 else f"{offset} хв"
    text = get_msg("reminders.class_starts", "⏳ За {time_str} почнеться пара:\n<b>{subject_name}</b>",
                   time_str=time_str, subject_name=subject_name)

    try:
        await bot.send_message(
            user_id,
            text,
            parse_mode="HTML",
            reply_markup=_get_dismiss_keyboard()
        )
    except Exception as e:
        logging.error(f"Помилка відправки нагадування: {e}")


async def schedule_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler):
    users = await db.get_active_users()
    tasks = {}

    for user in users:
        g = user['group_name']
        if g and user['notify_10_min']:
            offset = user.get('reminder_offset', 10)
            key = (g, offset)
            if key not in tasks:
                tasks[key] = []
            tasks[key].append(user['user_id'])

    for (group_name, offset), user_ids in tasks.items():
        schedule = await scraper.parse_schedule_for_today(group_name)

        for item in schedule:
            if item.get('is_pdf', False): continue
            time_parts = item['time'].split('-')[0].split(':')
            try:
                now = datetime.now()
                class_time = now.replace(hour=int(time_parts[0]), minute=int(time_parts[1]), second=0)
                reminder_time = class_time - timedelta(minutes=offset)

                if reminder_time > now:
                    for uid in user_ids:
                        scheduler.add_job(
                            send_class_reminder,
                            'date',
                            run_date=reminder_time,
                            args=[bot, uid, item['name'], group_name, offset]
                        )
            except Exception as e:
                logging.error(f"Помилка створення задачі: {e}")


async def check_schedule_updates_task(bot: Bot):
    """Перевіряє, чи не змінився розклад на сайті для кожної групи."""
    users = await db.get_active_users()
    groups = {}
    for user in users:
        g = user['group_name']
        if g and user['notify_schedule_update']:
            if g not in groups:
                groups[g] = []
            groups[g].append(user)

    for group_name, group_users in groups.items():
        has_changes = await scraper.check_schedule_changes(group_name)
        if has_changes:
            for user in group_users:
                try:
                    await bot.send_message(
                        user['user_id'],
                        get_msg("schedule.changed", "⚠️ <b>Увага!</b> Розклад для вашої групи був змінений на сайті ТНТУ!"),
                        parse_mode="HTML",
                        reply_markup=_get_dismiss_keyboard()
                    )
                except:
                    pass


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
    # Щоденне нагадування на завтра (20:00)
    scheduler.add_job(send_evening_schedule, 'cron', hour=20, minute=0, args=[bot])
    # Нагадування за 10 хвилин до кожної пари (створюється щодня о 6:00 для поточного дня)
    scheduler.add_job(schedule_daily_reminders, 'cron', hour=6, minute=0, args=[bot, scheduler])
    # Перевірка на оновлення розкладу кожні 2 години
    scheduler.add_job(check_schedule_updates_task, 'interval', hours=2, args=[bot])
    # Переведення на наступний курс (1 серпня о 12:00)
    scheduler.add_job(promote_groups, 'cron', month=8, day=1, hour=12, minute=0, args=[bot])
    return scheduler