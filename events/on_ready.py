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

        # Восстановление views (без API запросов на редактирование)
        await restore_pending_views(bot, ADM_ROLES_CH)
        await restore_button_view(bot, CL_REQUEST_CH)

        # Централизованная синхронизация команд (один раз при старте)
        print("Синхронизация команд...")
        from bot.config import GUILD
        try:
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
        raise ValueError(f"Клиентский канал с ID {client_channel_id} не найден!")
    return adm_channel, client_channel


async def update_table(bot):
    """Обновление комментариев в таблице."""
    print("Обновляем роли в таблице.")
    await update_roles(bot)
    print("Завершили обновление ролей.")


async def restore_pending_views(bot, adm_channel_id):
    """Восстановление views для pending запросов (без редактирования сообщений)."""
    print("Восстановление views для pending запросов...")

    try:
        async with bot.db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT message_id, user_id, embed FROM requests WHERE status = 'pending'"
            )

        if not rows:
            print("Нет pending запросов для восстановления.")
            return

        adm_channel = bot.get_channel(adm_channel_id)
        restored = 0

        for row in rows:
            try:
                user = await bot.fetch_user(row["user_id"])
                embed = discord.Embed.from_dict(json.loads(row["embed"]))
                view = PersistentView(embed, user, bot, adm_channel.guild)
                await view.load_presets()

                # Регистрируем view без редактирования сообщения
                bot.add_view(view, message_id=row["message_id"])
                restored += 1
            except discord.NotFound:
                print(f"Пользователь {row['user_id']} не найден, пропускаем")
            except Exception as e:
                print(f"Ошибка восстановления view для сообщения {row['message_id']}: {e}")

        print(f"✅ Восстановлено {restored} views для pending запросов")

    except Exception as e:
        print(f"Ошибка при восстановлении views: {e}")
        traceback.print_exc()


async def restore_button_view(bot, client_channel_id):
    """Восстановление кнопки 'Запросить роли'."""
    print("Восстановление кнопки 'Запросить роли'...")

    try:
        # Регистрируем persistent view глобально (работает для любого сообщения с этим custom_id)
        view = ButtonView(bot)
        bot.add_view(view)
        print("✅ Кнопка 'Запросить роли' восстановлена")
    except Exception as e:
        print(f"Ошибка при восстановлении кнопки: {e}")
        traceback.print_exc()
