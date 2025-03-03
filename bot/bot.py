import asyncio
import os
import traceback  # Для вывода полной информации об исключениях

import discord
from discord.ext import commands

from bot.config import TOKEN, ADM_ROLES_CH, CL_REQUEST_CH, APPLICATION_ID
from bot.database import setup_db
from events.on_error import setup_on_error
from events.on_member_update import setup_on_member_update
from events.on_ready import setup_on_ready

activity = discord.Game(name="sa-es.su")
intents = discord.Intents.all()
bot = commands.Bot(
    command_prefix="!",
    intents=discord.Intents.all(),
    activity=activity,
    application_id=APPLICATION_ID,
)


async def load_extensions():
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and not filename.__contains__("__init__"):
            # cut off the .py from the file name
            await bot.load_extension(f"cogs.{filename[:-3]}")


async def main():
    await setup_db(bot)

    await setup_on_ready(bot, ADM_ROLES_CH, CL_REQUEST_CH)
    await setup_on_error(bot)
    await setup_on_member_update(bot)

    print("Загружаем коги.")
    await load_extensions()
    print("Коги загружены.")

    print("Запуск бота...")
    try:
        await bot.start(TOKEN)
    except Exception as e:
        print(f"Ошибка при запуске бота: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
