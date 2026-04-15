from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database as db
import scraper
import logging
from datetime import datetime, timedelta
from messages import get_msg


def _get_dismiss_keyboard() -> InlineKeyboardMarkup:
    """Генерує клавіатуру з однією кнопкою для видалення повідомлення."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_msg("keyboard.dismiss", "✅ Прочитано"), callback_data="delete_msg")]
    ])


async def send_evening_schedule(bot: Bot):
    """Відправляє розклад на завтра кожного вечора."""
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

        if schedule:
            text = get_msg("schedule.evening_title", "🌙 <b>Розклад на завтра:</b>") + "\n"
            has_pdf = False
            for item in schedule:
                if item.get('is_pdf'):
                    if not has_pdf:
                        text += "\n" + f"<s>{'—' * 25}</s>" + "\n\n"
                        has_pdf = True
                    text += f"📄 {item['name']}\n"
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
    scheduler.add_job(send_evening_schedule, 'cron', hour=20, minute=0, args=[bot])
    scheduler.add_job(schedule_daily_reminders, 'cron', hour=6, minute=0, args=[bot, scheduler])
    scheduler.add_job(check_schedule_updates_task, 'interval', hours=2, args=[bot])
    return scheduler