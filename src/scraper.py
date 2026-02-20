import aiohttp
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timedelta
import hashlib
import json
import os

TNTU_SCHEDULE_URL = "https://tntu.edu.ua/"
HASHES_FILE = "data/schedule_hashes.json"


async def fetch_schedule_html(group_name: str) -> str:
    """Асинхронно завантажує сторінку розкладу для певної групи."""
    try:
        params = {'p': 'uk/schedule', 'group': group_name}
        async with aiohttp.ClientSession() as session:
            async with session.get(TNTU_SCHEDULE_URL, params=params) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    logging.error(f"Помилка доступу до сайту ТНТУ: {response.status}")
                    return None
    except Exception as e:
        logging.error(f"Помилка скрейпінгу: {e}")
        return None


async def parse_schedule_for_tomorrow(group_name: str) -> list:
    """Парсить HTML і повертає розклад на завтра."""
    html = await fetch_schedule_html(group_name)
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')

    pdf_links = []
    for a_tag in soup.find_all('a', href=True):
        if '.pdf' in a_tag['href'].lower() and ('розклад' in a_tag.text.lower() or 'rozklad' in a_tag['href'].lower()):
            full_link = a_tag['href']
            if not full_link.startswith('http'):
                full_link = f"https://tntu.edu.ua/{full_link}"
            pdf_links.append({'name': a_tag.text.strip(), 'url': full_link})

    schedule = []

    tomorrow = datetime.now() + timedelta(days=1)
    weekday = tomorrow.weekday()

    if weekday > 4:
        return schedule

    table = soup.find('table', id='ScheduleWeek')
    if not table:
        return schedule

    grid = {}
    rows = table.find_all('tr')

    for r_idx, row in enumerate(rows):
        col_idx = 0
        for cell in row.find_all(['td', 'th']):
            while grid.get((r_idx, col_idx)) is not None:
                col_idx += 1

            rowspan = int(cell.get('rowspan', 1))
            colspan = int(cell.get('colspan', 1))

            for r in range(rowspan):
                for c in range(colspan):
                    grid[(r_idx + r, col_idx + c)] = cell
            col_idx += colspan

    target_col = weekday + 1
    processed_cells = set()

    for r_idx in range(1, len(rows)):
        time_cell = grid.get((r_idx, 0))
        target_cell = grid.get((r_idx, target_col))

        if not time_cell or not target_cell:
            continue

        if target_cell in processed_cells:
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
            'time': '📄 PDF Розклад',
            'name': f"<a href='{pdf['url']}'>{pdf['name']}</a>",
            'is_pdf': True
        })

    return schedule


async def check_schedule_changes(group_name: str) -> bool:
    """
    Перевіряє, чи змінився розклад для конкретної групи.
    """
    html = await fetch_schedule_html(group_name)
    if not html:
        return False

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', id='ScheduleWeek')

    if not table:
        return False

    table_html = str(table)
    current_hash = hashlib.md5(table_html.encode('utf-8')).hexdigest()

    hashes = {}
    if os.path.exists(HASHES_FILE):
        try:
            with open(HASHES_FILE, 'r', encoding='utf-8') as f:
                hashes = json.load(f)
        except json.JSONDecodeError:
            pass

    previous_hash = hashes.get(group_name)

    if previous_hash and previous_hash != current_hash:
        hashes[group_name] = current_hash
        with open(HASHES_FILE, 'w', encoding='utf-8') as f:
            json.dump(hashes, f)
        return True
    elif not previous_hash:
        hashes[group_name] = current_hash
        os.makedirs(os.path.dirname(HASHES_FILE), exist_ok=True)
        with open(HASHES_FILE, 'w', encoding='utf-8') as f:
            json.dump(hashes, f)
        return False

    return False