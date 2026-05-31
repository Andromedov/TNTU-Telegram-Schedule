from ics import Calendar, Event
from datetime import datetime, timedelta
import pytz
import logging


def generate_week_ics(group_name: str, schedule_data: dict) -> str:
    """
    Генерує .ics файл для розкладу на тиждень.

    :param group_name: Назва групи
    :param schedule_data: Словник формату {datetime_obj: [{'time': '08:00-09:20', 'name': 'Предмет'}, ...]}
    :return: Шлях до згенерованого файлу (або вміст файлу у вигляді рядка)
    """
    cal = Calendar()
    timezone = pytz.timezone("Europe/Kyiv")

    for date_obj, classes in schedule_data.items():
        for item in classes:
            if item.get('is_pdf'):
                continue

            time_str = item.get('time', '')
            name = item.get('name', 'Пара')

            parts = time_str.split('-')
            try:
                start_time_str = parts[0].strip()
                start_hour, start_min = map(int, start_time_str.split(':'))

                start_datetime = timezone.localize(
                    datetime(date_obj.year, date_obj.month, date_obj.day, start_hour, start_min)
                )

                if len(parts) > 1:
                    end_time_str = parts[1].strip()
                    end_hour, end_min = map(int, end_time_str.split(':'))
                    end_datetime = timezone.localize(
                        datetime(date_obj.year, date_obj.month, date_obj.day, end_hour, end_min)
                    )
                else:
                    end_datetime = start_datetime + timedelta(hours=1, minutes=20)

                event = Event()
                event.name = name
                event.begin = start_datetime
                event.end = end_datetime
                event.location = "ТНТУ"
                event.description = f"Група: {group_name}\nЗгенеровано ботом @tntu_schedule_bot"

                cal.events.add(event)
            except Exception as e:
                logging.error(f"Помилка парсингу часу {time_str} для ICS: {e}")
                continue

    serialized = cal.serialize()
    return serialized.replace('\r\n', '\n').replace('\n', '\r\n') + '\r\n'
