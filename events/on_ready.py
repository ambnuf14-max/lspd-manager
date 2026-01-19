import json
import traceback

import discord

from bot.config import ENABLE_GSHEETS
from models.roles_request import PersistentView, ButtonView

if ENABLE_GSHEETS:
    from events.update_gsheet import update_roles


async def setup_on_ready(bot, ADM_ROLES_CH, CL_REQUEST_CH):
    @bot.event
    async def on_ready():
        print("Бот запущен и готов к работе.")
        await initialize_channels(bot, ADM_ROLES_CH, CL_REQUEST_CH)

        # Обновление Google Sheets (если включено)
        if ENABLE_GSHEETS:
            await update_table(bot)

        await process_pending_requests(bot, ADM_ROLES_CH)
        await send_button_message(bot, CL_REQUEST_CH)

        # Централизованная синхронизация команд (один раз при старте)
        print("Синхронизация команд...")
        from bot.config import GUILD
        try:
            # Копируем глобальные команды в guild перед синхронизацией
            bot.tree.copy_global_to(guild=GUILD)
            synced = await bot.tree.sync(guild=GUILD)
            print(f"✅ Синхронизировано {len(synced)} команд для сервера {GUILD.id}")
        except Exception as e:
            print(f"❌ Ошибка синхронизации команд: {e}")


async def initialize_channels(bot, adm_channel_id, client_channel_id):
    """Инициализация каналов."""
    adm_channel = bot.get_channel(adm_channel_id)
    if adm_channel is None:
        raise ValueError(f"Админ канал с ID {adm_channel_id} не найден!")

    client_channel = bot.get_channel(client_channel_id)
    if client_channel is None:
        raise ValueError(f"Админ канал с ID {client_channel_id} не найден!")
    return adm_channel, client_channel


async def update_table(bot):
    """Обновление комментариев в таблице."""
    print("Обновляем роли в таблице.")
    await update_roles(bot)
    print("Завершили обновление ролей.")


async def process_pending_requests(bot, adm_channel_id):
    """Обработка ожидающих запросов."""
    print("Подключение к базе данных...")
    try:
        async with bot.db_pool.acquire() as conn:
            await setup_utf(conn)
            rows = await fetch_pending_requests(conn)
            print(f"Запрос выполнен, получено {len(rows)} строк.")
            await handle_pending_requests(bot, adm_channel_id, rows)
    except Exception as e:
        print(f"Ошибка при работе с базой данных: {e}")
        traceback.print_exc()


async def setup_utf(conn):
    """Настройка подключения к базе данных."""
    print("Установка кодировки UTF8...")
    await conn.execute("SET client_encoding = 'UTF8'")


async def fetch_pending_requests(conn):
    """Получение ожидающих запросов из базы данных."""
    return await conn.fetch(
        "SELECT message_id, user_id, embed FROM requests WHERE status = 'pending'"
    )


async def handle_pending_requests(bot, adm_channel_id, rows):
    """Обработка каждого ожидающего запроса."""
    print("Обработка запросов...")
    adm_channel = bot.get_channel(adm_channel_id)
    for row in rows:
        await process_single_request(bot, adm_channel, row)
    print("Обработка завершена.")


async def process_single_request(bot, adm_channel, row):
    """Обработка одного запроса."""
    message_id = row["message_id"]
    user_id = row["user_id"]
    embed_data = row["embed"]

    try:
        user = await bot.fetch_user(user_id)
        embed = discord.Embed.from_dict(json.loads(embed_data))
        view = PersistentView(embed, user, bot)
        await view.load_presets()  # Загрузить пресеты ПЕРЕД обновлением сообщения
        await update_message(adm_channel, message_id, embed, view)
    except Exception as e:
        print(f"Ошибка при обработке запроса: {e}")
        traceback.print_exc()


async def update_message(adm_channel, message_id, embed, view):
    """Обновление сообщения в канале."""
    try:
        message = await adm_channel.fetch_message(message_id)
        print(f"Найдено сообщение: {message.id}")
        await message.edit(embed=embed, view=view)
    except discord.NotFound:
        print(f"Сообщение с ID {message_id} не найдено.")
    except Exception as e:
        print(f"Ошибка при редактировании сообщения: {e}")
        traceback.print_exc()


async def send_button_message(bot, client_channel_id):
    """Отправка сообщения с кнопкой в клиентский канал или восстановление существующего."""
    client_channel = bot.get_channel(client_channel_id)
    try:
        view = ButtonView(bot)

        # Проверяем последние сообщения на наличие кнопки от бота
        async for message in client_channel.history(limit=50):
            if message.author == bot.user and message.components:
                # Найдено существующее сообщение с компонентами - восстанавливаем view
                print(f"Найдено существующее сообщение с кнопкой (ID: {message.id}), восстанавливаем view")
                bot.add_view(view, message_id=message.id)
                return

        # Если не найдено - отправляем новое сообщение
        await client_channel.send(
            "Нажмите кнопку ниже, чтобы получить роли:", view=view
        )
        print("Отправлено новое сообщение с кнопкой")
    except Exception as e:
        print(f"Ошибка при отправке сообщения с кнопкой: {e}")
        traceback.print_exc()
