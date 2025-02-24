import traceback
from datetime import datetime

import discord
from discord.ext import tasks

channel_id = None
message_id = None

class FTOView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.cleanup_task.start()
        self.add_item(EnterQueue())
        self.add_item(LeaveButton())

    @tasks.loop(minutes=1)  # Задача запускается каждую минуту
    async def cleanup_task(self):
        try:
            async with self.bot.db_pool.acquire() as conn:
                # Находим записи, которые находятся в очереди дольше 3 часов
                expired_entries = await conn.fetch(
                    "SELECT * FROM queue WHERE finished_at IS NULL AND created_at < NOW() - INTERVAL '3 hours'"
                )

                for entry in expired_entries:
                    # Удаляем запись из очереди
                    await conn.execute(
                        "UPDATE queue SET finished_at = NOW() WHERE queue_id = $1",
                        entry['queue_id']
                    )

                    # Получаем канал и сообщение
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            message = await channel.fetch_message(message_id)
                            embed = message.embeds[0] if message.embeds else None

                            if embed:
                                # Удаляем пользователя из Embed
                                field_name = "Свободные FTO" if entry['officer_id'] else "Стажеры в очереди"
                                await self.remove_user_from_embed(embed, entry['display_name'], field_name)

                                # Обновляем сообщение
                                await message.edit(embed=embed)
                        except discord.NotFound:
                            print(f"Сообщение {entry['message_id']} не найдено.")
                        except discord.Forbidden:
                            print(f"Нет прав для редактирования сообщения {entry['message_id']}.")
                        except Exception as e:
                            print(f"Ошибка при обновлении сообщения: {e}")
                    else:
                        print(f"Канал {channel_id} не найден")
                    # Отправляем уведомление пользователю
                    user_id = entry['officer_id'] if entry['officer_id'] else entry['probationary_id']
                    user = self.bot.get_user(user_id)
                    if user:
                        try:
                            await user.send("❌ Вы были удалены из очереди, так как никто не нашёлся за 3 часа.")
                        except:
                            pass  # Пользователь закрыл ЛС для бота

        except Exception as e:
            print(f"Ошибка в фоновой задаче: {e}")
            traceback.print_exc()

    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        await self.bot.wait_until_ready()  # Ждём, пока бот будет готов

    async def remove_user_from_embed(self, embed: discord.Embed, user_name: str, field_name: str):
        """
        Удаляет пользователя из указанного поля Embed.
        """
        for field in embed.fields:
            if field.name == field_name:
                names = [name.strip() for name in field.value.split('\n') if name.strip()]
                if user_name in names:
                    names.remove(user_name)
                    new_value = '\n'.join(names) if names else (
                        "Нет FTO" if field_name == "Свободные FTO"
                        else "Нет стажеров в очереди"
                    )
                    embed.set_field_at(
                        embed.fields.index(field),
                        name=field_name,
                        value=new_value,
                        inline=False
                    )


