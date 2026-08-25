import aiohttp
from bs4 import BeautifulSoup, Tag
import logging
from datetime import datetime, timedelta
import json
import os
import copy
import asyncio
import urllib.parse
import re
from typing import Optional, Tuple, List, Dict, Any

TNTU_SCHEDULE_URL = "https://tntu.edu.ua/"
SNAPSHOTS_FILE = "data/schedule_snapshots.json"

# ==========================================
#          ГЛОБАЛЬНИЙ КЕШ
# ==========================================
# Формат: {"GROUP_NAME": {"html": "...", "timestamp": datetime_object}}
_html_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = 5

_semester_dates_cache: Optional[Tuple[datetime, datetime]] = None
_semester_dates_cache_time: Optional[datetime] = None
# ==========================================

def sanitize_group(group_name: str) -> str:
    """Замінює візуально схожі англійські літери на українські."""
    mapping: Dict[str, str] = {
        'A': 'А', 'a': 'а', 'B': 'В', 'C': 'С', 'c': 'с', 'E': 'Е', 'e': 'е',
        'H': 'Н', 'I': 'І', 'i': 'і', 'K': 'К', 'k': 'к', 'M': 'М', 'm': 'м',
        'O': 'О', 'o': 'о', 'P': 'Р', 'p': 'р', 'T': 'Т', 't': 'т', 'X': 'Х', 'x': 'х'
    }
    res: List[str] = []
    for ch in group_name:
        res.append(str(mapping.get(ch, ch)))
    return "".join(res)


def _transliterate_for_url(text: str) -> str:
    """Транслітерує назву групи для формування прямого URL."""
    mapping: Dict[str, str] = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'ґ': 'g', 'д': 'd', 'е': 'e', 'є': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'y', 'і': 'i', 'ї': 'i', 'й': 'y', 'к': 'k',
        'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's',
        'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh',
        'щ': 'shch', 'ь': '', 'ю': 'yu', 'я': 'ya', '-': ''
    }
    res: List[str] = []
    for char in text.lower():
        res.append(str(mapping.get(char, char)))
    return "".join(res)


def _extract_text(element: Tag) -> str:
    """
    Безпечно дістає текст з тегу BeautifulSoup, розділяючи елементи пробілами.
    Це вирішує конфлікти типізації, пов'язані з методом get_text().
    """
    texts: List[str] = []
    for t in element.strings:
        s = str(t).strip()
        if s:
            texts.append(s)
    return " ".join(texts)


def _is_valid_schedule_page(soup: BeautifulSoup, clean_group_no_hyphen: str) -> bool:
    """Перевіряє, чи містить сторінка розклад для цільової групи (допоміжна функція)."""
    has_target_heading = False
    for h2 in soup.find_all('h2'):
        if isinstance(h2, Tag) and clean_group_no_hyphen in sanitize_group(_extract_text(h2)).upper().replace('-', ''):
            has_target_heading = True
            break

    return has_target_heading and isinstance(soup.find('table', attrs={'id': 'ScheduleWeek'}), Tag)


def _get_target_week(soup: BeautifulSoup, target_date: datetime) -> int:
    """Визначає, який тиждень (1 чи 2) буде в цільову дату."""
    semester_start = _extract_semester_start(soup)
    if semester_start is not None:
        semester_monday = semester_start.date() - timedelta(days=semester_start.weekday())
        target_monday = target_date.date() - timedelta(days=target_date.weekday())
        if target_monday < semester_monday:
            return 1
        weeks_diff = (target_monday - semester_monday).days // 7
        return 1 if weeks_diff % 2 == 0 else 2

    h3_black = soup.find('h3', attrs={'class': 'Black'})
    current_week = 1
    if isinstance(h3_black, Tag):
        text = _extract_text(h3_black).lower()
        if 'другий' in text:
            current_week = 2

    today = datetime.now()
    today_monday = today.date() - timedelta(days=today.weekday())
    target_monday = target_date.date() - timedelta(days=target_date.weekday())
    weeks_diff = (target_monday - today_monday).days // 7

    if weeks_diff % 2 != 0:
        return 2 if current_week == 1 else 1
    return current_week


