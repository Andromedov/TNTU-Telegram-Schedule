import aiohttp
from bs4 import BeautifulSoup, Tag
import logging
from datetime import datetime, timedelta
import hashlib
import json
import os
import copy
import asyncio
from typing import Optional, Tuple, List, Dict, Any

TNTU_SCHEDULE_URL = "https://tntu.edu.ua/"
HASHES_FILE = "data/schedule_hashes.json"

# ==========================================
#          ГЛОБАЛЬНИЙ КЕШ
# ==========================================
# Формат: {"GROUP_NAME": {"html": "...", "timestamp": datetime_object}}
_html_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = 5


# ==========================================
#  ДОПОМІЖНІ ФУНКЦІЇ (Форматування тексту)
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
    if isinstance(soup.find('table', attrs={'id': 'ScheduleWeek'}), Tag):
        return True

    for h2 in soup.find_all('h2'):
        if isinstance(h2, Tag) and clean_group_no_hyphen in sanitize_group(_extract_text(h2)).upper().replace('-', ''):
            return True

    return False


def _get_target_week(soup: BeautifulSoup, target_date: datetime) -> int:
    """Визначає, який тиждень (1 чи 2) буде в цільову дату."""
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


# ==========================================
#    СИНХРОННІ ФУНКЦІЇ ДЛЯ РОБОТИ З ФАЙЛАМИ
# ==========================================

def _read_hashes_sync() -> dict:
    if os.path.exists(HASHES_FILE):
        try:
            with open(HASHES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}


def _write_hashes_sync(hashes: dict):
    os.makedirs(os.path.dirname(HASHES_FILE), exist_ok=True)
    with open(HASHES_FILE, 'w', encoding='utf-8') as f:
        json.dump(hashes, f)


# ==========================================
#      МЕРЕЖЕВИЙ РІВЕНЬ (Отримання HTML)
# ==========================================

async def fetch_schedule_html(group_name: str) -> Optional[str]:
    """Асинхронно завантажує сторінку розкладу."""
    clean_group = sanitize_group(group_name)
    clean_group_no_hyphen = clean_group.upper().replace('-', '')

    now = datetime.now()
    if clean_group in _html_cache:
        cached_data = _html_cache[clean_group]
        if now - cached_data['timestamp'] < timedelta(minutes=CACHE_TTL_MINUTES):
            return cached_data['html']

    html_result = None

    try:
        async with aiohttp.ClientSession() as session:
            # POST запит
            async with session.post(TNTU_SCHEDULE_URL, params={'p': 'uk/schedule'}, data={'group': group_name}) as resp:
                if resp.status == 200:
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
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        if _is_valid_schedule_page(soup, clean_group_no_hyphen):
                            html_result = html

            # Резервний GET запит для PDF сторінки
            if not html_result:
                async with session.get(TNTU_SCHEDULE_URL, params={'p': 'uk/schedule'}) as resp:
                    if resp.status == 200:
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

            return html_result

    except Exception as e:
        logging.error(f"Помилка скрейпінгу: {e}")
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

async def check_group_exists(group_name: str) -> bool:
    """Перевіряє, чи існує група на сайті ТНТУ."""
    html = await fetch_schedule_html(group_name)
    group_exists, _, _, _ = _parse_core_data(html, group_name)
    return group_exists


async def check_schedule_changes(group_name: str) -> bool:
    """Перевіряє, чи змінився розклад (хешує лише текст таблиці)."""
    html = await fetch_schedule_html(group_name)
    _, table, _, _ = _parse_core_data(html, group_name)

    if not isinstance(table, Tag):
        return False

    for el in table.find_all(['h2', 'h3']):
        if isinstance(el, Tag):
            el.decompose()

    table_text = _extract_text(table)
    current_hash = hashlib.md5(table_text.encode('utf-8')).hexdigest()

    hashes = await asyncio.to_thread(_read_hashes_sync)

    clean_group = sanitize_group(group_name)
    previous_hash = hashes.get(clean_group)

    if previous_hash and previous_hash != current_hash:
        hashes[clean_group] = current_hash
        await asyncio.to_thread(_write_hashes_sync, hashes)
        return True
    elif not previous_hash:
        hashes[clean_group] = current_hash
        await asyncio.to_thread(_write_hashes_sync, hashes)

    return False


