import asyncio
import json
import traceback
from datetime import datetime

import discord

from bot.config import ADM_ROLES_CH
from bot.logger import get_logger

logger = get_logger('roles_request')


class PersistentView(discord.ui.View):
    def __init__(self, embed: discord.Embed, user: discord.User, bot):
        super().__init__(timeout=None)
        self.embed = embed
        self.user = user
        self.bot = bot
        self._presets_loaded = False

        # Основные кнопки
        self.add_item(DoneButton(embed, user))
        self.add_item(DropButton(embed, user))

    async def load_presets(self):
        """Загрузка пресетов из БД и добавление кнопок.
        ВАЖНО: Вызывать ПЕРЕД отправкой view в Discord!"""
        if self._presets_loaded:
            return  # Уже загружены

        try:
            async with self.bot.db_pool.acquire() as conn:
                presets = await conn.fetch(
                    "SELECT preset_id, name, role_ids FROM role_presets ORDER BY name"
                )

            # Лимит Discord: 25 компонентов, у нас уже 2 (Done + Drop)
            # Максимум 23 пресета, но лучше ~20 для удобства
            for preset in presets[:20]:
                self.add_item(PresetButton(
                    preset_id=preset['preset_id'],
                    preset_name=preset['name'],
                    role_ids=preset['role_ids'],
                    embed=self.embed,
                    user=self.user
                ))

            self._presets_loaded = True
            logger.info(f"Загружено {len(presets[:20])} пресетов для запроса от {self.user.display_name}")
        except Exception as e:
            logger.error(f"Ошибка при загрузке пресетов: {e}", exc_info=True)


class PresetButton(discord.ui.Button):
    """Кнопка для применения пресета ролей"""

    def __init__(self, preset_id: int, preset_name: str, role_ids: list, embed: discord.Embed, user: discord.User):
        super().__init__(
            label=preset_name,
            style=discord.ButtonStyle.blurple,
            custom_id=f"preset_{preset_id}"
        )
        self.preset_id = preset_id
        self.preset_name = preset_name
        self.role_ids = role_ids
        self.embed = embed
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        """Обработка нажатия на кнопку пресета"""
        guild = interaction.guild
        member = guild.get_member(self.user.id)

        logger.info(f"Пресет '{self.preset_name}' применяется к {self.user.display_name} ({self.user.id}) администратором {interaction.user.display_name}")

        if not member:
            logger.warning(f"Пользователь {self.user.display_name} ({self.user.id}) не найден на сервере")
            await interaction.response.send_message(
                "❌ Пользователь больше не на сервере.",
                ephemeral=True
            )
            return

        # Выдача ролей из пресета
        success_roles = []
        failed_roles = []

        for role_id in self.role_ids:
            role = guild.get_role(role_id)
            if not role:
                failed_roles.append(f"ID {role_id} (роль не найдена)")
                logger.warning(f"Роль с ID {role_id} не найдена на сервере")
                continue

            try:
                await member.add_roles(role, reason=f"Пресет '{self.preset_name}' применен {interaction.user.display_name}")
                success_roles.append(role.name)
                logger.info(f"Роль '{role.name}' выдана пользователю {member.display_name}")
            except discord.Forbidden:
                failed_roles.append(f"{role.name} (нет прав)")
                logger.error(f"Нет прав для выдачи роли '{role.name}' пользователю {member.display_name}")
            except discord.HTTPException as e:
                failed_roles.append(f"{role.name} (ошибка: {e})")
                logger.error(f"HTTP ошибка при выдаче роли '{role.name}': {e}")

        # Обновление embed
        self.embed.color = discord.Color.green()
        footer_text = f"Пресет '{self.preset_name}' применен пользователем {interaction.user.display_name}"

        if failed_roles:
            footer_text += f"\n⚠️ Не удалось выдать: {', '.join(failed_roles)}"

        self.embed.set_footer(text=footer_text)

        # Очистка кнопок и обновление сообщения
        self.view.clear_items()
        await interaction.message.edit(embed=self.embed, view=self.view)

        # Обновление БД
        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = 'approved', finished_by = $1, finished_at = $2 WHERE message_id = $3",
                interaction.user.id,
                datetime.now(),
                interaction.message.id
            )

        # Уведомление пользователя
        try:
            msg = f"✅ Ваш запрос на получение ролей был одобрен!\nВыданы роли: {', '.join(success_roles)}"
            if failed_roles:
                msg += f"\n\n⚠️ Некоторые роли не были выданы автоматически, обратитесь к администратору."
            await self.user.send(msg)
        except discord.Forbidden:
            pass

        # Уведомление администратора
        response_msg = f"✅ Пресет '{self.preset_name}' применен для {self.user.display_name}!"
        if success_roles:
            response_msg += f"\n✅ Выдано: {', '.join(success_roles)}"
        if failed_roles:
            response_msg += f"\n❌ Ошибки: {', '.join(failed_roles)}"

        await interaction.response.send_message(response_msg, ephemeral=True)


