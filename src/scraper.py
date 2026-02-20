import aiohttp
from bs4 import BeautifulSoup
import logging

TNTU_SCHEDULE_URL = "https://tntu.edu.ua/?p=uk/schedule&s=fis-sts21"


async def fetch_schedule_html(group_name: str) -> str:
    """Асинхронно завантажує сторінку розкладу для певної групи."""
    try:
        params = {'group': group_name}
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
    schedule = []

    # TODO: add scraper logic here

    return schedule


async def check_schedule_changes() -> bool:
    """
    Функція для перевірки, чи змінився розклад.
    Реалізація: зберегти хеш або текст вчорашнього розкладу в БД або файл,
    спарсити сьогоднішній і порівняти їх.
    """
    # Логіка порівняння
    return False