def _extract_semester_start(soup: BeautifulSoup) -> Optional[datetime]:
    """Дістає початок семестру з заголовка над таблицею розкладу."""
    months_map = {
        'січня': 1, 'лютого': 2, 'березня': 3, 'квітня': 4, 'травня': 5, 'червня': 6,
        'липня': 7, 'серпня': 8, 'вересня': 9, 'жовтня': 10, 'листопада': 11, 'грудня': 12,
    }
    pattern = re.compile(r"(\d{1,2})\s+([а-яіїєґ]+).*?(\d{4})\s*року", re.IGNORECASE)
    schedule = soup.find('div', attrs={'id': 'Schedule'})
    root = schedule if isinstance(schedule, Tag) else soup
    for heading in root.find_all(['h2', 'h3']):
        if not isinstance(heading, Tag):
            continue
        match = pattern.search(_extract_text(heading))
        if not match:
            continue
        month = months_map.get(match.group(2).lower())
        if month:
            try:
                return datetime(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                return None
    return None


# ==========================================
#    СИНХРОННІ ФУНКЦІЇ ДЛЯ РОБОТИ З ФАЙЛАМИ
# ==========================================

def _read_snapshots_sync() -> dict:
    if not os.path.exists(SNAPSHOTS_FILE):
        return {}
    try:
        with open(SNAPSHOTS_FILE, 'r', encoding='utf-8') as file:
            value = json.load(file)
            return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_snapshots_sync(snapshots: dict):
    os.makedirs(os.path.dirname(SNAPSHOTS_FILE), exist_ok=True)
    temporary_file = f"{SNAPSHOTS_FILE}.tmp"
    with open(temporary_file, 'w', encoding='utf-8') as file:
        json.dump(snapshots, file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, SNAPSHOTS_FILE)


# ==========================================
#      МЕРЕЖЕВИЙ РІВЕНЬ (Отримання HTML)
# ==========================================

class ScheduleLookupError(RuntimeError):
    """Сайт розкладу не дав жодної успішної відповіді."""


async def fetch_schedule_html(group_name: str, *, raise_on_network_error: bool = False) -> Optional[str]:
    """Асинхронно завантажує сторінку розкладу."""
    clean_group = sanitize_group(group_name)
    clean_group_no_hyphen = clean_group.upper().replace('-', '')

    now = datetime.now()
    if clean_group in _html_cache:
        cached_data = _html_cache[clean_group]
        if now - cached_data['timestamp'] < timedelta(minutes=CACHE_TTL_MINUTES):
            return cached_data['html']

    html_result = None
    successful_responses = 0

    try:
        async with aiohttp.ClientSession() as session:
            # POST запит
            async with session.post(TNTU_SCHEDULE_URL, params={'p': 'uk/schedule'}, data={'group': group_name}) as resp:
                if resp.status == 200:
                    successful_responses += 1
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    if _is_valid_schedule_page(soup, clean_group_no_hyphen):
                        html_result = html

            # Якщо POST не спрацював, робимо GET запит по факультетах
            if not html_result:
                group_translit = _transliterate_for_url(clean_group)
                async with session.get(TNTU_SCHEDULE_URL,
                                       params={'p': 'uk/schedule', 's': f"-{group_translit}"}) as resp:
                    if resp.status == 200:
                        successful_responses += 1
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        if _is_valid_schedule_page(soup, clean_group_no_hyphen):
                            html_result = html

            # Резервний GET запит для PDF сторінки
            if not html_result:
                async with session.get(TNTU_SCHEDULE_URL, params={'p': 'uk/schedule'}) as resp:
                    if resp.status == 200:
                        successful_responses += 1
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        for a_tag in soup.find_all('a', href=True):
                            href_attr = a_tag.get('href')
                            if not href_attr:
                                continue

                            # Надійне отримання рядка з атрибуту
                            href_str = str(href_attr[0] if isinstance(href_attr, list) else href_attr)

                            if '.pdf' in href_str.lower():
                                safe_text = sanitize_group(_extract_text(a_tag)).upper().replace('\xa0', ' ').replace('-', '')
                                if clean_group_no_hyphen in safe_text:
                                    html_result = html
                                    break

            # Зберігаємо результат у кеш, якщо він знайдений
            if html_result:
                _html_cache[clean_group] = {'html': html_result, 'timestamp': now}

            if not html_result and raise_on_network_error and successful_responses == 0:
                raise ScheduleLookupError(f"Сайт розкладу недоступний для перевірки групи {group_name}")

            return html_result

    except Exception as e:
        logging.error(f"Помилка скрейпінгу: {e}")
        if raise_on_network_error:
            raise ScheduleLookupError(f"Не вдалося перевірити групу {group_name}") from e
        return None


# ==========================================
#               ЯДРО ПАРСИНГУ
# ==========================================

def _parse_core_data(html: Optional[str], group_name: str) -> Tuple[
    bool, Optional[Tag], List[Dict[str, Any]], Optional[BeautifulSoup]]:
    """Парсить HTML, повертає об'єкти для розкладу."""
    if not html:
        return False, None, [], None

    soup = BeautifulSoup(html, 'html.parser')
    clean_group_no_hyphen = sanitize_group(group_name).upper().replace('-', '')

    group_exists = False
    table = soup.find('table', attrs={'id': 'ScheduleWeek'})

    if not isinstance(table, Tag):
        table = None
        for tbl in soup.find_all('table'):
            if not isinstance(tbl, Tag):
                continue
            headers = [_extract_text(th).lower() for th in tbl.find_all('th') if isinstance(th, Tag)]
            if any('понеділок' in h or 'вівторок' in h for h in headers):
                table = tbl
                break

    if isinstance(table, Tag):
        group_exists = True
    else:
        for h2 in soup.find_all('h2'):
            if isinstance(h2, Tag) and clean_group_no_hyphen in sanitize_group(_extract_text(h2)).upper().replace('-', ''):
                group_exists = True
                break

    pdf_links: List[Dict[str, Any]] = []
    for a_tag in soup.find_all('a', href=True):
        if not isinstance(a_tag, Tag):
            continue

        href_attr = a_tag.get('href')
        if not href_attr:
            continue

        # Якщо href_attr повертає список (дуже рідко, але буває), беремо 1 елемент
        href_str = str(href_attr[0] if isinstance(href_attr, list) else href_attr)

        if '.pdf' in href_str.lower():
            raw_text = _extract_text(a_tag)
            safe_text = sanitize_group(raw_text).upper().replace('\xa0', ' ').replace('-', '')

            if ('ГРУПИ' in safe_text and clean_group_no_hyphen in safe_text) or (
                    'ГРАФІК' in safe_text or 'РОЗКЛАД' in safe_text):
                full_link = href_str if href_str.startswith(
                    'http') else f"https://tntu.edu.ua/{href_str}"

                # Уникаємо дублікатів PDF
                if not any(pdf['url'] == full_link for pdf in pdf_links):
                    pdf_links.append({'name': raw_text, 'url': full_link})

                group_exists = True

    return group_exists, table, pdf_links, soup


# ==========================================
#          ПУБЛІЧНІ ФУНКЦІЇ ДЛЯ БОТА
# ==========================================

async def get_semester_dates() -> Optional[Tuple[datetime, datetime]]:
    """Отримує та парсить дати початку й кінця поточного семестру."""
    global _semester_dates_cache, _semester_dates_cache_time
    now = datetime.now()

    if _semester_dates_cache and _semester_dates_cache_time and (now - _semester_dates_cache_time).total_seconds() < 604800:
        return _semester_dates_cache

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TNTU_SCHEDULE_URL, params={'p': 'uk/schedule'}) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')

                    pattern = re.compile(
                        r"(\d{1,2})\s+([а-яяіїє]+)(?:\s+(\d{4}))?\s*(?:-|–|—|до)\s*(\d{1,2})\s+([а-яяіїє]+)\s+(\d{4})",
                        re.IGNORECASE
                    )

                    months_map = {
                        'січня': 1, 'лютого': 2, 'березня': 3, 'квітня': 4, 'травня': 5, 'червня': 6,
                        'липня': 7, 'серпня': 8, 'вересня': 9, 'жовтня': 10, 'листопада': 11, 'грудня': 12
                    }

                    for tag in soup.find_all(['h2', 'h3', 'div', 'p']):
                        if not isinstance(tag, Tag):
                            continue

                        text = _extract_text(tag)
                        match = pattern.search(text)

                        if match:
                            try:
                                start_day = int(match.group(1))
                                start_month_str = match.group(2).lower()
                                start_year_str = match.group(3)

                                end_day = int(match.group(4))
                                end_month_str = match.group(5).lower()
                                end_year = int(match.group(6))

                                start_month = months_map.get(start_month_str)
                                end_month = months_map.get(end_month_str)

                                if not start_month or not end_month:
                                    continue

                                start_year = int(start_year_str) if start_year_str else end_year

                                if not start_year_str and start_month > end_month:
                                    start_year = end_year - 1

                                start_date = datetime(start_year, start_month, start_day)
                                end_date = datetime(end_year, end_month, end_day, 23, 59, 59)

                                _semester_dates_cache = (start_date, end_date)
                                _semester_dates_cache_time = now
                                return _semester_dates_cache

                            except ValueError:
                                continue
    except Exception as e:
        logging.error(f"Помилка парсингу дат семестру: {e}")

    return None


