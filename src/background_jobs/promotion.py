import asyncio
import logging
import re

from aiogram import Bot

import database as db
import scraper
from background_jobs.formatting import get_dismiss_keyboard
from messages import get_html_msg, normalize_language


GROUP_CHECK_CONCURRENCY = 8
GROUP_CHECK_FAILED = "CHECK_FAILED"


def next_group_candidate(group_name: str) -> str | None:
    match = re.fullmatch(r"([А-ЯІЇЄA-Zа-яіїєa-z]+-?)(\d{1,2})(.*)", group_name)
    if not match:
        return None
    prefix, number, suffix = match.groups()
    course = int(number[0])
    if course < 1 or course >= 6:
        return "GRADUATED"
    return f"{prefix}{course + 1}{number[1:]}{suffix}"


async def process_promotion(bot: Bot, dry_run: bool = False):
    promoted_count = graduated_count = 0
    group_counts = {}
    limit = 500
    offset = 0
    while True:
        users = await db.get_users_batch(limit, offset)
        if not users:
            break
        for user in users:
            group = user["group_name"]
            if group:
                group_counts[group] = group_counts.get(group, 0) + 1
        offset += limit

    group_mapping = {}
    candidates = {}
    for group in set(group_counts):
        candidate = next_group_candidate(group)
        if candidate == "GRADUATED":
            group_mapping[group] = candidate
        elif candidate:
            candidates[group] = candidate

    semaphore = asyncio.Semaphore(GROUP_CHECK_CONCURRENCY)

    async def check_group(group: str) -> bool | None:
        async with semaphore:
            for attempt in range(3):
                result = await scraper.check_group_exists_status(group)
                if result is not None:
                    return result
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
            return None

    async def find_valid_group(group: str) -> str | None:
        exists = await check_group(group)
        if exists is True:
            return group
        if exists is None:
            return GROUP_CHECK_FAILED
        if "-" in group:
            parts = group.split("-")
            if len(parts[0]) > 1 and parts[0][-1].isalpha():
                alternate = f"{parts[0][:-1] + parts[0][-1].lower()}-{'-'.join(parts[1:])}"
                alt_exists = await check_group(alternate)
                if alt_exists is True:
                    return alternate
                if alt_exists is None:
                    return GROUP_CHECK_FAILED
        return None

    if candidates:
        results = await asyncio.gather(*(find_valid_group(group) for group in candidates.values()))
        for old_group, new_group in zip(candidates, results):
            group_mapping[old_group] = new_group or "GRADUATED"

    if dry_run:
        report = [f"{group} -> {new_group} (користувачів: {group_counts.get(group, 0)})"
                  for group, new_group in group_mapping.items()]
        return "\n".join(report) if report else "Немає груп для переведення."

    offset = 0
    while True:
        users = await db.get_users_batch(limit, offset)
        if not users:
            break
        for user in users:
            old_group = user["group_name"]
            if old_group not in group_mapping:
                continue
            new_group = group_mapping[old_group]
            if new_group == GROUP_CHECK_FAILED:
                logging.warning("Пропущено переведення групи %s: не вдалося перевірити сайт", old_group)
                continue
            language = normalize_language(dict(user).get("language"))
            if new_group == "GRADUATED":
                await db.clear_user_group(user["user_id"])
                graduated_count += 1
                try:
                    await bot.send_message(user["user_id"], get_html_msg(
                        "promotion.graduated", language=language, group=old_group),
                        parse_mode="HTML", reply_markup=get_dismiss_keyboard(language))
                except Exception as error:
                    logging.warning("Не вдалося повідомити випускника %s: %s", user["user_id"], error)
            else:
                await db.add_or_update_user(user["user_id"], new_group)
                try:
                    await bot.send_message(user["user_id"], get_html_msg(
                        "promotion.promoted", language=language, old_group=old_group, new_group=new_group),
                        parse_mode="HTML", reply_markup=get_dismiss_keyboard(language))
                    promoted_count += 1
                except Exception:
                    pass
        offset += limit
    logging.info("Переведення завершено. Оновлено: %s, Випущено: %s.", promoted_count, graduated_count)


async def promote_groups(bot: Bot):
    logging.info("Запуск автоматичного переведення груп на новий навчальний рік...")
    await process_promotion(bot)


async def promote_groups_dry_run(bot: Bot) -> str | None:
    return await process_promotion(bot, dry_run=True)
