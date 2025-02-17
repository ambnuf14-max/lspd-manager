import asyncio
import json
import traceback  # Для вывода полной информации об исключениях

import discord
from discord.ext import commands

from bot.config import TOKEN, GUILD, ADM_ROLES_CH, CL_REQUEST_CH
from bot.database import setup_db
from events.on_error import setup_on_error
from events.on_ready import setup_on_ready
from models.roles_request import PersistentView, ButtonView

intents = discord.Intents.all()  # Подключаем "Разрешения"
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

async def run_bot():
    await setup_db(bot)

    await setup_on_ready(bot, ADM_ROLES_CH, CL_REQUEST_CH, GUILD)
    await setup_on_error(bot)

    print("Запуск бота...")
    try:
        await bot.start(TOKEN)
    except Exception as e:
        print(f"Ошибка при запуске бота: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_bot())