async def check_group_exists(group_name: str) -> bool:
    """Перевіряє, чи існує група на сайті ТНТУ."""
    html = await fetch_schedule_html(group_name)
    group_exists, _, _, _ = _parse_core_data(html, group_name)
    return group_exists


async def check_group_exists_status(group_name: str) -> Optional[bool]:
    """Повертає True/False для наявної/відсутньої групи та None при помилці сайту."""
    try:
        html = await fetch_schedule_html(group_name, raise_on_network_error=True)
    except ScheduleLookupError:
        return None

    group_exists, _, _, _ = _parse_core_data(html, group_name)
    return group_exists


async def check_schedule_changes(group_name: str) -> bool:
    """Сумісна булева перевірка; деталі повертає get_schedule_changes()."""
    return bool(await get_schedule_changes(group_name))


def _build_schedule_grid(table: Tag) -> tuple[list[Tag], Dict[Tuple[int, int], Tag]]:
    rows = [row for row in table.find_all('tr') if isinstance(row, Tag)]
    grid: Dict[Tuple[int, int], Tag] = {}
    for row_index, row in enumerate(rows):
        column_index = 0
        for cell in row.find_all(['td', 'th'], recursive=False):
            if not isinstance(cell, Tag):
                continue
            while (row_index, column_index) in grid:
                column_index += 1
            try:
                rowspan = int(str(cell.get('rowspan', 1)))
                colspan = int(str(cell.get('colspan', 1)))
            except ValueError:
                rowspan = colspan = 1
            for row_offset in range(rowspan):
                for column_offset in range(colspan):
                    grid[(row_index + row_offset, column_index + column_offset)] = cell
            column_index += colspan
    return rows, grid


