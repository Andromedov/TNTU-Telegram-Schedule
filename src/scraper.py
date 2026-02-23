import aiohttp
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timedelta
import hashlib
import json
import os
from typing import Optional, Tuple, List, Dict

TNTU_SCHEDULE_URL = "https://tntu.edu.ua/"
HASHES_FILE = "data/schedule_hashes.json"


# ==========================================
#  ДОПОМІЖНІ ФУНКЦІЇ (Форматування тексту)
# ==========================================

def sanitize_group(group_name: str) -> str:
    """Замінює візуально схожі англійські літери на українські."""
    mapping = {
        'A': 'А', 'a': 'а', 'B': 'В', 'C': 'С', 'c': 'с', 'E': 'Е', 'e': 'е',
        'H': 'Н', 'I': 'І', 'i': 'і', 'K': 'К', 'k': 'к', 'M': 'М', 'm': 'м',
        'O': 'О', 'o': 'о', 'P': 'Р', 'p': 'р', 'T': 'Т', 't': 'т', 'X': 'Х', 'x': 'х'
    }
    return "".join(mapping.get(ch, ch) for ch in group_name)


def _transliterate_for_url(text: str) -> str:
    """Транслітерує назву групи для формування прямого URL."""
    mapping = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'ґ': 'g', 'д': 'd', 'е': 'e', 'є': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'y', 'і': 'i', 'ї': 'i', 'й': 'y', 'к': 'k',
        'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's',
        'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh',
        'щ': 'shch', 'ь': '', 'ю': 'yu', 'я': 'ya', '-': ''
    }
    return "".join(mapping.get(char, char) for char in text.lower())


def _get_target_week(soup: BeautifulSoup, target_date: datetime) -> int:
    """Визначає, який тиждень (1 чи 2) буде в цільову дату."""
    h3_black = soup.find('h3', class_='Black')
    current_week = 1
    if h3_black:
        text = h3_black.text.lower()
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
#      МЕРЕЖЕВИЙ РІВЕНЬ (Отримання HTML)
# ==========================================

async def fetch_schedule_html(group_name: str) -> Optional[str]:
    """Асинхронно завантажує сторінку розкладу."""
    clean_group = sanitize_group(group_name)
    clean_group_no_hyphen = clean_group.upper().replace('-', '')

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(TNTU_SCHEDULE_URL, params={'p': 'uk/schedule'}, data={'group': group_name}) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    if 'id="ScheduleWeek"' in html or clean_group_no_hyphen in html.upper().replace('-', ''):
                        return html

            group_translit = _transliterate_for_url(clean_group)
            for fac in ['fis', 'fpt', 'fmt', 'fem']:
                async with session.get(TNTU_SCHEDULE_URL,
                                       params={'p': 'uk/schedule', 's': f"{fac}-{group_translit}"}) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        if 'id="ScheduleWeek"' in html or clean_group_no_hyphen in html.upper().replace('-', ''):
                            return html

            async with session.get(TNTU_SCHEDULE_URL, params={'p': 'uk/schedule'}) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    if clean_group_no_hyphen in html.upper().replace('\xa0', ' ').replace('-', ''):
                        return html

            return None
    except Exception as e:
        logging.error(f"Помилка скрейпінгу: {e}")
        return None


# ==========================================
#               ЯДРО ПАРСИНГУ
# ==========================================

