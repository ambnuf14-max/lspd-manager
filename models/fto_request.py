import traceback
from datetime import datetime

import discord
from discord.ext import tasks

from bot.config import FTO_ROLE_NAME, INTERN_ROLE_NAME, FTO_QUEUE_CLEANUP_HOURS, FTO_QUEUE_CHECK_MINUTES
from bot.logger import get_logger

logger = get_logger('fto')


class FTOView(discord.ui.View):
    def __init__(self, bot, channel_id=None, message_id=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.channel_id = channel_id
        self.message_id = message_id
        self.cleanup_task.start()
        self.add_item(EnterQueue(self))
        self.add_item(LeaveButton(self))

    @tasks.loop(minutes=FTO_QUEUE_CHECK_MINUTES)
    async def cleanup_task(self):
        """Очистка устаревших записей из очереди."""
        try:
            async with self.bot.db_pool.acquire() as conn:
                expired_entries = await self.fetch_expired_entries(conn)
                for entry in expired_entries:
                    await self.process_expired_entry(conn, entry)
                if expired_entries:
                    logger.info(f"Очищено {len(expired_entries)} устаревших записей из очереди FTO")
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче cleanup: {e}", exc_info=True)

    @staticmethod
    async def fetch_expired_entries(conn):
        """Получение устаревших записей из базы данных."""
        return await conn.fetch(
            "SELECT * FROM queue WHERE finished_at IS NULL AND created_at < NOW() - make_interval(hours => $1)",
            FTO_QUEUE_CLEANUP_HOURS
        )

    async def process_expired_entry(self, conn, entry):
        """Обработка одной устаревшей записи."""
        await self.mark_entry_as_finished(conn, entry)
        await self.update_embed_for_expired_entry(entry)
        await self.notify_user_about_expiration(entry)

    async def update_embed_for_expired_entry(self, entry):
        """Обновление Embed для устаревшей записи."""
        if not self.channel_id or not self.message_id:
            logger.warning("channel_id или message_id не установлены, пропускаем обновление embed")
            return

        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            logger.warning(f"Канал {self.channel_id} не найден")
            return

        try:
            message = await channel.fetch_message(self.message_id)
            embed = message.embeds[0] if message.embeds else None

            if embed:
                field_name = (
                    "Свободные FTO" if entry["officer_id"] else "Стажеры в очереди"
                )
                await self.remove_user_from_embed(
                    embed, entry["display_name"], field_name
                )
                await message.edit(embed=embed)
        except discord.NotFound:
            logger.warning(f"Сообщение {self.message_id} не найдено.")
        except discord.Forbidden:
            logger.error(f"Нет прав для редактирования сообщения {self.message_id}.")
        except Exception as e:
            logger.error(f"Ошибка при обновлении сообщения: {e}")

    async def notify_user_about_expiration(self, entry):
        """Уведомление пользователя об истечении времени в очереди."""
        user_id = (
            entry["officer_id"] if entry["officer_id"] else entry["probationary_id"]
        )
        user = self.bot.get_user(user_id)
        if not user:
            return

        try:
            await user.send(
                "❌ Вы были удалены из очереди, так как никто не нашёлся за 3 часа."
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

    @staticmethod
    async def mark_entry_as_finished(conn, entry):
        """Помечаем запись как завершённую в базе данных."""
        await conn.execute(
            "UPDATE queue SET finished_at = NOW() WHERE queue_id = $1",
            entry["queue_id"],
        )

    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        await self.bot.wait_until_ready()  # Ждём, пока бот будет готов

    @staticmethod
    async def remove_user_from_embed(
        embed: discord.Embed, user_name: str, field_name: str
    ):
        """
        Удаляет пользователя из указанного поля Embed.
        """
        for field in embed.fields:
            if field.name == field_name:
                names = [
                    name.strip() for name in field.value.split("\n") if name.strip()
                ]
                if user_name in names:
                    names.remove(user_name)
                    new_value = (
                        "\n".join(names)
                        if names
                        else (
                            "Нет FTO"
                            if field_name == "Свободные FTO"
                            else "Нет стажеров в очереди"
                        )
                    )
                    embed.set_field_at(
                        embed.fields.index(field),
                        name=field_name,
                        value=new_value,
                        inline=False,
                    )


# noinspection PyUnresolvedReferences
class EnterQueue(discord.ui.Button):
    def __init__(self, fto_view: FTOView):
        super().__init__(
            label="Войти в очередь",
            custom_id="enter_queue",
            style=discord.ButtonStyle.green,
        )
        self.fto_view = fto_view

    async def callback(self, interaction: discord.Interaction):
        try:
            embed = (
                interaction.message.embeds[0]
                if interaction.message.embeds
                else discord.Embed()
            )

            # Сохраняем channel_id и message_id в FTOView для cleanup_task
            self.fto_view.channel_id = interaction.channel.id
            self.fto_view.message_id = interaction.message.id

            fto_role = discord.utils.find(
                lambda r: r.name == FTO_ROLE_NAME,
                interaction.guild.roles,
            )
            intern_role = discord.utils.find(
                lambda r: r.name == INTERN_ROLE_NAME, interaction.guild.roles
            )

            if (
                fto_role not in interaction.user.roles
                and intern_role not in interaction.user.roles
            ):
                await interaction.response.send_message(
                    "❌ Вы не являетесь офицером полевой подготовки либо стажером.",
                    ephemeral=True,
                )
                return

            async with interaction.client.db_pool.acquire() as conn:
                existing_entry = await conn.fetch(
                    "SELECT * FROM queue WHERE (probationary_id = $1 OR officer_id = $2) AND finished_at IS NULL",
                    interaction.user.id,
                    interaction.user.id,
                )

            if existing_entry:
                await interaction.response.send_message(
                    "❌ Вы уже в очереди.", ephemeral=True
                )
                return

            result = None
            async with interaction.client.db_pool.acquire() as conn:
                if fto_role in interaction.user.roles:
                    result = await conn.fetchrow(
                        "INSERT INTO queue (officer_id, created_at, display_name) VALUES ($1, $2, $3) RETURNING "
                        "queue_id",
                        interaction.user.id,
                        datetime.now(),
                        interaction.user.display_name,
                    )
                    logger.info(f"FTO {interaction.user.display_name} добавлен в очередь, queue_id={result['queue_id']}")
                    field_name = "Свободные FTO"

                elif intern_role in interaction.user.roles:
                    result = await conn.fetchrow(
                        "INSERT INTO queue (probationary_id, created_at, display_name) VALUES ($1, $2, $3) RETURNING "
                        "queue_id",
                        interaction.user.id,
                        datetime.now(),
                        interaction.user.display_name,
                    )
                    logger.info(f"Стажёр {interaction.user.display_name} добавлен в очередь, queue_id={result['queue_id']}")
                    field_name = "Стажеры в очереди"

            if fto_role in interaction.user.roles:
                await self.check_and_pair_fto(interaction, result["queue_id"], embed)
            else:
                await self.check_and_pair_intern(interaction, result["queue_id"], embed)

            await self.update_embed_field(
                embed, field_name, interaction.user.display_name
            )
            await interaction.response.edit_message(embed=embed)
            await interaction.followup.send(
                "✅ Вы вошли в очередь. Учтите, ваша позиция действительна 3 часа.",
                ephemeral=True,
            )

        except Exception as e:
            await interaction.response.send_message(
                "❌ Произошла ошибка. Обратитесь к администратору.", ephemeral=True
            )
            logger.error(f"Ошибка в модуле FTO при входе в очередь: {e}", exc_info=True)

    @staticmethod
    async def update_embed_field(embed: discord.Embed, field_name: str, value: str):
        """
        Обновляет поле в embed. Если поле существует, добавляет значение к текущему.
        Если поле не существует, создаёт новое поле.
        """
        existing_field = next(
            (field for field in embed.fields if field.name == field_name), None
        )

        if existing_field:
            if (
                existing_field.value == "Нет FTO"
                or existing_field.value == "Нет стажеров в очереди"
            ):
                new_value = f"\n{value}"
            else:
                new_value = f"{existing_field.value}\n{value}"
            return embed.set_field_at(
                embed.fields.index(existing_field),
                name=field_name,
                value=new_value,
                inline=False,
            )
        else:
            embed.add_field(name=field_name, value=value, inline=False)

    async def check_and_pair_fto(self, interaction, queue_id, embed):
        """Проверяет наличие стажёра для FTO"""
        try:
            logger.info(f"Проверяем наличие стажёра для FTO {interaction.user.display_name}...")
            async with interaction.client.db_pool.acquire() as conn:
                # Используем транзакцию с SELECT FOR UPDATE для предотвращения race condition
                async with conn.transaction():
                    intern_entry = await conn.fetchrow(
                        "SELECT * FROM queue WHERE probationary_id IS NOT NULL AND finished_at IS NULL "
                        "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                    )

                    if intern_entry:
                        logger.info(f"Найден стажёр: {intern_entry['display_name']}")
                        # Завершаем обе записи в рамках одной транзакции
                        await conn.execute(
                            "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                            datetime.now(),
                            queue_id,
                        )
                        await conn.execute(
                            "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                            datetime.now(),
                            intern_entry["queue_id"],
                        )
                    else:
                        logger.info("Свободных стажёров не найдено")
                        return  # Нет стажёров - выходим

            if intern_entry:

                await self.remove_user_from_embed(
                    embed, interaction.user.display_name, "Свободные FTO"
                )
                await self.remove_user_from_embed(
                    embed, intern_entry["display_name"], "Стажеры в очереди"
                )

                intern_user = interaction.guild.get_member(
                    intern_entry["probationary_id"]
                )
                if intern_user:
                    try:
                        await intern_user.send(
                            f"🎉 Вы нашли FTO: <@{interaction.user.id}> ({interaction.user.display_name})!"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление стажёру: {e}")

                try:
                    await interaction.user.send(
                        f"🎉 Вы нашли стажёра: <@{intern_entry['probationary_id']}> ({intern_entry['display_name']})!"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление FTO: {e}")
                await interaction.response.edit_message(embed=embed)

        except Exception as e:
            logger.error(f"Ошибка при проверке наличия стажёра для FTO: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ Произошла ошибка. Обратитесь к администратору.",
                ephemeral=True,
            )

    async def check_and_pair_intern(self, interaction, queue_id, embed):
        """Проверяет наличие FTO для стажёра"""
        try:
            logger.info(f"Проверяем наличие FTO для стажёра {interaction.user.display_name}...")
            async with interaction.client.db_pool.acquire() as conn:
                # Используем транзакцию с SELECT FOR UPDATE для предотвращения race condition
                async with conn.transaction():
                    fto_entry = await conn.fetchrow(
                        "SELECT * FROM queue WHERE officer_id IS NOT NULL AND finished_at IS NULL "
                        "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                    )

                    if fto_entry:
                        logger.info(f"Найден FTO: {fto_entry['display_name']}")
                        # Завершаем обе записи в рамках одной транзакции
                        await conn.execute(
                            "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                            datetime.now(),
                            queue_id,
                        )
                        await conn.execute(
                            "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                            datetime.now(),
                            fto_entry["queue_id"],
                        )
                    else:
                        logger.info("Свободных FTO не найдено")
                        return  # Нет FTO - выходим

            if fto_entry:
                await self.remove_user_from_embed(
                    embed, interaction.user.display_name, "Стажеры в очереди"
                )
                await self.remove_user_from_embed(
                    embed, fto_entry["display_name"], "Свободные FTO"
                )

                fto_user = interaction.guild.get_member(fto_entry["officer_id"])
                if fto_user:
                    try:
                        await fto_user.send(
                            f"🎉 Вы нашли стажёра: <@{interaction.user.id}> ({interaction.user.display_name})!"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление FTO: {e}")

                try:
                    await interaction.user.send(
                        f"🎉 Вы нашли FTO: <@{fto_entry['officer_id']}> ({fto_entry['display_name']})!"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление стажёру: {e}")

                await interaction.response.edit_message(embed=embed)

        except Exception as e:
            logger.error(f"Ошибка при проверке наличия FTO для стажёра: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ Произошла ошибка. Обратитесь к администратору.", ephemeral=True
            )

    @staticmethod
    async def remove_user_from_embed(
        embed: discord.Embed, user_name: str, field_name: str
    ):
        """Удаляет пользователя из указанного поля Embed"""
        for field in embed.fields:
            if field.name == field_name:
                names = [
                    name.strip() for name in field.value.split("\n") if name.strip()
                ]
                if user_name in names:
                    names.remove(user_name)
                    new_value = (
                        "\n".join(names)
                        if names
                        else (
                            "Нет FTO"
                            if field_name == "Свободные FTO"
                            else "Нет стажеров в очереди"
                        )
                    )
                    embed.set_field_at(
                        embed.fields.index(field),
                        name=field_name,
                        value=new_value,
                        inline=False,
                    )


# noinspection PyUnresolvedReferences
class LeaveButton(discord.ui.Button):
    def __init__(self, fto_view: FTOView):
        super().__init__(
            label="Выйти с очереди",
            custom_id="leave_queue",
            style=discord.ButtonStyle.red,
        )
        self.fto_view = fto_view

    async def callback(self, interaction: discord.Interaction):
        try:
            embed = (
                interaction.message.embeds[0]
                if interaction.message.embeds
                else discord.Embed()
            )
            async with interaction.client.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT queue_id FROM queue "
                    "WHERE (probationary_id = $1 OR officer_id = $2) AND finished_at IS NULL",
                    interaction.user.id,
                    interaction.user.id,
                )

            if not rows:
                await interaction.response.send_message(
                    "❌ Вы не в очереди.", ephemeral=True
                )
                return

            for row in rows:
                queue_id = row["queue_id"]
                async with interaction.client.db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                        datetime.now(),
                        queue_id,
                    )

                await self.remove_user_from_embed(embed, interaction.user.display_name)

            await interaction.response.edit_message(embed=embed)
            await interaction.followup.send("👌 Вы покинули очередь.", ephemeral=True)

        except Exception as e:
            logger.error(f"Ошибка при выходе из очереди: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ Ошибка при обработке запроса.", ephemeral=True
            )

    @staticmethod
    async def remove_user_from_embed(embed: discord.Embed, user_name: str):
        """
        Удаляет пользователя из всех полей embed.
        """
        for field in embed.fields:
            if user_name in field.value:
                names = [
                    name.strip() for name in field.value.split("\n") if name.strip()
                ]
                if user_name in names:
                    names.remove(user_name)
                    new_value = (
                        "\n".join(names)
                        if names
                        else (
                            "Нет FTO"
                            if field.name == "Свободные FTO"
                            else "Нет стажеров в очереди"
                        )
                    )
                    embed.set_field_at(
                        embed.fields.index(field),
                        name=field.name,
                        value=new_value,
                        inline=False,
                    )
        # await interaction.response.send_message(f"Вы покинули очередь")