def _parse_location(location: str) -> tuple[Optional[str], Optional[str]]:
    match = re.fullmatch(r"([А-ЯІЇЄҐA-Z]+\d+)\s*[-–—]\s*(.+)", location.strip(), re.IGNORECASE)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _parse_lesson_cell(cell: Tag, time_text: str) -> Optional[Dict[str, Any]]:
    subject_link = cell.find('a', href=True)
    subject_div = cell.find('div', attrs={'class': 'Subject'})
    if isinstance(subject_link, Tag):
        subject = _extract_text(subject_link)
    elif isinstance(subject_div, Tag):
        subject = _extract_text(subject_div)
    else:
        clone = copy.deepcopy(cell)
        for detail in clone.find_all(['div', 'span'], attrs={'class': ['Info', 'Notes', 'LessonType']}):
            if isinstance(detail, Tag):
                detail.decompose()
        subject = _extract_text(clone)

    if not subject or subject == '-':
        return None

    info = cell.find('div', attrs={'class': 'Info'})
    info_parts = list(info.stripped_strings) if isinstance(info, Tag) else []
    lesson_type = str(info_parts[0]).strip().lower() if info_parts else None
    location = str(info_parts[-1]).strip() if len(info_parts) > 1 else None
    if location == lesson_type:
        location = None
    building, room = _parse_location(location or '')

    notes_element = cell.find('div', attrs={'class': 'Notes'})
    notes = _extract_text(notes_element) if isinstance(notes_element, Tag) else None
    href = str(subject_link.get('href')) if isinstance(subject_link, Tag) else None
    atutor_url = urllib.parse.urljoin(TNTU_SCHEDULE_URL, href) if href else None

    details = [value for value in (lesson_type, location) if value]
    full_name = subject + (f" ({', '.join(details)})" if details else '')
    if notes:
        full_name += f" ❗️{notes}"

    return {
        'time': time_text,
        'name': full_name,
        'subject': subject,
        'lesson_type': lesson_type,
        'location': location,
        'building': building,
        'room': room,
        'atutor_url': atutor_url,
        'notes': notes,
        'is_pdf': False,
    }


