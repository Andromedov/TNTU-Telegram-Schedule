import asyncio
import logging
from aiogram import Bot, Dispatcher, Router
from config import BOT_TOKEN
from database import init_db
from handlers import ScheduleBotHandlers
from scheduler import setup_scheduler
from messages import get_msg
from http_client import http_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def main():
    # Ініціалізація БД
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    scheduler = None

    try:
        await http_client.start()
        scheduler = setup_scheduler(bot)

        main_router = Router()
        handlers = ScheduleBotHandlers(main_router, scheduler)
        dp.include_router(main_router)

        await bot.set_my_commands(handlers.get_bot_commands("uk"))
        await bot.set_my_commands(handlers.get_bot_commands("en"), language_code="en")

        scheduler.start()
        logging.info(get_msg("bot.started", "Бот запущено!"))
        await dp.start_polling(bot)
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
        await http_client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
