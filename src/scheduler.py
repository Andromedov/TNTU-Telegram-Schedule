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


async def promote_groups(bot: Bot):
    """
    Запускається щорічно 1-го серпня.
    Автоматично переводить студентів на наступний курс (напр. СТс-21 -> СТс-31).
    """
    logging.info("Запуск автоматичного переведення груп на новий навчальний рік...")
    users = await db.get_all_users()
    promoted_count = 0

    # Знаходимо унікальні групи, щоб не перевіряти одну й ту ж через scraper сотні разів
    unique_groups = {u['group_name'] for u in users if u['group_name']}
    group_mapping = {}

    # Регулярка для пошуку: префікс з літер (СТс-), цифра курсу (2), решта (1)
    pattern = re.compile(r"^([А-ЯІЇЄA-Zа-яіїєa-z]+-)(\d)(\d*)$")

    for group in unique_groups:
        match = pattern.match(group)
        if match:
            prefix = match.group(1)
            year = int(match.group(2))
            suffix = match.group(3)

            new_year = year + 1
            if new_year > 6:  # Зазвичай не більше 5-6 курсів (Магістратура)
                continue

            new_group = f"{prefix}{new_year}{suffix}"

            # Перевіряємо, чи вже завантажили цю нову групу на сайт ТНТУ
            exists = await scraper.check_group_exists(new_group)
            if exists:
                group_mapping[group] = new_group

    # Оновлюємо базу даних та розсилаємо привітання
    for user in users:
        old_group = user['group_name']
        if old_group in group_mapping:
            new_group = group_mapping[old_group]
            await db.add_or_update_user(user['user_id'], new_group)

            try:
                await bot.send_message(
                    user['user_id'],
                    f"🎓 <b>Вітаємо з новим навчальним роком!</b>\n\n"
                    f"Вашу групу було автоматично переведено з <b>{old_group}</b> на <b>{new_group}</b>.\n"
                    f"<i>Якщо ви завершили навчання або перейшли в іншу групу, ви можете змінити її в налаштуваннях.</i>",
                    parse_mode="HTML",
                    reply_markup=_get_dismiss_keyboard()
                )
                promoted_count += 1
            except Exception as e:
                logging.error(f"Не вдалося відправити повідомлення про переведення {user['user_id']}: {e}")

    logging.info(f"Переведення завершено. Оновлено {promoted_count} студентів.")


async def send_evening_schedule(bot: Bot):
    """Відправляє розклад на завтра кожного вечора."""
    tomorrow = datetime.now() + timedelta(days=1)
    is_active_semester = is_active_study_period(tomorrow)
    is_weekend = tomorrow.weekday() in [5, 6]  # Субота та Неділя

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
            if is_weekend:
                continue
            if not is_active_semester:
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


async def send_10_min_reminder(bot: Bot, user_id: int, subject_name: str, scheduled_group: str):
    """Відправляє нагадування про конкретну пару."""
    user = await db.get_user(user_id)

    if not user or user['is_paused'] or not user['notify_10_min'] or user['group_name'] != scheduled_group:
        return

    try:
        await bot.send_message(
            user_id,
            get_msg("reminders.10_min", "⏳ За 10 хвилин почнеться пара:\n<b>{subject_name}</b>", subject_name=subject_name),
            parse_mode="HTML",
            reply_markup=_get_dismiss_keyboard()
        )
    except Exception as e:
        logging.error(f"Помилка відправки нагадування: {e}")


async def schedule_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler):
    users = await db.get_active_users()
    groups = {}

    for user in users:
        g = user['group_name']
        if g and user['notify_10_min']:
            if g not in groups:
                groups[g] = []
            groups[g].append(user['user_id'])

    for group_name, user_ids in groups.items():
        schedule = await scraper.parse_schedule_for_today(group_name)

        for item in schedule:
            if item.get('is_pdf', False):
                continue

            time_parts = item['time'].split('-')[0].split(':')
            try:
                now = datetime.now()
                class_time = now.replace(hour=int(time_parts[0]), minute=int(time_parts[1]), second=0)
                reminder_time = class_time - timedelta(minutes=10)

                if reminder_time > now:
                    for uid in user_ids:
                        scheduler.add_job(
                            send_10_min_reminder,
                            'date',
                            run_date=reminder_time,
                            args=[bot, uid, item['name'], group_name]
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