def _table_snapshot(table: Tag) -> List[Dict[str, Any]]:
    rows, grid = _build_schedule_grid(table)
    time_to_rows: Dict[int, Dict[str, Any]] = {}
    for row_index in range(1, len(rows)):
        time_cell = grid.get((row_index, 0))
        if not isinstance(time_cell, Tag):
            continue
        data = time_to_rows.setdefault(id(time_cell), {'cell': time_cell, 'indices': []})
        if row_index not in data['indices']:
            data['indices'].append(row_index)

    snapshot: List[Dict[str, Any]] = []
    seen_cells = set()
    for data in time_to_rows.values():
        time_cell = data['cell']
        time_div = time_cell.find('div', attrs={'class': 'LessonPeriod'})
        time_text = _extract_text(time_div) if isinstance(time_div, Tag) else _extract_text(time_cell)
        indices = data['indices']
        for week in (1, 2):
            row_index = indices[min(week - 1, len(indices) - 1)]
            for weekday in range(5):
                cell = grid.get((row_index, weekday + 1))
                identity = (week, weekday, id(cell))
                if not isinstance(cell, Tag) or identity in seen_cells:
                    continue
                seen_cells.add(identity)
                lesson = _parse_lesson_cell(cell, time_text)
                if lesson:
                    lesson.update({'week': week, 'weekday': weekday})
                    snapshot.append(lesson)
    return snapshot


def _lesson_identity(lesson: Dict[str, Any], include_time: bool = True) -> tuple:
    values = (lesson.get('week'), lesson.get('weekday'))
    if include_time:
        values += (lesson.get('time'),)
    return values + (lesson.get('subject'),)


