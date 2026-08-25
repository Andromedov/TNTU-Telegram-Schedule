from typing import Any, Dict


def lesson_identity(lesson: Dict[str, Any], include_time: bool = True) -> tuple:
    values = (lesson.get("week"), lesson.get("weekday"))
    if include_time:
        values += (lesson.get("time"),)
    return values + (lesson.get("subject"),)


def compare_schedule_snapshots(old: list, new: list) -> list:
    def changed_details(before: dict, after: dict) -> dict:
        fields = {
            field: {"old": before.get(field), "new": after.get(field)}
            for field in ("lesson_type", "building", "room", "atutor_url", "notes")
            if before.get(field) != after.get(field)
        }
        if before.get("location") != after.get("location") and not any(
            field in fields for field in ("building", "room")
        ):
            fields["location"] = {"old": before.get("location"), "new": after.get("location")}
        return fields

    unmatched_new = list(new)
    changes = []
    for old_lesson in old:
        exact = next((item for item in unmatched_new if lesson_identity(item) == lesson_identity(old_lesson)), None)
        if exact is not None:
            unmatched_new.remove(exact)
            fields = changed_details(old_lesson, exact)
            if fields:
                changes.append({"kind": "changed", "lesson": exact, "fields": fields})
            continue

        moved_candidates = [
            item
            for item in unmatched_new
            if lesson_identity(item, include_time=False) == lesson_identity(old_lesson, include_time=False)
        ]
        moved = next(
            (item for item in moved_candidates if item.get("lesson_type") == old_lesson.get("lesson_type")),
            moved_candidates[0] if moved_candidates else None,
        )
        if moved is not None:
            unmatched_new.remove(moved)
            fields = {"time": {"old": old_lesson.get("time"), "new": moved.get("time")}}
            fields.update(changed_details(old_lesson, moved))
            changes.append({"kind": "changed", "lesson": moved, "fields": fields})
            continue

        same_slot = next(
            (
                item
                for item in unmatched_new
                if (item.get("week"), item.get("weekday"), item.get("time"))
                == (old_lesson.get("week"), old_lesson.get("weekday"), old_lesson.get("time"))
            ),
            None,
        )
        if same_slot is not None:
            unmatched_new.remove(same_slot)
            changes.append(
                {
                    "kind": "changed",
                    "lesson": same_slot,
                    "fields": {"subject": {"old": old_lesson.get("subject"), "new": same_slot.get("subject")}},
                }
            )
        else:
            changes.append({"kind": "removed", "lesson": old_lesson})

    changes.extend({"kind": "added", "lesson": lesson} for lesson in unmatched_new)
    return changes
