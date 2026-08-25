"""APScheduler configuration and public background-job API."""

# Re-export job functions from one scheduling boundary.
import asyncio

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from infrastructure import database as db
from jobs.formatting import (
    format_reminder_offset as _format_reminder_offset,
    format_schedule_changes as _format_schedule_changes,
    get_dismiss_keyboard as _get_dismiss_keyboard,
    get_reminder_keyboard as _get_reminder_keyboard,
    reminder_job_id as _reminder_job_id,
)
from jobs.notifications import (
    check_schedule_updates_task,
    is_active_study_period,
    schedule_daily_reminders as _schedule_daily_reminders,
    send_class_reminder,
    send_evening_schedule,
    send_morning_digest,
    send_snoozed_reminder,
)
from jobs.promotion import (
    GROUP_CHECK_CONCURRENCY,
    GROUP_CHECK_FAILED,
    next_group_candidate as _next_group_candidate,
    process_promotion,
    promote_groups,
    promote_groups_dry_run,
)
from jobs.reminders import kyiv_now
from schedule import service as scraper


async def schedule_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler):
    """Schedule lesson reminders while preserving the facade's clock hook."""
    return await _schedule_daily_reminders(bot, scheduler, now_provider=kyiv_now)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
    scheduler.add_job(send_evening_schedule, "cron", hour=20, minute=0, args=[bot])
    scheduler.add_job(schedule_daily_reminders, "cron", hour=6, minute=0, args=[bot, scheduler])
    for digest_hour in (6, 7, 8, 9):
        scheduler.add_job(send_morning_digest, "cron", hour=digest_hour, minute=0, args=[bot, digest_hour])
    scheduler.add_job(check_schedule_updates_task, "interval", hours=2, args=[bot])
    scheduler.add_job(promote_groups, "cron", month=8, day=1, hour=12, minute=0, args=[bot])
    return scheduler