def _compare_schedule_snapshots(old: list, new: list) -> list:
    def changed_details(before: dict, after: dict) -> dict:
        fields = {
            field: {'old': before.get(field), 'new': after.get(field)}
            for field in ('lesson_type', 'building', 'room', 'atutor_url', 'notes')
            if before.get(field) != after.get(field)
        }
        if (before.get('location') != after.get('location')
                and not any(field in fields for field in ('building', 'room'))):
            fields['location'] = {'old': before.get('location'), 'new': after.get('location')}
        return fields

    unmatched_new = list(new)
    changes = []

    for old_lesson in old:
        exact = next((item for item in unmatched_new if _lesson_identity(item) == _lesson_identity(old_lesson)), None)
        if exact is not None:
            unmatched_new.remove(exact)
            fields = changed_details(old_lesson, exact)
            if fields:
                changes.append({'kind': 'changed', 'lesson': exact, 'fields': fields})
            continue

        moved_candidates = [
            item for item in unmatched_new
            if _lesson_identity(item, include_time=False) == _lesson_identity(old_lesson, include_time=False)
        ]
        moved = next(
            (item for item in moved_candidates if item.get('lesson_type') == old_lesson.get('lesson_type')),
            moved_candidates[0] if moved_candidates else None,
        )
        if moved is not None:
            unmatched_new.remove(moved)
            fields = {'time': {'old': old_lesson.get('time'), 'new': moved.get('time')}}
            fields.update(changed_details(old_lesson, moved))
            changes.append({'kind': 'changed', 'lesson': moved, 'fields': fields})
            continue

        same_slot = next((item for item in unmatched_new
                          if (item.get('week'), item.get('weekday'), item.get('time')) ==
                          (old_lesson.get('week'), old_lesson.get('weekday'), old_lesson.get('time'))), None)
        if same_slot is not None:
            unmatched_new.remove(same_slot)
            changes.append({'kind': 'changed', 'lesson': same_slot, 'fields': {
                'subject': {'old': old_lesson.get('subject'), 'new': same_slot.get('subject')}
            }})
        else:
            changes.append({'kind': 'removed', 'lesson': old_lesson})

    changes.extend({'kind': 'added', 'lesson': lesson} for lesson in unmatched_new)
    return changes


async def get_schedule_changes(group_name: str) -> list:
    """Зберігає структурований snapshot і повертає конкретні зміни."""
    html = await fetch_schedule_html(group_name)
    _, table, _, _ = _parse_core_data(html, group_name)
    if not isinstance(table, Tag):
        return []

    current = _table_snapshot(table)
    if not current and table.find('div', attrs={'class': 'Info'}):
        logging.error("Таблиця групи %s містить пари, але жодну не вдалося розібрати", group_name)
        return []
    snapshots = await asyncio.to_thread(_read_snapshots_sync)
    key = sanitize_group(group_name).upper()
    previous = snapshots.get(key)
    snapshots[key] = current
    await asyncio.to_thread(_write_snapshots_sync, snapshots)
    if not isinstance(previous, list):
        return []
    return _compare_schedule_snapshots(previous, current)


async def _get_schedule_for_date(group_name: str, target_date: datetime) -> list:
    """Парсинг розкладу на конкретну дату."""
    html = await fetch_schedule_html(group_name)
    group_exists, table, pdf_links, soup = _parse_core_data(html, group_name)

    schedule = []
    if not group_exists:
        return schedule

    formatted_pdfs = []
    for p in pdf_links:
        encoded_url = urllib.parse.quote(p['url'])
        viewer_url = f"https://docs.google.com/viewer?url={encoded_url}"
        formatted_pdfs.append({
            'time': '📄 PDF',
            'name': p['name'],
            'url': p['url'],
            'viewer_url': viewer_url,
            'is_pdf': True
        })

    if not soup:
        return formatted_pdfs

    weekday = target_date.weekday()
    if weekday > 4 or not isinstance(table, Tag):
        return formatted_pdfs

    target_week = _get_target_week(soup, target_date)
    schedule.extend(
        lesson for lesson in _table_snapshot(table)
        if lesson['week'] == target_week and lesson['weekday'] == weekday
    )

    schedule.extend(formatted_pdfs)
    return schedule


async def parse_schedule_for_today(group_name: str) -> list:
    return await _get_schedule_for_date(group_name, datetime.now())


async def parse_schedule_for_tomorrow(group_name: str) -> list:
    return await _get_schedule_for_date(group_name, datetime.now() + timedelta(days=1))
