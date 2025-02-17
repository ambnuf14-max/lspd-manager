import json
import traceback

import discord
from discord.ext import commands

from bot.database import setup_db
from models.roles_request import PersistentView, ButtonView

async def setup_on_ready(bot, ADM_ROLES_CH, CL_REQUEST_CH, GUILD):
    @bot.event
    async def on_ready():
        print("Бот запущен и готов к работе.")
        # await bot.tree.sync(guild=GUILD)
        # print("Синхронизация команд завершена.")

        adm_channel = bot.get_channel(ADM_ROLES_CH)
        if adm_channel is None:
            print(f"Канал с ID {ADM_ROLES_CH} не найден!")
            return

        client_channel = bot.get_channel(CL_REQUEST_CH)
        if client_channel is None:
            print(f"Канал с ID {CL_REQUEST_CH} не найден!")
            return

        print("Подключение к базе данных...")
        try:
            async with bot.db_pool.acquire() as conn:
                print("Установка кодировки UTF8...")
                await conn.execute("SET client_encoding = 'UTF8'")
                print("Выполнение SELECT запроса...")
                rows = await conn.fetch("SELECT message_id, user_id, embed FROM requests WHERE status = 'pending'")
                print(f"Запрос выполнен, получено {len(rows)} строк.")
        except Exception as e:
            print(f"Ошибка при работе с базой данных: {e}")
            traceback.print_exc()
            return

        print("Обработка запросов...")
        for row in rows:
            message_id = row["message_id"]
            user_id = row["user_id"]
            embed_data = row["embed"]

            try:
                user = await bot.fetch_user(user_id)
                embed = discord.Embed.from_dict(json.loads(embed_data))
            except Exception as e:
                print(f"Ошибка при создании embed или получении пользователя: {e}")
                traceback.print_exc()
                continue

            try:
                view = PersistentView(embed, user)
            except Exception as e:
                print(f"Ошибка при создании PersistentView: {e}")
                traceback.print_exc()
                continue

            try:
                message = await adm_channel.fetch_message(message_id)
                print(f"Найдено сообщение: {message.id}")
                await message.edit(embed=embed, view=view)
            except discord.NotFound:
                print(f"Сообщение с ID {message_id} не найдено.")
            except Exception as e:
                print(f"Ошибка при редактировании сообщения: {e}")
                traceback.print_exc()

        print("Обработка завершена.")

        try:
            view = ButtonView()
            await client_channel.send("Нажмите кнопку ниже, чтобы получить роли:", view=view)
        except Exception as e:
            print(f"Ошибка при отправке сообщения с кнопкой: {e}")
            traceback.print_exc()