from dataclasses import dataclass
from datetime import datetime, timezone

from apscheduler.events import JobExecutionEvent


@dataclass(frozen=True)
class JobRunStatus:
    job_id: str
    finished_at: datetime
    succeeded: bool
    error_type: str | None = None


class JobMonitor:
    def __init__(self):
        self._statuses: dict[str, JobRunStatus] = {}

    def handle_event(self, event: JobExecutionEvent) -> None:
        exception = event.exception
        self._statuses[event.job_id] = JobRunStatus(
            job_id=event.job_id,
            finished_at=datetime.now(timezone.utc),
            succeeded=exception is None,
            error_type=type(exception).__name__ if exception is not None else None,
        )

    def get(self, job_id: str) -> JobRunStatus | None:
        return self._statuses.get(job_id)

    def clear(self) -> None:
        self._statuses.clear()


job_monitor = JobMonitor()
