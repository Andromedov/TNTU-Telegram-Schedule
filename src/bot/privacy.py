"""Privacy-related cleanup kept behind one auditable boundary."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.common import ics_cooldown
from infrastructure import database as db
from jobs.scheduler import remove_user_jobs


async def erase_user_data(user_id: int, scheduler: AsyncIOScheduler | None) -> bool:
    """Erase persisted and temporary per-user data managed by this application."""
    deleted = await db.delete_user_data(user_id)
    ics_cooldown.pop(user_id, None)
    remove_user_jobs(scheduler, user_id)
    return deleted
