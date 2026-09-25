"""Public TNTU schedule access, caching, and snapshot persistence."""

import asyncio
import json
import logging
import os
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from bs4 import BeautifulSoup, Tag

from infrastructure.http_client import HttpRequestError, http_client
from schedule.diff import compare_schedule_snapshots, lesson_identity
from schedule.parsing import (
    TNTU_SCHEDULE_URL,
    build_schedule_grid,
    extract_semester_dates,
    extract_semester_start,
    extract_text,
    get_target_week,
    is_valid_schedule_page,
    parse_core_data,
    parse_lesson_cell,
    parse_location,
    sanitize_group,
    table_snapshot,
    transliterate_for_url,
)
from schedule.saturday import SATURDAY_SCHEDULE_SOURCE_URL, get_saturday_substitution

# Backward-compatible private aliases used by existing integrations and tests.
_transliterate_for_url = transliterate_for_url
_extract_text = extract_text
_is_valid_schedule_page = is_valid_schedule_page
_get_target_week = get_target_week
_extract_semester_start = extract_semester_start
_extract_semester_dates = extract_semester_dates
_parse_core_data = parse_core_data
_build_schedule_grid = build_schedule_grid
_parse_location = parse_location
_parse_lesson_cell = parse_lesson_cell
_table_snapshot = table_snapshot
_lesson_identity = lesson_identity
_compare_schedule_snapshots = compare_schedule_snapshots

SNAPSHOTS_FILE = "data/schedule_snapshots.json"
CACHE_TTL_MINUTES = 5
_html_cache: Dict[str, Dict[str, Any]] = {}
_semester_dates_cache: Optional[Tuple[datetime, datetime]] = None
_semester_dates_cache_time: Optional[datetime] = None


class ScheduleLookupError(RuntimeError):
    """Сайт розкладу не дав жодної успішної відповіді."""


def _read_snapshots_sync() -> dict:
    if not os.path.exists(SNAPSHOTS_FILE):
        return {}
    try:
        with open(SNAPSHOTS_FILE, "r", encoding="utf-8") as file:
            value = json.load(file)
            return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_snapshots_sync(snapshots: dict):
    os.makedirs(os.path.dirname(SNAPSHOTS_FILE), exist_ok=True)
    temporary_file = f"{SNAPSHOTS_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(snapshots, file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, SNAPSHOTS_FILE)


async def fetch_schedule_html(group_name: str, *, raise_on_network_error: bool = False) -> Optional[str]:
    clean_group = sanitize_group(group_name)
    clean_group_no_hyphen = clean_group.upper().replace("-", "")
    now = datetime.now()
    cached = _html_cache.get(clean_group)
    if cached and now - cached["timestamp"] < timedelta(minutes=CACHE_TTL_MINUTES):
        return cached["html"]

    html_result = None
    successful_responses = 0
    try:
        response = await http_client.request_text(
            "POST",
            TNTU_SCHEDULE_URL,
            params={"p": "uk/schedule"},
            data={"group": group_name},
        )
        if response.status == 200:
            successful_responses += 1
            soup = BeautifulSoup(response.text, "html.parser")
            if is_valid_schedule_page(soup, clean_group_no_hyphen):
                html_result = response.text

        if not html_result:
            response = await http_client.request_text(
                "GET",
                TNTU_SCHEDULE_URL,
                params={"p": "uk/schedule", "s": f"-{transliterate_for_url(clean_group)}"},
            )
            if response.status == 200:
                successful_responses += 1
                soup = BeautifulSoup(response.text, "html.parser")
                if is_valid_schedule_page(soup, clean_group_no_hyphen):
                    html_result = response.text

        if not html_result:
            response = await http_client.request_text(
                "GET",
                TNTU_SCHEDULE_URL,
                params={"p": "uk/schedule"},
            )
            if response.status == 200:
                successful_responses += 1
                soup = BeautifulSoup(response.text, "html.parser")
                for anchor in soup.find_all("a", href=True):
                    href_value = anchor.get("href")
                    if not href_value:
                        continue
                    href = str(href_value[0] if isinstance(href_value, list) else href_value)
                    if ".pdf" in href.lower():
                        safe_text = sanitize_group(extract_text(anchor)).upper().replace("\xa0", " ").replace("-", "")
                        if clean_group_no_hyphen in safe_text:
                            html_result = response.text
                            break

        if html_result:
            _html_cache[clean_group] = {"html": html_result, "timestamp": now}
        if not html_result and raise_on_network_error and successful_responses == 0:
            raise ScheduleLookupError("Сайт розкладу недоступний")
        return html_result
    except (HttpRequestError, ScheduleLookupError) as error:
        logging.error("Помилка отримання розкладу: %s", error)
        if raise_on_network_error:
            raise ScheduleLookupError("Не вдалося перевірити групу") from error
        return None
    except Exception as error:
        logging.exception("Неочікувана помилка обробки відповіді сайту розкладу")
        if raise_on_network_error:
            raise ScheduleLookupError("Не вдалося обробити розклад групи") from error
        return None


