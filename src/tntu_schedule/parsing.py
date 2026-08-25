import copy
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

from schedule_formatting import normalize_atutor_url


TNTU_SCHEDULE_URL = "https://tntu.edu.ua/"


def sanitize_group(group_name: str) -> str:
    mapping = {
        "A": "А", "a": "а", "B": "В", "C": "С", "c": "с", "E": "Е", "e": "е",
        "H": "Н", "I": "І", "i": "і", "K": "К", "k": "к", "M": "М", "m": "м",
        "O": "О", "o": "о", "P": "Р", "p": "р", "T": "Т", "t": "т", "X": "Х", "x": "х",
    }
    return "".join(str(mapping.get(character, character)) for character in group_name)


def transliterate_for_url(text: str) -> str:
    mapping = {
        "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e",
        "є": "e", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "y",
        "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch",
        "ш": "sh", "щ": "shch", "ь": "", "ю": "yu", "я": "ya", "-": "",
    }
    return "".join(str(mapping.get(character, character)) for character in text.lower())


def extract_text(element: Tag) -> str:
    return " ".join(
        value for text in element.strings if (value := str(text).strip())
    )


def is_valid_schedule_page(soup: BeautifulSoup, clean_group_no_hyphen: str) -> bool:
    has_target_heading = any(
        isinstance(heading, Tag)
        and clean_group_no_hyphen in sanitize_group(extract_text(heading)).upper().replace("-", "")
        for heading in soup.find_all("h2")
    )
    return has_target_heading and isinstance(
        soup.find("table", attrs={"id": "ScheduleWeek"}), Tag
    )


