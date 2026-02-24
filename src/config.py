import os
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не знайдено BOT_TOKEN у файлі .env")
SENIOR_ID = os.getenv("SENIOR_ID")
if SENIOR_ID:
    try:
        SENIOR_ID = int(SENIOR_ID)
    except ValueError:
        SENIOR_ID = None

DB_PATH = "data/users.sqlite3"