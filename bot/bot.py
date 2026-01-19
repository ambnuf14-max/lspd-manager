import asyncio
import os
import traceback  # Для вывода полной информации об исключениях

import discord
from discord.ext import commands

from bot.config import (
    TOKEN,
    ADM_ROLES_CH,
    CL_REQUEST_CH,
    APPLICATION_ID,
    BOT_ACTIVITY_NAME,
    COMMAND_PREFIX,
    ENABLE_GSHEETS,
    ENABLE_FTO_AUTO_MESSAGE
)
from bot.database import setup_db
from bot.logger import get_logger
from events.on_error import setup_on_error
from events.on_member_update import setup_on_member_update
from events.on_ready import setup_on_ready

logger = get_logger('main')

activity = discord.Game(name=BOT_ACTIVITY_NAME)
intents = discord.Intents.all()
bot = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    intents=discord.Intents.all(),
    activity=activity,
    application_id=APPLICATION_ID,
)


async def load_extensions():
    """Загрузка cog'ов с учетом конфигурации"""
    # Список отключенных модулей
    disabled_cogs = []

    if not ENABLE_GSHEETS:
        disabled_cogs.append("gsheets")
        logger.info("Google Sheets модуль отключен (ENABLE_GSHEETS=false)")

    if not ENABLE_FTO_AUTO_MESSAGE:
        disabled_cogs.append("fto")
        logger.info("FTO модуль отключен (ENABLE_FTO_AUTO_MESSAGE=false)")

    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and not filename.__contains__("__init__"):
            cog_name = filename[:-3]

            # Пропускаем отключенные модули
            if cog_name in disabled_cogs:
                logger.info(f"Пропускаем {cog_name}.py (модуль отключен)")
                continue

            await bot.load_extension(f"cogs.{cog_name}")
            logger.info(f"Загружен cog: {cog_name}.py")


async def main():
    await setup_db(bot)

    await setup_on_ready(bot, ADM_ROLES_CH, CL_REQUEST_CH)
    await setup_on_error(bot)
    await setup_on_member_update(bot)

    logger.info("Загружаем коги...")
    await load_extensions()
    logger.info("Коги загружены")

    logger.info("Запуск бота...")
    try:
        await bot.start(TOKEN)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
