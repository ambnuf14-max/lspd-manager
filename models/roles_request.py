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

        # Основные кнопки (row=0) - сбоку
        self.add_item(DoneButton(embed, user))
        self.add_item(DropButton(embed, user))

    async def load_presets(self):
        """Загрузка пресетов из БД и добавление Select Menu.
        ВАЖНО: Вызывать ПЕРЕД отправкой view в Discord!"""
        if self._presets_loaded:
            return  # Уже загружены

        try:
            async with self.bot.db_pool.acquire() as conn:
                presets = await conn.fetch(
                    "SELECT preset_id, name, role_ids FROM role_presets ORDER BY name"
                )

            # Добавляем Select Menu с пресетами (row=1), включая опцию "Добавить пресет"
            self.add_item(PresetSelect(presets[:24], self.embed, self.user, self.bot))
            if presets:
                logger.info(f"Загружено {len(presets[:24])} пресетов для запроса от {self.user.display_name}")

            self._presets_loaded = True
        except Exception as e:
            logger.error(f"Ошибка при загрузке пресетов: {e}", exc_info=True)


class PresetSelect(discord.ui.Select):
    """Выпадающий список для выбора пресета"""

    def __init__(self, presets: list, embed: discord.Embed, user: discord.User, bot):
        self.presets_data = {str(p['preset_id']): p for p in presets}
        self.embed = embed
        self.user = user
        self.bot = bot

        # Первая опция - добавить пресет
        options = [
            discord.SelectOption(
                label="Добавить пресет",
                value="add_preset",
                emoji="➕",
                description="Создать новый пресет ролей"
            )
        ]

        # Остальные пресеты
        options.extend([
            discord.SelectOption(
                label=preset['name'][:100],
                value=str(preset['preset_id']),
                description=f"Ролей: {len(preset['role_ids'])}"
            )
            for preset in presets[:24]
        ])

        super().__init__(
            placeholder="Выберите пресет для применения...",
            options=options,
            custom_id="preset_select",
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        """Обработка выбора пресета"""
        selected_value = self.values[0]

        # Обработка добавления пресета
        if selected_value == "add_preset":
            from cogs.presets import PresetCreateModal
            modal = PresetCreateModal(self.bot, interaction.guild)
            await interaction.response.send_modal(modal)
            return

        preset = self.presets_data.get(selected_value)

        if not preset:
            await interaction.response.send_message("❌ Пресет не найден.", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(self.user.id)

        if not member:
            await interaction.response.send_message(
                "❌ Пользователь больше не на сервере.",
                ephemeral=True
            )
            return

        # Получаем названия ролей для подтверждения
        role_names = []
        for role_id in preset['role_ids']:
            role = guild.get_role(role_id)
            if role:
                role_names.append(role.name)
            else:
                role_names.append(f"ID {role_id}")

        # Показываем подтверждение
        confirm_view = ConfirmPresetView(
            preset=preset,
            embed=self.embed,
            user=self.user,
            original_message=interaction.message,
            original_view=self.view
        )

        await interaction.response.send_message(
            f"**Выдать пресет «{preset['name']}»?**\n\nРоли: {', '.join(role_names)}",
            view=confirm_view,
            ephemeral=True
        )


class ConfirmPresetView(discord.ui.View):
    """View для подтверждения применения пресета"""

    def __init__(self, preset: dict, embed: discord.Embed, user: discord.User, original_message, original_view):
        super().__init__(timeout=60)
        self.preset = preset
        self.embed = embed
        self.user = user
        self.original_message = original_message
        self.original_view = original_view

    @discord.ui.button(label="Да", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Подтверждение применения пресета"""
        guild = interaction.guild
        member = guild.get_member(self.user.id)

        preset_name = self.preset['name']
        role_ids = self.preset['role_ids']

        logger.info(f"Пресет '{preset_name}' применяется к {self.user.display_name} ({self.user.id}) администратором {interaction.user.display_name}")

        if not member:
            logger.warning(f"Пользователь {self.user.display_name} ({self.user.id}) не найден на сервере")
            await interaction.response.edit_message(content="❌ Пользователь больше не на сервере.", view=None)
            return

        # Выдача ролей из пресета
        success_roles = []
        failed_roles = []

        for role_id in role_ids:
            role = guild.get_role(role_id)
            if not role:
                failed_roles.append(f"ID {role_id} (роль не найдена)")
                logger.warning(f"Роль с ID {role_id} не найдена на сервере")
                continue

            try:
                await member.add_roles(role, reason=f"Пресет '{preset_name}' применен {interaction.user.display_name}")
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
        footer_text = f"Пресет '{preset_name}' применен пользователем {interaction.user.display_name}"

        if failed_roles:
            footer_text += f"\n⚠️ Не удалось выдать: {', '.join(failed_roles)}"

        self.embed.set_footer(text=footer_text)

        # Очистка компонентов и обновление оригинального сообщения
        self.original_view.clear_items()
        await self.original_message.edit(embed=self.embed, view=self.original_view)

        # Обновление БД
        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = 'approved', finished_by = $1, finished_at = $2 WHERE message_id = $3",
                interaction.user.id,
                datetime.now(),
                self.original_message.id
            )

        # Уведомление пользователя
        try:
            msg = f"✅ Ваш запрос на получение ролей был одобрен!\nВыданы роли: {', '.join(success_roles)}"
            if failed_roles:
                msg += f"\n\n⚠️ Некоторые роли не были выданы автоматически, обратитесь к администратору."
            await self.user.send(msg)
        except discord.Forbidden:
            pass

        # Обновление ephemeral сообщения
        response_msg = f"✅ Пресет '{preset_name}' применен для {self.user.display_name}!"
        if success_roles:
            response_msg += f"\n✅ Выдано: {', '.join(success_roles)}"
        if failed_roles:
            response_msg += f"\n❌ Ошибки: {', '.join(failed_roles)}"

        await interaction.response.edit_message(content=response_msg, view=None)

    @discord.ui.button(label="Нет", style=discord.ButtonStyle.red, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Отмена применения пресета"""
        await interaction.response.edit_message(content="❌ Отменено.", view=None)


class FeedbackModal(discord.ui.Modal, title="Получение роли"):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.bot = None

    info = discord.ui.TextInput(
        label="Важно",
        default="Никнейм на сервере должен быть в формате: Name Surname (OOC Nick)",
        style=discord.TextStyle.short,
        required=False,
    )

    feedback = discord.ui.TextInput(
        label="Запрашиваемые роли",
        style=discord.TextStyle.long,
        placeholder="Например: Rampart Area, Detective I",
        required=True,
        max_length=300,
    )

    forum = discord.ui.TextInput(
        label="Форумный аккаунт (ps.ls-es.su)",
        style=discord.TextStyle.short,
        placeholder="Удостоверьтесь, что указали Discord в профиле",
        required=True,
        max_length=100,
    )

    vk = discord.ui.TextInput(
        label="ВКонтакте",
        style=discord.TextStyle.short,
        placeholder="https://vk.com/...",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Защита от спама: кулдаун 10 минут между запросами (не для админов/preset_admin)
        from bot.config import PRESET_ADMIN_ROLE_ID

        is_admin = interaction.user.guild_permissions.administrator
        has_preset_role = False

        if PRESET_ADMIN_ROLE_ID:
            try:
                preset_role = interaction.guild.get_role(int(PRESET_ADMIN_ROLE_ID))
                has_preset_role = preset_role and preset_role in interaction.user.roles
            except (ValueError, TypeError):
                pass

        if not is_admin and not has_preset_role:
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
        member = interaction.guild.get_member(self.user.id)

        # Получаем текущие роли пользователя (кроме @everyone)
        current_roles = [role.mention for role in member.roles if role.name != "@everyone"] if member else []
        roles_text = ", ".join(current_roles) if current_roles else "Нет ролей"

        # Дата захода на сервер
        joined_at = member.joined_at.strftime("%d.%m.%Y %H:%M") if member and member.joined_at else "Неизвестно"
        # Дата регистрации Discord
        created_at = self.user.created_at.strftime("%d.%m.%Y") if self.user.created_at else "Неизвестно"

        embed = discord.Embed(
            title="Запрос ролей",
            description=f"**От {self.user.mention} (ID: {self.user.id})**\n\n"
            f"**{self.feedback.label}**\n"
            f"{self.feedback.value}\n\n"
            f"**{self.forum.label}**\n"
            f"{self.forum.value}\n\n"
            f"**{self.vk.label}**\n"
            f"{self.vk.value}",
            color=discord.Color.yellow(),
            timestamp=datetime.now(),
        )

        embed.set_author(
            name=self.user.display_name,
            icon_url=self.user.display_avatar.url,
            url=f"https://discord.com/users/{self.user.id}",
        )

        embed.add_field(name="📅 На сервере с", value=joined_at, inline=True)
        embed.add_field(name="🗓️ Аккаунт создан", value=created_at, inline=True)
        embed.add_field(name="🎭 Текущие роли", value=roles_text[:1024], inline=False)

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
            label="Отклонить",
            style=discord.ButtonStyle.red,
            custom_id="drop_button",
            row=0
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
            label="Выполнено",
            style=discord.ButtonStyle.green,
            custom_id="done_button",
            row=0
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
