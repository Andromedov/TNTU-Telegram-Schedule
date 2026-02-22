import aiohttp
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timedelta
import hashlib
import json
import os

TNTU_SCHEDULE_URL = "https://tntu.edu.ua/"
HASHES_FILE = "data/schedule_hashes.json"


def sanitize_group(group_name: str) -> str:
    """Замінює візуально схожі англійські літери на українські для уникнення помилок вводу."""
    mapping = {
        'A': 'А', 'a': 'а', 'B': 'В', 'C': 'С', 'c': 'с', 'E': 'Е', 'e': 'е',
        'H': 'Н', 'I': 'І', 'i': 'і', 'K': 'К', 'k': 'к', 'M': 'М', 'm': 'м',
        'O': 'О', 'o': 'о', 'P': 'Р', 'p': 'р', 'T': 'Т', 't': 'т', 'X': 'Х', 'x': 'х'
    }
    return "".join(mapping.get(ch, ch) for ch in group_name)


def _transliterate_for_url(text: str) -> str:
    """Транслітерує назву групи для формування прямого URL (напр. СТс-21 -> sts21)."""
    mapping = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'ґ': 'g', 'д': 'd', 'е': 'e', 'є': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'y', 'і': 'i', 'ї': 'i', 'й': 'y', 'к': 'k',
        'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's',
        'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh',
        'щ': 'shch', 'ь': '', 'ю': 'yu', 'я': 'ya',
        '-': ''
    }
    text = text.lower()
    res = ""
    for char in text:
        res += mapping.get(char, char)
    return res


async def fetch_schedule_html(group_name: str) -> str:
    """Асинхронно завантажує сторінку розкладу для певної групи."""
    clean_group = sanitize_group(group_name)

    try:
        async with aiohttp.ClientSession() as session:
            params = {'p': 'uk/schedule'}
            data = {'group': clean_group}

            async with session.post(TNTU_SCHEDULE_URL, params=params, data=data) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    if soup.find('table', id='ScheduleWeek') or soup.find('h2', string=lambda
                            s: s and clean_group.upper() in s.upper()):
                        return html

            group_translit = _transliterate_for_url(clean_group)
            faculties = ['fis', 'fpt', 'fmt', 'fem']

            for fac in faculties:
                s_param = f"{fac}-{group_translit}"
                guess_params = {'p': 'uk/schedule', 's': s_param}
                async with session.get(TNTU_SCHEDULE_URL, params=guess_params) as response:
                    if response.status == 200:
                        html = await response.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        if soup.find('table', id='ScheduleWeek') or soup.find('h2', string=lambda
                                s: s and clean_group.upper() in s.upper()):
                            return html

            async with session.get(TNTU_SCHEDULE_URL, params={'p': 'uk/schedule'}) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    for a_tag in soup.find_all('a', href=True):
                        if '.pdf' in a_tag['href'].lower() and clean_group.upper() in a_tag.text.upper():
                            return html

            return None
    except Exception as e:
        logging.error(f"Помилка скрейпінгу: {e}")
        return None


async def check_group_exists(group_name: str) -> bool:
    """Перевіряє, чи існує група на сайті ТНТУ."""
    clean_group = sanitize_group(group_name)
    html = await fetch_schedule_html(clean_group)
    if not html:
        return False

    soup = BeautifulSoup(html, 'html.parser')

    if soup.find('table', id='ScheduleWeek'):
        return True

    headers = soup.find_all('h2')
    for header in headers:
        if clean_group.upper() in header.text.upper():
            return True

    for a_tag in soup.find_all('a', href=True):
        if '.pdf' in a_tag['href'].lower() and clean_group.upper() in a_tag.text.upper():
            return True

    return False


def _get_target_week(soup: BeautifulSoup, target_date: datetime) -> int:
    """Визначає, який тиждень (1 чи 2) буде в цільову дату."""
    h3_black = soup.find('h3', class_='Black')
    current_week = 1
    if h3_black:
        text = h3_black.text.lower()
        if 'другий' in text:
            current_week = 2
        elif 'перший' in text:
            current_week = 1

    today = datetime.now()
    today_monday = today.date() - timedelta(days=today.weekday())
    target_monday = target_date.date() - timedelta(days=target_date.weekday())
    weeks_diff = (target_monday - today_monday).days // 7

    if weeks_diff % 2 != 0:
        return 2 if current_week == 1 else 1

    return current_week


