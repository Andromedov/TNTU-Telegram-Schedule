import asyncio
import logging
from aiogram import Bot, Dispatcher, Router
from config import BOT_TOKEN
from database import init_db
from handlers import ScheduleBotHandlers
from scheduler import setup_scheduler
from messages import get_msg

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def main():
    # Ініціалізація БД
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    main_router = Router()
    handlers = ScheduleBotHandlers(main_router)

    dp.include_router(main_router)

    await bot.set_my_commands(handlers.get_bot_commands())

    scheduler = setup_scheduler(bot)
    scheduler.start()

    logging.info(get_msg("bot.started", "Бот запущено!"))

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())