class FeedbackModal(discord.ui.Modal, title="Получение роли"):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.bot = None

    info = discord.ui.TextInput(
        label="Информация!!!!",
        default="Обязательно укажите на сервере никнейм в следующем формате: Name Surname (OOC Nick).",
        style=discord.TextStyle.long,
        required=False,
    )

    feedback = discord.ui.TextInput(
        label="Укажите необходимые роли:",
        style=discord.TextStyle.long,
        required=True,
        max_length=300,
    )

    forum = discord.ui.TextInput(
        label="Форумник sa-es.su:",
        style=discord.TextStyle.short,
        placeholder="Проверьте, указан ли в профиле дискорд.",
        required=True,
        max_length=100,
    )

    vk = discord.ui.TextInput(
        label="Ваш ВКонтакте:",
        style=discord.TextStyle.short,
        placeholder="https://vk.com/id1",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Защита от спама: кулдаун 10 минут между запросами
        async with interaction.client.db_pool.acquire() as conn:
            last_request = await conn.fetchrow(
                "SELECT created_at FROM requests WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1",
                self.user.id
            )

        if last_request and last_request['created_at']:
            time_diff = datetime.now() - last_request['created_at']
            cooldown_minutes = 10
            if time_diff.total_seconds() < cooldown_minutes * 60:
                remaining = cooldown_minutes - int(time_diff.total_seconds() / 60)
                await interaction.response.send_message(
                    f"❌ Подождите ещё {remaining} мин. перед созданием нового запроса.",
                    ephemeral=True
                )
                return

        channel = interaction.guild.get_channel(ADM_ROLES_CH)

        embed = discord.Embed(
            title="Новый запрос",
            description=f"**От {self.user.mention}**\n\n"
            f"**{self.feedback.label}**\n"
            f"{self.feedback.value}\n"
            f"**{self.forum.label}**\n"
            f"{self.forum.value}\n"
            f"**{self.vk.label}**\n"
            f"{self.vk.value}",
            color=discord.Color.yellow(),
        )

        embed.set_author(
            name=self.user.display_name,
            icon_url=self.user.display_avatar.url,
            url=f"https://discord.com/users/{self.user.id}",
        )

        view = PersistentView(embed, self.user, self.bot)
        await view.load_presets()  # Загрузить пресеты ПЕРЕД отправкой

        message = await channel.send(embed=embed, view=view)

        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO requests (message_id, user_id, embed, status, created_at) VALUES ($1, $2, $3, $4, $5)",
                message.id,
                self.user.id,
                json.dumps(embed.to_dict()),
                "pending",
                datetime.now(),
            )

        await interaction.response.send_message(
            f"Скоро вы получите свои роли, {self.user.mention}!", ephemeral=True
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await interaction.response.send_message(
            "Упс! Что-то пошло не так.", ephemeral=True
        )
        traceback.print_exception(type(error), error, error.__traceback__)


class DropModal(discord.ui.Modal, title="Причина отказа"):
    def __init__(self, embed: discord.Embed, user: discord.User, view: discord.ui.View):
        super().__init__()
        self.embed = embed
        self.user = user
        self.view = view

    reason = discord.ui.TextInput(
        label="Укажите причину отказа",
        style=discord.TextStyle.long,
        placeholder="Например, недостаточно информации",
        required=True,
        max_length=300,
    )

    async def on_submit(self, interaction: discord.Interaction):
        self.embed.color = discord.Color.red()
        self.embed.set_footer(
            text=f"Отклонено пользователем {interaction.user.display_name}. Причина: {self.reason.value}"
        )

        self.view.clear_items()
        await interaction.message.edit(embed=self.embed, view=self.view)

        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = 'rejected', finished_by = $1, finished_at = $2, reject_reason = $3"
                " WHERE message_id = $4",
                interaction.user.id,
                datetime.now(),
                self.reason.value,
                interaction.message.id,
            )

        try:
            await self.user.send(
                f"Ваш запрос на получение ролей был отклонён. Причина: {self.reason.value}"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Не удалось отправить сообщение пользователю {self.user.display_name}. Возможно, у него закрыты "
                f"личные сообщения.",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"Запрос от {self.user.display_name} отклонён!", ephemeral=True
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await interaction.response.send_message(
            "Упс! Что-то пошло не так.", ephemeral=True
        )
        traceback.print_exception(type(error), error, error.__traceback__)


class ButtonView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Получить роли",
        custom_id="register_button",
        style=discord.ButtonStyle.red,
    )
    async def registerbtn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        feedback_modal = FeedbackModal()
        feedback_modal.user = interaction.user
        feedback_modal.bot = self.bot
        await interaction.response.send_modal(feedback_modal)


class DropButton(discord.ui.Button):
    def __init__(self, embed: discord.Embed, user: discord.User):
        super().__init__(
            label="Отклонить", style=discord.ButtonStyle.red, custom_id="drop_button"
        )
        self.embed = embed
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            DropModal(self.embed, self.user, self.view)
        )


class DoneButton(discord.ui.Button):
    def __init__(self, embed: discord.Embed, user: discord.User):
        super().__init__(
            label="Выполнено", style=discord.ButtonStyle.green, custom_id="done_button"
        )
        self.embed = embed
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        self.embed.color = discord.Color.green()
        self.embed.set_footer(
            text=f"Запрос выполнен пользователем {interaction.user.display_name}"
        )

        self.view.clear_items()
        await interaction.message.edit(embed=self.embed, view=self.view)

        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = 'approved', finished_by = $1, finished_at = $2 WHERE message_id = $3",
                interaction.user.id,
                datetime.now(),
                interaction.message.id,
            )

        try:
            await self.user.send("Ваш запрос на получение ролей был одобрен.")
        except discord.Forbidden:
            await interaction.followup.send(
                f"Не удалось отправить сообщение пользователю {self.user.display_name}. Возможно, у него закрыты "
                f"личные сообщения.",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"Запрос от {self.user.display_name} выполнен!", ephemeral=True
        )