def _parse_core_data(html: Optional[str], group_name: str) -> Tuple[
    bool, Optional[BeautifulSoup], List[Dict], Optional[BeautifulSoup]]:
    """
    Єдина функція, яка парсить HTML.
    Повертає: (чи_існує_група, таблиця_розкладу, список_pdf, об'єкт_soup)
    """
    if not html:
        return False, None, [], None

    soup = BeautifulSoup(html, 'html.parser')
    clean_group_no_hyphen = sanitize_group(group_name).upper().replace('-', '')

    table = soup.find('table', id='ScheduleWeek')
    if not table:
        for tbl in soup.find_all('table'):
            headers = [th.get_text(strip=True).lower() for th in tbl.find_all('th')]
            if any('понеділок' in h or 'вівторок' in h for h in headers):
                table = tbl
                break

    group_exists = False
    if table:
        group_exists = True
    else:
        for h2 in soup.find_all('h2'):
            if clean_group_no_hyphen in sanitize_group(h2.text).upper().replace('-', ''):
                group_exists = True
                break

    pdf_links = []
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].lower()
        if '.pdf' in href:
            raw_text = a_tag.text.strip()
            safe_text = sanitize_group(raw_text).upper().replace('\xa0', ' ').replace('-', '')

            if ('ГРУПИ' in safe_text and clean_group_no_hyphen in safe_text) or (
                    'ГРАФІК' in safe_text or 'РОЗКЛАД' in safe_text):
                full_link = a_tag['href'] if a_tag['href'].startswith(
                    'http') else f"https://tntu.edu.ua/{a_tag['href']}"
                pdf_links.append({'name': raw_text, 'url': f"https://docs.google.com/viewer?url={full_link}"})
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

    if not table:
        return False

    for el in table.find_all(['h2', 'h3']):
        el.decompose()

    table_text = table.get_text(separator=' ', strip=True)
    current_hash = hashlib.md5(table_text.encode('utf-8')).hexdigest()

    hashes = {}
    if os.path.exists(HASHES_FILE):
        try:
            with open(HASHES_FILE, 'r', encoding='utf-8') as f:
                hashes = json.load(f)
        except json.JSONDecodeError:
            pass

    clean_group = sanitize_group(group_name)
    previous_hash = hashes.get(clean_group)

    if previous_hash and previous_hash != current_hash:
        hashes[clean_group] = current_hash
        with open(HASHES_FILE, 'w', encoding='utf-8') as f:
            json.dump(hashes, f)
        return True
    elif not previous_hash:
        hashes[clean_group] = current_hash
        os.makedirs(os.path.dirname(HASHES_FILE), exist_ok=True)
        with open(HASHES_FILE, 'w', encoding='utf-8') as f:
            json.dump(hashes, f)

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

    weekday = target_date.weekday()
    if weekday > 4 or not table:
        return formatted_pdfs

    target_week = _get_target_week(soup, target_date)

    grid = {}

    rows = [r for r in table.find_all('tr') if r.find_parent('table') == table]

    for r_idx, row in enumerate(rows):
        col_idx = 0
        cells = [c for c in row.find_all(['td', 'th']) if c.find_parent('tr') == row]

        for cell in cells:
            while grid.get((r_idx, col_idx)) is not None:
                col_idx += 1
            rowspan = int(cell.get('rowspan', '1'))
            colspan = int(cell.get('colspan', '1'))
            for r in range(rowspan):
                for c in range(colspan):
                    grid[(r_idx + r, col_idx + c)] = cell
            col_idx += colspan

    target_col = weekday + 1
    processed_cells = set()
    time_to_rows = {}

    for r_idx in range(len(rows)):
        time_cell = grid.get((r_idx, 0))
        if time_cell:
            time_text = time_cell.get_text(strip=True)
            if any(c.isdigit() for c in time_text) and (':' in time_text or '-' in time_text):
                time_to_rows.setdefault(id(time_cell), {'cell': time_cell, 'rows': []})['rows'].append(r_idx)

    for time_id, data in time_to_rows.items():
        time_cell = data['cell']
        indices = list(dict.fromkeys(data['rows']))

        if len(indices) >= 2:
            active_r_idx = indices[0] if target_week == 1 else indices[1]
        else:
            active_r_idx = indices[0]

        target_cell = grid.get((active_r_idx, target_col))

        if not target_cell or id(target_cell) in processed_cells:
            continue

        processed_cells.add(id(target_cell))

        time_div = time_cell.find('div', class_='LessonPeriod')
        time_text = time_div.get_text(separator=' ', strip=True) if time_div else time_cell.get_text(separator=' ',
                                                                                                     strip=True)
        if not time_text:
            continue

        subject_name = ""
        subject_link = target_cell.find('a')
        subject_div = target_cell.find('div', class_='Subject')

        if subject_link:
            subject_name = subject_link.get_text(separator=' ', strip=True)
        elif subject_div:
            subject_name = subject_div.get_text(separator=' ', strip=True)
        else:
            clone = target_cell.copy()
            for d in clone.find_all('div', class_=['Info', 'Notes', 'LessonType']):
                d.decompose()
            text = clone.get_text(separator=' ', strip=True)
            if text:
                subject_name = text

        if subject_name and subject_name not in ["-", ""]:
            info_div = target_cell.find('div', class_='Info')
            notes_div = target_cell.find('div', class_='Notes')

            full_name = subject_name
            if info_div:
                info_text = info_div.get_text(separator=' ', strip=True)
                if info_text:
                    full_name += f" ({info_text})"

            if notes_div:
                notes_text = notes_div.get_text(separator=' ', strip=True)
                if notes_text:
                    full_name += f" ❗️{notes_text}"

            schedule.append({'time': time_text, 'name': full_name, 'is_pdf': False})

    schedule.extend(formatted_pdfs)
    return schedule


async def parse_schedule_for_today(group_name: str) -> list:
    return await _get_schedule_for_date(group_name, datetime.now())


async def parse_schedule_for_tomorrow(group_name: str) -> list:
    return await _get_schedule_for_date(group_name, datetime.now() + timedelta(days=1))