async def _extract_schedule_from_html(html: str, group_name: str, target_date: datetime) -> list:
    """Парсить HTML (звичайні пари + потрібні PDF) на певну дату."""
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')
    clean_group = sanitize_group(group_name)

    target_week = _get_target_week(soup, target_date)

    pdf_links = []
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].lower()
        if '.pdf' in href:
            raw_text = a_tag.text.strip()
            text_upper = sanitize_group(raw_text).upper().replace('\xa0', ' ')
            group_upper = clean_group.upper()

            if 'ГРУПИ' in text_upper:
                if group_upper in text_upper:
                    full_link = a_tag['href']
                    if not full_link.startswith('http'):
                        full_link = f"https://tntu.edu.ua/{full_link}"
                    pdf_links.append({'name': raw_text, 'url': full_link})
            elif 'ГРАФІК' in text_upper or 'РОЗКЛАД' in text_upper:
                full_link = a_tag['href']
                if not full_link.startswith('http'):
                    full_link = f"https://tntu.edu.ua/{full_link}"
                pdf_links.append({'name': raw_text, 'url': full_link})

    schedule = []

    weekday = target_date.weekday()

    if weekday > 4:
        return schedule

    table = soup.find('table', id='ScheduleWeek')
    if not table:
        for pdf in pdf_links:
            schedule.append({'time': '📄 PDF', 'name': f"<a href='{pdf['url']}'>{pdf['name']}</a>", 'is_pdf': True})
        return schedule

    grid = {}
    rows = table.find_all('tr')

    for r_idx, row in enumerate(rows):
        col_idx = 0
        for cell in row.find_all(['td', 'th']):
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
    for r_idx in range(1, len(rows)):
        time_cell = grid.get((r_idx, 0))
        if time_cell:
            if time_cell not in time_to_rows:
                time_to_rows[time_cell] = []
            if r_idx not in time_to_rows[time_cell]:
                time_to_rows[time_cell].append(r_idx)

    for time_cell, indices in time_to_rows.items():
        if len(indices) >= 2:
            active_r_idx = indices[0] if target_week == 1 else indices[1]
        else:
            active_r_idx = indices[0]

        target_cell = grid.get((active_r_idx, target_col))

        if not target_cell or target_cell in processed_cells:
            continue

        processed_cells.add(target_cell)

        time_div = time_cell.find('div', class_='LessonPeriod')
        if not time_div:
            continue
        time_str = time_div.text.strip()  # "8:00-9:20"

        subject_link = target_cell.find('a')
        if not subject_link:
            continue

        subject_name = subject_link.text.strip()

        info_div = target_cell.find('div', class_='Info')
        info_text = info_div.get_text(separator=" ", strip=True) if info_div else ""

        notes_div = target_cell.find('div', class_='Notes')
        notes_text = notes_div.text.strip() if notes_div else ""

        full_name = f"{subject_name} ({info_text})"
        if notes_text:
            full_name += f" ❗️{notes_text}"

        schedule.append({
            'time': time_str,
            'name': full_name,
            'is_pdf': False
        })

    for pdf in pdf_links:
        schedule.append({
            'time': '📄 PDF',
            'name': f"<a href='{pdf['url']}'>{pdf['name']}</a>",
            'is_pdf': True
        })

    return schedule


async def parse_schedule_for_tomorrow(group_name: str) -> list:
    """
    Отримує розклад на завтра.
    """
    html = await fetch_schedule_html(group_name)
    tomorrow = datetime.now() + timedelta(days=1)
    return await _extract_schedule_from_html(html, group_name, tomorrow)


async def parse_schedule_for_today(group_name: str) -> list:
    """
    Отримує розклад на сьогодні.
    """
    html = await fetch_schedule_html(group_name)
    today = datetime.now()
    return await _extract_schedule_from_html(html, group_name, today)


async def check_schedule_changes(group_name: str) -> bool:
    """
    Перевіряє, чи змінився розклад для конкретної групи.
    """
    clean_group = sanitize_group(group_name)
    html = await fetch_schedule_html(clean_group)
    if not html:
        return False

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', id='ScheduleWeek')

    if not table:
        return False

    for tag in table.find_all(True):
        if 'class' in tag.attrs:
            del tag.attrs['class']

    table_html = str(table)
    current_hash = hashlib.md5(table_html.encode('utf-8')).hexdigest()

    hashes = {}
    if os.path.exists(HASHES_FILE):
        try:
            with open(HASHES_FILE, 'r', encoding='utf-8') as f:
                hashes = json.load(f)
        except json.JSONDecodeError:
            pass

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

    return False