async def get_semester_dates() -> Optional[Tuple[datetime, datetime]]:
    global _semester_dates_cache, _semester_dates_cache_time
    now = datetime.now()
    if (
        _semester_dates_cache
        and _semester_dates_cache_time
        and (now - _semester_dates_cache_time).total_seconds() < 604800
    ):
        return _semester_dates_cache

    try:
        response = await http_client.request_text(
            "GET",
            TNTU_SCHEDULE_URL,
            params={"p": "uk/schedule"},
        )
        if response.status != 200:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        semester_dates = extract_semester_dates(soup)
        if semester_dates:
            _semester_dates_cache = semester_dates
            _semester_dates_cache_time = now
            return _semester_dates_cache
    except HttpRequestError as error:
        logging.error("Не вдалося отримати дати семестру: %s", error)
    except Exception:
        logging.exception("Помилка парсингу дат семестру")
    return None


async def check_group_exists(group_name: str) -> bool:
    html = await fetch_schedule_html(group_name)
    group_exists, _, _, _ = parse_core_data(html, group_name)
    return group_exists


async def check_group_exists_status(group_name: str) -> Optional[bool]:
    try:
        html = await fetch_schedule_html(group_name, raise_on_network_error=True)
    except ScheduleLookupError:
        return None
    group_exists, _, _, _ = parse_core_data(html, group_name)
    return group_exists


async def check_schedule_changes(group_name: str) -> bool:
    return bool(await get_schedule_changes(group_name))


async def get_schedule_changes(group_name: str) -> list:
    html = await fetch_schedule_html(group_name)
    _, table, _, _ = parse_core_data(html, group_name)
    if not isinstance(table, Tag):
        return []
    current = table_snapshot(table)
    if not current and table.find("div", attrs={"class": "Info"}):
        logging.error("Таблиця групи містить пари, але жодну не вдалося розібрати")
        return []
    snapshots = await asyncio.to_thread(_read_snapshots_sync)
    key = sanitize_group(group_name).upper()
    previous = snapshots.get(key)
    snapshots[key] = current
    await asyncio.to_thread(_write_snapshots_sync, snapshots)
    return [] if not isinstance(previous, list) else compare_schedule_snapshots(previous, current)


async def _get_schedule_for_date(group_name: str, target_date: datetime) -> list:
    html = await fetch_schedule_html(group_name)
    group_exists, table, pdf_links, soup = parse_core_data(html, group_name)
    if not group_exists:
        return []

    formatted_pdfs = [
        {
            "time": "📄 PDF",
            "name": pdf["name"],
            "url": pdf["url"],
            "viewer_url": f"https://docs.google.com/viewer?url={urllib.parse.quote(pdf['url'])}",
            "is_pdf": True,
        }
        for pdf in pdf_links
    ]
    if not soup:
        return formatted_pdfs

    saturday_substitution = get_saturday_substitution(group_name, target_date)
    if saturday_substitution:
        weekday = saturday_substitution.source_weekday
        target_week = saturday_substitution.source_week
        if not any(pdf["url"] == SATURDAY_SCHEDULE_SOURCE_URL for pdf in formatted_pdfs):
            formatted_pdfs.append(
                {
                    "time": "📄 PDF",
                    "name": "Суботнє навчання — осінь 2026",
                    "url": SATURDAY_SCHEDULE_SOURCE_URL,
                    "viewer_url": (
                        f"https://docs.google.com/viewer?url={urllib.parse.quote(SATURDAY_SCHEDULE_SOURCE_URL)}"
                    ),
                    "is_pdf": True,
                }
            )
    else:
        semester_start = extract_semester_start(soup)
        semester_dates = extract_semester_dates(soup)
        if semester_start and target_date.date() < semester_start.date():
            return formatted_pdfs
        if semester_dates and target_date.date() > semester_dates[1].date():
            return formatted_pdfs
        weekday = target_date.weekday()
        if weekday > 4:
            return formatted_pdfs
        target_week = get_target_week(soup, target_date)

    if not isinstance(table, Tag):
        return formatted_pdfs
    lessons = []
    for lesson in table_snapshot(table):
        if lesson["week"] != target_week or lesson["weekday"] != weekday:
            continue
        if saturday_substitution:
            lesson = {
                **lesson,
                "saturday_source_weekday": weekday,
                "saturday_source_week": target_week,
                "saturday_source_url": SATURDAY_SCHEDULE_SOURCE_URL,
            }
        lessons.append(lesson)
    return [*lessons, *formatted_pdfs]


async def parse_schedule_for_today(group_name: str) -> list:
    return await _get_schedule_for_date(group_name, datetime.now())


async def parse_schedule_for_tomorrow(group_name: str) -> list:
    return await _get_schedule_for_date(group_name, datetime.now() + timedelta(days=1))