async def _get_schedule_for_date(group_name: str, target_date: datetime) -> list:
    """Парсинг розкладу на конкретну дату."""
    html = await fetch_schedule_html(group_name)
    group_exists, table, pdf_links, soup = _parse_core_data(html, group_name)

    schedule = []
    if not group_exists:
        return schedule

    formatted_pdfs = [{'time': '📄 PDF', 'name': f"<a href='{p['url']}'>{p['name']}</a>", 'is_pdf': True} for p in
                      pdf_links]

    if not soup:
        return formatted_pdfs

    weekday = target_date.weekday()
    if weekday > 4 or not isinstance(table, Tag):
        return formatted_pdfs

    target_week = _get_target_week(soup, target_date)

    grid: Dict[Tuple[int, int], Tag] = {}
    rows = table.find_all('tr')

    for r_idx, row in enumerate(rows):
        if not isinstance(row, Tag):
            continue

        col_idx = 0
        cells = row.find_all(['td', 'th'])

        for cell in cells:
            if not isinstance(cell, Tag):
                continue

            while grid.get((r_idx, col_idx)) is not None:
                col_idx += 1

            rs_val = cell.get('rowspan')
            if isinstance(rs_val, list):
                rowspan = int(rs_val[0])
            elif rs_val is not None:
                rowspan = int(rs_val)
            else:
                rowspan = 1

            cs_val = cell.get('colspan')
            if isinstance(cs_val, list):
                colspan = int(cs_val[0])
            elif cs_val is not None:
                colspan = int(cs_val)
            else:
                colspan = 1

            for r in range(rowspan):
                for c in range(colspan):
                    grid[(r_idx + r, col_idx + c)] = cell
            col_idx += colspan

    target_col = weekday + 1
    processed_cells = set()
    time_to_rows = {}

    for r_idx in range(1, len(rows)):
        time_cell = grid.get((r_idx, 0))
        if isinstance(time_cell, Tag):
            t_id = id(time_cell)
            if t_id not in time_to_rows:
                time_to_rows[t_id] = {'cell': time_cell, 'indices': []}
            if r_idx not in time_to_rows[t_id]['indices']:
                time_to_rows[t_id]['indices'].append(r_idx)

    for t_id, data in time_to_rows.items():
        time_cell = data['cell']
        if not isinstance(time_cell, Tag):
            continue

        indices = data['indices']

        if len(indices) >= 2:
            active_r_idx = indices[0] if target_week == 1 else indices[1]
        else:
            active_r_idx = indices[0]

        target_cell = grid.get((active_r_idx, target_col))

        if not isinstance(target_cell, Tag) or id(target_cell) in processed_cells:
            continue

        processed_cells.add(id(target_cell))

        time_div = time_cell.find('div', attrs={'class': 'LessonPeriod'})
        if isinstance(time_div, Tag):
            time_text = _extract_text(time_div)
        else:
            time_text = _extract_text(time_cell)

        if not time_text:
            continue

        subject_name = ""
        subject_link = target_cell.find('a')
        subject_div = target_cell.find('div', attrs={'class': 'Subject'})

        if isinstance(subject_link, Tag):
            subject_name = _extract_text(subject_link)
        elif isinstance(subject_div, Tag):
            subject_name = _extract_text(subject_div)
        else:
            clone = copy.deepcopy(target_cell)
            for d in clone.find_all(['div', 'span', 'br'], attrs={'class': ['Info', 'Notes', 'LessonType']}):
                if isinstance(d, Tag):
                    d.decompose()
            text = _extract_text(clone)
            if text:
                subject_name = text

        if subject_name and subject_name not in ["-", ""]:
            info_div = target_cell.find('div', attrs={'class': 'Info'})
            notes_div = target_cell.find('div', attrs={'class': 'Notes'})

            full_name = subject_name
            if isinstance(info_div, Tag):
                info_text = _extract_text(info_div)
                if info_text:
                    full_name += f" ({info_text})"

            if isinstance(notes_div, Tag):
                notes_text = _extract_text(notes_div)
                if notes_text:
                    full_name += f" ❗️{notes_text}"

            schedule.append({'time': time_text, 'name': full_name, 'is_pdf': False})

    schedule.extend(formatted_pdfs)
    return schedule


async def parse_schedule_for_today(group_name: str) -> list:
    return await _get_schedule_for_date(group_name, datetime.now())


async def parse_schedule_for_tomorrow(group_name: str) -> list:
    return await _get_schedule_for_date(group_name, datetime.now() + timedelta(days=1))