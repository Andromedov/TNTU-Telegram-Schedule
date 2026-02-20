from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
import database as db
import scraper
import logging
from datetime import datetime, timedelta
from messages import get_msg


async def send_evening_schedule(bot: Bot):
    """Відправляє розклад на завтра кожного вечора."""
    users = await db.get_active_users()
    for user in users:
        if user['notify_evening'] and user['group_name']:
            schedule = await scraper.parse_schedule_for_tomorrow(user['group_name'])
            if schedule:
                text = get_msg("evening_schedule_title")
                for item in schedule:
                    text += f"⏰ {item['time']} - {item['name']}\n"
                try:
                    await bot.send_message(user['user_id'], text)
                except Exception as e:
                    logging.error(f"Не вдалося відправити повідомлення користувачу {user['user_id']}: {e}")


async def send_10_min_reminder(bot: Bot, user_id: int, subject_name: str):
    """Відправляє нагадування про конкретну пару."""
    try:
        await bot.send_message(user_id, get_msg("reminder_10_min", subject_name=subject_name))
    except Exception as e:
        logging.error(f"Помилка відправки нагадування: {e}")


async def schedule_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler):
    """
    Ця функція повинна запускатись рано вранці (наприклад, о 06:00).
    Вона парсить розклад на сьогодні для всіх груп і створює
    одноразові задачі в APScheduler на (час_пари - 10 хв).
    """
    users = await db.get_active_users()
    for user in users:
        if user['notify_10_min'] and user['group_name']:
            # УВАГА: Тут має бути парсинг розкладу НА СЬОГОДНІ
            schedule = await scraper.parse_schedule_for_tomorrow(user['group_name'])

            for item in schedule:
                # Перетворюємо час (наприклад "08:00") у об'єкт datetime на сьогодні
                time_parts = item['time'].split(':')
                now = datetime.now()
                class_time = now.replace(hour=int(time_parts[0]), minute=int(time_parts[1]), second=0)

                reminder_time = class_time - timedelta(minutes=10)

                # Якщо час нагадування ще не пройшов
                if reminder_time > now:
                    scheduler.add_job(
                        send_10_min_reminder,
                        'date',
                        run_date=reminder_time,
                        args=[bot, user['user_id'], item['name']]
                    )


async def check_schedule_updates_task(bot: Bot):
    """Перевіряє, чи не змінився розклад на сайті (наприклад, щогодини)."""
    has_changes = await scraper.check_schedule_changes()
    if has_changes:
        users = await db.get_active_users()
        for user in users:
            try:
                await bot.send_message(user['user_id'], get_msg("schedule_changed"))
            except:
                pass


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")

    scheduler.add_job(send_evening_schedule, 'cron', hour=20, minute=0, args=[bot])

    scheduler.add_job(schedule_daily_reminders, 'cron', hour=6, minute=0, args=[bot, scheduler])

    scheduler.add_job(check_schedule_updates_task, 'interval', hours=2, args=[bot])

    return scheduler