class EnterQueue(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Войти в очередь", custom_id="enter_queue", style=discord.ButtonStyle.green)

    async def callback(self, interaction: discord.Interaction):
        try:
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()

            global channel_id, message_id
            channel_id = interaction.channel.id
            message_id = interaction.message.id

            fto_role = discord.utils.find(lambda r: r.name == "FTO Officer", interaction.guild.roles)
            intern_role = discord.utils.find(lambda r: r.name == "Probationary Officer", interaction.guild.roles)

            if fto_role not in interaction.user.roles and intern_role not in interaction.user.roles:
                await interaction.response.send_message("❌ Вы не являетесь офицером полевой подготовки либо стажером.",
                                                        ephemeral=True)
                return

            async with interaction.client.db_pool.acquire() as conn:
                existing_entry = await conn.fetch(
                    "SELECT * FROM queue WHERE (probationary_id = $1 OR officer_id = $2) AND finished_at IS NULL",
                    interaction.user.id, interaction.user.id
                )

            if existing_entry:
                await interaction.response.send_message("❌ Вы уже в очереди.", ephemeral=True)
                return

            result = None
            async with interaction.client.db_pool.acquire() as conn:
                if fto_role in interaction.user.roles:
                    result = await conn.fetchrow(
                        "INSERT INTO queue (officer_id, created_at, display_name) VALUES ($1, $2, $3) RETURNING "
                        "queue_id",
                        interaction.user.id, datetime.now(), interaction.user.display_name
                    )
                    print(result)
                    field_name = "Свободные FTO"

                elif intern_role in interaction.user.roles:
                    result = await conn.fetchrow(
                        "INSERT INTO queue (probationary_id, created_at, display_name) VALUES ($1, $2, $3) RETURNING "
                        "queue_id",
                        interaction.user.id, datetime.now(), interaction.user.display_name
                    )
                    print(result)
                    field_name = "Стажеры в очереди"
            print(result)

            if fto_role in interaction.user.roles:
                await self.check_and_pair_fto(interaction, result['queue_id'], embed)
            else:
                await self.check_and_pair_intern(interaction, result['queue_id'], embed)

            await self.update_embed_field(embed, field_name, interaction.user.display_name)
            await interaction.response.edit_message(embed=embed)
            await interaction.followup.send("✅ Вы вошли в очередь. Учтите, ваша позиция действительна 3 часа.",
                                            ephemeral=True)



        except Exception as e:
            await interaction.response.send_message(f"❌ Произошла ошибка. Обратитесь к администратору.", ephemeral=True)
            traceback.print_exc()  # Логируем ошибку

    async def update_embed_field(self, embed: discord.Embed, field_name: str, value: str):
        """
        Обновляет поле в embed. Если поле существует, добавляет значение к текущему.
        Если поле не существует, создаёт новое поле.
        """
        existing_field = next((field for field in embed.fields if field.name == field_name), None)

        if existing_field:
            if existing_field.value == "Нет FTO" or existing_field.value == "Нет стажеров в очереди":
                new_value = f"\n{value}"
            else:
                new_value = f"{existing_field.value}\n{value}"
            return embed.set_field_at(embed.fields.index(existing_field), name=field_name, value=new_value,
                                      inline=False)
        else:
            embed.add_field(name=field_name, value=value, inline=False)

    async def check_and_pair_fto(self, interaction, queue_id, embed):
        """Проверяет наличие стажёра для FTO"""
        try:
            print("Проверяем наличие стажёра для FTO...")
            async with interaction.client.db_pool.acquire() as conn:
                intern_entry = await conn.fetchrow(
                    "SELECT * FROM queue WHERE probationary_id IS NOT NULL AND finished_at IS NULL ORDER BY created_at "
                    "LIMIT 1 "
                )
            print("Результат запроса:", intern_entry)
            if intern_entry:
                async with interaction.client.db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                        datetime.now(), queue_id
                    )
                    await conn.execute(
                        "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                        datetime.now(), intern_entry['queue_id']
                    )

                await self.remove_user_from_embed(embed, interaction.user.display_name, "Свободные FTO")
                await self.remove_user_from_embed(embed, intern_entry['display_name'], "Стажеры в очереди")

                intern_user = interaction.guild.get_member(intern_entry['probationary_id'])
                if intern_user:
                    try:
                        await intern_user.send(
                            f"🎉 Вы нашли FTO: <@{interaction.user.id}> ({interaction.user.display_name})!")
                    except:
                        pass

                try:
                    await interaction.user.send(
                        f"🎉 Вы нашли стажёра: <@{intern_entry['probationary_id']}> ({intern_entry['display_name']})!")
                except:
                    pass
                await interaction.response.edit_message(embed=embed)

                # await interaction.edit_original_response(embed=embed)
        except Exception as e:
            traceback.print_exc()  # Логируем ошибку
            await interaction.response.send_message(f"❌ Произошла ошибка. Обратитесь к администратору.", ephemeral=True)

    async def check_and_pair_intern(self, interaction, queue_id, embed):
        """Проверяет наличие FTO для стажёра"""
        try:
            print("Проверяем наличие FTO для стажёра...")
            async with interaction.client.db_pool.acquire() as conn:
                fto_entry = await conn.fetchrow(
                    "SELECT * FROM queue WHERE officer_id IS NOT NULL AND finished_at IS NULL ORDER BY created_at "
                    "LIMIT 1 "
                )
            print("Результат запроса:", fto_entry)
            if fto_entry:
                async with interaction.client.db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                        datetime.now(), queue_id
                    )
                    await conn.execute(
                        "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                        datetime.now(), fto_entry['queue_id']
                    )

                await self.remove_user_from_embed(embed, interaction.user.display_name, "Стажеры в очереди")
                await self.remove_user_from_embed(embed, fto_entry['display_name'], "Свободные FTO")

                fto_user = interaction.guild.get_member(fto_entry['officer_id'])
                if fto_user:
                    try:
                        await fto_user.send(f"🎉 Вы нашли стажёра: {interaction.user.display_name}!")
                    except:
                        pass

                try:
                    await interaction.user.send(f"🎉 Вы нашли FTO: {fto_entry['display_name']}!")
                except:
                    pass

                await interaction.response.edit_message(embed=embed)
                # await interaction.edit_original_response(embed=embed)
        except Exception as e:
            traceback.print_exc()  # Логируем ошибку
            await interaction.response.send_message(f"❌ Произошла ошибка. Обратитесь к администратору.", ephemeral=True)

    async def remove_user_from_embed(self, embed: discord.Embed, user_name: str, field_name: str):
        """Удаляет пользователя из указанного поля Embed"""
        for field in embed.fields:
            if field.name == field_name:
                names = [name.strip() for name in field.value.split('\n') if name.strip()]
                if user_name in names:
                    names.remove(user_name)
                    new_value = '\n'.join(names) if names else (
                        "Нет FTO" if field_name == "Свободные FTO"
                        else "Нет стажеров в очереди"
                    )
                    embed.set_field_at(
                        embed.fields.index(field),
                        name=field_name,
                        value=new_value,
                        inline=False
                    )


class LeaveButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Выйти с очереди", custom_id="leave_queue", style=discord.ButtonStyle.red)

    async def callback(self, interaction: discord.Interaction):
        try:
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            async with interaction.client.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT queue_id FROM queue "
                    "WHERE (probationary_id = $1 OR officer_id = $2) AND finished_at IS NULL",
                    interaction.user.id, interaction.user.id
                )

            if not rows:
                await interaction.response.send_message("❌ Вы не в очереди.", ephemeral=True)
                return

            for row in rows:
                queue_id = row["queue_id"]
                async with interaction.client.db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE queue SET finished_at = $1 WHERE queue_id = $2",
                        datetime.now(),
                        queue_id
                    )

                await self.remove_user_from_embed(embed, interaction.user.display_name)

            await interaction.response.edit_message(embed=embed)
            await interaction.followup.send("👌 Вы покинули очередь.", ephemeral=True)

        except Exception as e:
            error_message = "❌ Ошибка при обработке запроса."
            await interaction.response.send_message(error_message, ephemeral=True)
            traceback.print_exception(type(e), e, e.__traceback__)  # Логируем ошибку

    async def remove_user_from_embed(self, embed: discord.Embed, user_name: str):
        """
        Удаляет пользователя из всех полей embed.
        """
        for field in embed.fields:
            if user_name in field.value:
                names = [name.strip() for name in field.value.split('\n') if name.strip()]
                if user_name in names:
                    names.remove(user_name)
                    new_value = '\n'.join(names) if names else (
                        "Нет FTO" if field.name == "Свободные FTO"
                        else "Нет стажеров в очереди"
                    )
                    embed.set_field_at(
                        embed.fields.index(field),
                        name=field.name,
                        value=new_value,
                        inline=False
                    )
        # await interaction.response.send_message(f"Вы покинули очередь")
