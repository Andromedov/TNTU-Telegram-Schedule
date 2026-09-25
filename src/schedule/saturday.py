import re
from dataclasses import dataclass
from datetime import date, datetime

from schedule.parsing import sanitize_group

SATURDAY_SCHEDULE_SOURCE_URL = "https://tntu.edu.ua/storage/pages/00000170/Saturday_autumn_2026_ukr.pdf"

SPECIAL_M_GROUPS = frozenset(
    {
        "МА-11",
        "МА-12",
        "МН-11",
        "МН-12",
        "МН-13",
        "МБ-11",
        "МБ-12",
        "МБ-13",
        "МС-11",
        "МС-12",
        "МА-21",
        "МН-21",
        "МБ-21",
        "МБ-22",
        "МАС-21",
        "МНС-21",
        "МБС-21",
    }
)


@dataclass(frozen=True)
class SaturdaySubstitution:
    source_weekday: int
    source_week: int
    audience: str = "bachelor"
    included_groups: frozenset[str] = frozenset()
    excluded_groups: frozenset[str] = frozenset()


SATURDAY_SUBSTITUTIONS = {
    date(2026, 9, 26): SaturdaySubstitution(
        0,
        1,
        "bachelor_and_first_year_master",
        excluded_groups=SPECIAL_M_GROUPS,
    ),
    date(2026, 10, 3): SaturdaySubstitution(1, 1, "bachelor_and_first_year_master"),
    date(2026, 10, 10): SaturdaySubstitution(2, 1, "bachelor_and_first_year_master"),
    date(2026, 10, 17): SaturdaySubstitution(3, 1, "bachelor_and_first_year_master"),
    date(2026, 10, 24): SaturdaySubstitution(4, 1, "bachelor_and_first_year_master"),
    date(2026, 10, 31): SaturdaySubstitution(0, 2),
    date(2026, 11, 7): SaturdaySubstitution(1, 2),
    date(2026, 11, 14): SaturdaySubstitution(2, 2),
    date(2026, 11, 21): SaturdaySubstitution(3, 2),
    date(2026, 11, 28): SaturdaySubstitution(4, 2),
    date(2026, 12, 5): SaturdaySubstitution(0, 1, "listed_groups", included_groups=SPECIAL_M_GROUPS),
}


def normalize_group_name(group_name: str) -> str:
    normalized = sanitize_group(group_name).upper().replace(" ", "").replace("–", "-").replace("—", "-")
    if "-" not in normalized:
        suffix = re.fullmatch(r"(.+?)(\d{1,2})", normalized)
        if suffix:
            normalized = f"{suffix.group(1)}-{suffix.group(2)}"
    return normalized


def group_course(group_name: str) -> int | None:
    # TNTU group numbers use the first digit as the course: 1–4 bachelor,
    # 5 first-year master, and 6 second-year master/graduating course.
    match = re.search(r"-(\d)", normalize_group_name(group_name))
    return int(match.group(1)) if match else None


def has_saturday_schedule_date(target_date: date | datetime) -> bool:
    value = target_date.date() if isinstance(target_date, datetime) else target_date
    return value in SATURDAY_SUBSTITUTIONS


def get_saturday_substitution(
    group_name: str,
    target_date: date | datetime,
) -> SaturdaySubstitution | None:
    value = target_date.date() if isinstance(target_date, datetime) else target_date
    substitution = SATURDAY_SUBSTITUTIONS.get(value)
    if substitution is None:
        return None

    normalized_group = normalize_group_name(group_name)
    if normalized_group in substitution.excluded_groups:
        return None
    if substitution.included_groups and normalized_group not in substitution.included_groups:
        return None

    course = group_course(normalized_group)
    if substitution.audience == "listed_groups":
        return substitution
    if substitution.audience == "bachelor_and_first_year_master":
        return substitution if course is not None and 1 <= course <= 5 else None
    return substitution if course is not None and 1 <= course <= 4 else None


def get_saturday_source(schedule: list[dict]) -> tuple[int, int] | None:
    for item in schedule:
        if "saturday_source_weekday" in item and "saturday_source_week" in item:
            return int(item["saturday_source_weekday"]), int(item["saturday_source_week"])
    return None
