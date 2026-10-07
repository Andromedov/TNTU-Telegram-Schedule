def user_subgroup(user, group_name: str | None = None) -> int | None:
    """A saved subgroup applies only to the user's own group."""
    values = dict(user) if user else {}
    if group_name is not None and group_name != values.get("group_name"):
        return None
    subgroup = values.get("subgroup")
    return subgroup if subgroup in (1, 2) else None


def lesson_matches_subgroup(lesson: dict, subgroup: int | None) -> bool:
    return subgroup is None or lesson.get("subgroup") in (None, subgroup)


def filter_schedule(schedule: list, subgroup: int | None) -> list:
    """Keep common classes, the selected subgroup, and supplementary PDFs."""
    return [item for item in schedule if item.get("is_pdf") or lesson_matches_subgroup(item, subgroup)]
