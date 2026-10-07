import logging
import uuid
from datetime import datetime, timedelta

import pytz

from schedule.formatting import lesson_plain_text


def _escape_text(value: str) -> str:
    """Екранує значення типу TEXT відповідно до RFC 5545."""
    return (
        str(value)
        .replace('\\', '\\\\')
        .replace('\r\n', '\\n')
        .replace('\n', '\\n')
        .replace('\r', '\\n')
        .replace(';', '\\;')
        .replace(',', '\\,')
    )


def _fold_line(line: str) -> list[str]:
    """Складає content line по 75 UTF-8 октетів без розриву Unicode-символів."""
    folded = []
    remaining = line
    byte_limit = 75

    while len(remaining.encode('utf-8')) > byte_limit:
        byte_count = 0
        split_at = 0
        for index, char in enumerate(remaining):
            char_size = len(char.encode('utf-8'))
            if byte_count + char_size > byte_limit:
                break
            byte_count += char_size
            split_at = index + 1
        folded.append(remaining[:split_at])
        remaining = " " + remaining[split_at:]
        byte_limit = 75

    folded.append(remaining)
    return folded


def generate_week_ics(group_name: str, schedule_data: dict) -> str:
    """
    Ручний генератор .ics файлу для розкладу на тиждень (без сторонніх бібліотек).

    :param group_name: Назва групи
    :param schedule_data: Словник формату {datetime_obj: [{'time': '08:00-09:20', 'name': 'Предмет'}, ...]}
    :return: Вміст .ics файлу у вигляді рядка
    """
    timezone = pytz.timezone("Europe/Kyiv")
    utc = pytz.UTC

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TNTU Schedule Bot//UK",
        "CALSCALE:GREGORIAN",
    ]

    for date_obj, classes in schedule_data.items():
        for item in classes:
            if item.get('is_pdf'):
                continue

            time_str = item.get('time', '')
            name = _escape_text(lesson_plain_text(item) or 'Пара')

            parts = time_str.split('-')
            try:
                start_time_str = parts[0].strip()
                start_hour, start_min = map(int, start_time_str.split(':'))

                start_dt = timezone.localize(
                    datetime(date_obj.year, date_obj.month, date_obj.day, start_hour, start_min)
                )

                if len(parts) > 1:
                    end_time_str = parts[1].strip()
                    end_hour, end_min = map(int, end_time_str.split(':'))
                    end_dt = timezone.localize(datetime(date_obj.year, date_obj.month, date_obj.day, end_hour, end_min))
                else:
                    end_dt = start_dt + timedelta(hours=1, minutes=20)

                start_utc = start_dt.astimezone(utc).strftime("%Y%m%dT%H%M%SZ")
                end_utc = end_dt.astimezone(utc).strftime("%Y%m%dT%H%M%SZ")
                now_utc = datetime.now(utc).strftime("%Y%m%dT%H%M%SZ")

                uid = f"{uuid.uuid4()}@tntu_schedule_bot"

                lines.append("BEGIN:VEVENT")
                lines.append(f"UID:{uid}")
                lines.append(f"DTSTAMP:{now_utc}")
                lines.append(f"DTSTART:{start_utc}")
                lines.append(f"DTEND:{end_utc}")
                lines.append("SUMMARY:" + name)
                lines.append("LOCATION:ТНТУ")
                description = _escape_text(f"Група: {group_name}\nЗгенеровано ботом @tntu_schedule_bot")
                lines.append(f"DESCRIPTION:{description}")
                lines.append("END:VEVENT")

            except Exception as e:
                logging.error(f"Помилка парсингу часу {time_str} для ICS: {e}")
                continue

    lines.append("END:VCALENDAR")
    folded_lines = [part for line in lines for part in _fold_line(line)]
    return "\r\n".join(folded_lines) + "\r\n"