def extract_semester_start(soup: BeautifulSoup) -> Optional[datetime]:
    months = {
        "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
        "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11,
        "грудня": 12,
    }
    pattern = re.compile(r"(\d{1,2})\s+([а-яіїєґ]+).*?(\d{4})\s*року", re.IGNORECASE)
    schedule = soup.find("div", attrs={"id": "Schedule"})
    root = schedule if isinstance(schedule, Tag) else soup
    for heading in root.find_all(["h2", "h3"]):
        if not isinstance(heading, Tag):
            continue
        match = pattern.search(extract_text(heading))
        if not match:
            continue
        month = months.get(match.group(2).lower())
        if month:
            try:
                return datetime(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                return None
    return None


def get_target_week(soup: BeautifulSoup, target_date: datetime) -> int:
    semester_start = extract_semester_start(soup)
    if semester_start is not None:
        semester_monday = semester_start.date() - timedelta(days=semester_start.weekday())
        target_monday = target_date.date() - timedelta(days=target_date.weekday())
        if target_monday < semester_monday:
            return 1
        weeks_difference = (target_monday - semester_monday).days // 7
        return 1 if weeks_difference % 2 == 0 else 2

    heading = soup.find("h3", attrs={"class": "Black"})
    current_week = 2 if isinstance(heading, Tag) and "другий" in extract_text(heading).lower() else 1
    today = datetime.now()
    today_monday = today.date() - timedelta(days=today.weekday())
    target_monday = target_date.date() - timedelta(days=target_date.weekday())
    weeks_difference = (target_monday - today_monday).days // 7
    if weeks_difference % 2:
        return 2 if current_week == 1 else 1
    return current_week


def parse_core_data(html: Optional[str], group_name: str) -> Tuple[
        bool, Optional[Tag], List[Dict[str, Any]], Optional[BeautifulSoup]
]:
    if not html:
        return False, None, [], None
    soup = BeautifulSoup(html, "html.parser")
    clean_group = sanitize_group(group_name).upper().replace("-", "")
    table = soup.find("table", attrs={"id": "ScheduleWeek"})
    if not isinstance(table, Tag):
        table = next((
            candidate for candidate in soup.find_all("table")
            if isinstance(candidate, Tag) and any(
                "понеділок" in header or "вівторок" in header
                for header in [
                    extract_text(th).lower() for th in candidate.find_all("th")
                    if isinstance(th, Tag)
                ]
            )
        ), None)

    group_exists = isinstance(table, Tag) or any(
        isinstance(heading, Tag)
        and clean_group in sanitize_group(extract_text(heading)).upper().replace("-", "")
        for heading in soup.find_all("h2")
    )
    pdf_links = []
    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href_value = anchor.get("href")
        if not href_value:
            continue
        href = str(href_value[0] if isinstance(href_value, list) else href_value)
        if ".pdf" not in href.lower():
            continue
        raw_text = extract_text(anchor)
        safe_text = sanitize_group(raw_text).upper().replace("\xa0", " ").replace("-", "")
        if (("ГРУПИ" in safe_text and clean_group in safe_text)
                or "ГРАФІК" in safe_text or "РОЗКЛАД" in safe_text):
            full_link = href if href.startswith("http") else f"https://tntu.edu.ua/{href}"
            if not any(pdf["url"] == full_link for pdf in pdf_links):
                pdf_links.append({"name": raw_text, "url": full_link})
            group_exists = True
    return group_exists, table if isinstance(table, Tag) else None, pdf_links, soup


def build_schedule_grid(table: Tag) -> tuple[list[Tag], Dict[Tuple[int, int], Tag]]:
    rows = [row for row in table.find_all("tr") if isinstance(row, Tag)]
    grid = {}
    for row_index, row in enumerate(rows):
        column_index = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            if not isinstance(cell, Tag):
                continue
            while (row_index, column_index) in grid:
                column_index += 1
            try:
                rowspan = int(str(cell.get("rowspan", 1)))
                colspan = int(str(cell.get("colspan", 1)))
            except ValueError:
                rowspan = colspan = 1
            for row_offset in range(rowspan):
                for column_offset in range(colspan):
                    grid[(row_index + row_offset, column_index + column_offset)] = cell
            column_index += colspan
    return rows, grid


def parse_location(location: str) -> tuple[Optional[str], Optional[str]]:
    match = re.fullmatch(
        r"([А-ЯІЇЄҐA-Z]+\d+)\s*[-–—]\s*(.+)", location.strip(), re.IGNORECASE
    )
    return (match.group(1), match.group(2)) if match else (None, None)


def parse_lesson_cell(cell: Tag, time_text: str) -> Optional[Dict[str, Any]]:
    subject_link = cell.find("a", href=True)
    subject_div = cell.find("div", attrs={"class": "Subject"})
    if isinstance(subject_link, Tag):
        subject = extract_text(subject_link)
    elif isinstance(subject_div, Tag):
        subject = extract_text(subject_div)
    else:
        clone = copy.deepcopy(cell)
        for detail in clone.find_all(
                ["div", "span"], attrs={"class": ["Info", "Notes", "LessonType"]}
        ):
            if isinstance(detail, Tag):
                detail.decompose()
        subject = extract_text(clone)
    if not subject or subject == "-":
        return None

    info = cell.find("div", attrs={"class": "Info"})
    info_parts = list(info.stripped_strings) if isinstance(info, Tag) else []
    lesson_type = str(info_parts[0]).strip().lower() if info_parts else None
    location = str(info_parts[-1]).strip() if len(info_parts) > 1 else None
    if location == lesson_type:
        location = None
    building, room = parse_location(location or "")
    notes_element = cell.find("div", attrs={"class": "Notes"})
    notes = extract_text(notes_element) if isinstance(notes_element, Tag) else None
    href = str(subject_link.get("href")) if isinstance(subject_link, Tag) else None
    atutor_url = normalize_atutor_url(urllib.parse.urljoin(TNTU_SCHEDULE_URL, href)) if href else None
    details = [value for value in (lesson_type, location) if value]
    full_name = subject + (f" ({', '.join(details)})" if details else "")
    if notes:
        full_name += f" ❗️{notes}"
    return {
        "time": time_text, "name": full_name, "subject": subject,
        "lesson_type": lesson_type, "location": location, "building": building,
        "room": room, "atutor_url": atutor_url, "notes": notes, "is_pdf": False,
    }


def table_snapshot(table: Tag) -> List[Dict[str, Any]]:
    rows, grid = build_schedule_grid(table)
    time_to_rows = {}
    for row_index in range(1, len(rows)):
        time_cell = grid.get((row_index, 0))
        if not isinstance(time_cell, Tag):
            continue
        data = time_to_rows.setdefault(id(time_cell), {"cell": time_cell, "indices": []})
        if row_index not in data["indices"]:
            data["indices"].append(row_index)

    snapshot = []
    seen_cells = set()
    for data in time_to_rows.values():
        time_cell = data["cell"]
        time_div = time_cell.find("div", attrs={"class": "LessonPeriod"})
        time_text = extract_text(time_div) if isinstance(time_div, Tag) else extract_text(time_cell)
        indices = data["indices"]
        for week in (1, 2):
            row_index = indices[min(week - 1, len(indices) - 1)]
            for weekday in range(5):
                cell = grid.get((row_index, weekday + 1))
                identity = (week, weekday, id(cell))
                if not isinstance(cell, Tag) or identity in seen_cells:
                    continue
                seen_cells.add(identity)
                lesson = parse_lesson_cell(cell, time_text)
                if lesson:
                    lesson.update({"week": week, "weekday": weekday})
                    snapshot.append(lesson)
    return snapshot
