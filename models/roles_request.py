import json
import traceback
from datetime import datetime

import discord

from bot.config import ADM_ROLES_CH


class PersistentView(discord.ui.View):
    def __init__(self, embed: discord.Embed, user: discord.User):
        super().__init__(timeout=None)  # Делаем View постоянным
        self.embed = embed
        self.user = user

        # Добавляем кнопки с уникальными custom_id
        self.add_item(DoneButton(embed, user))
        self.add_item(DropButton(embed, user))


class FeedbackModal(discord.ui.Modal, title="Получение роли"):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None  # Инициализируем атрибут пользователя

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
        channel = interaction.guild.get_channel(ADM_ROLES_CH)

        # Создаем Embed
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

        # Устанавливаем автора Embed с кликабельной ссылкой на пользователя
        embed.set_author(
            name=self.user.display_name,  # Имя пользователя
            icon_url=self.user.display_avatar.url,  # Аватар пользователя
            url=f"https://discord.com/users/{self.user.id}",  # Ссылка на профиль пользователя
        )

        view = PersistentView(embed, self.user)

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
        # Обновляем Embed
        self.embed.color = discord.Color.red()  # Меняем цвет Embed на красный
        self.embed.set_footer(
            text=f"Отклонено пользователем {interaction.user.display_name}. Причина: {self.reason.value}"
        )

        # Убираем кнопки
        self.view.clear_items()  # Удаляем все кнопки из View
        await interaction.message.edit(
            embed=self.embed, view=self.view
        )  # Обновляем сообщение

        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = 'rejected', finished_by = $1, finished_at = $2, reject_reason = $3"
                " WHERE message_id = $4",
                interaction.user.id,
                datetime.now(),
                self.reason.value,
                interaction.message.id,
            )

        # Отправляем сообщение пользователю
        try:
            await self.user.send(
                f"Ваш запрос на получение ролей был отклонён. Причина: {self.reason.value}"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Не удалось отправить сообщение пользователю {self.user.display_name}. Возможно, у него закрыты личные сообщения.",
                ephemeral=True,
            )

            # Подтверждение
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
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Получить роли",
        custom_id="register_button",
        style=discord.ButtonStyle.red,
    )
    async def registerbtn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        feedback_modal = FeedbackModal()
        feedback_modal.user = interaction.user  # Устанавливаем пользователя
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
        # Обновляем Embed, чтобы показать, что запрос выполнен
        self.embed.color = discord.Color.green()  # Меняем цвет Embed на зеленый
        self.embed.set_footer(
            text=f"Запрос выполнен пользователем {interaction.user.display_name}"
        )

        # Убираем кнопку "Выполнено"
        self.view.clear_items()  # Удаляем все кнопки из View
        await interaction.message.edit(
            embed=self.embed, view=self.view
        )  # Обновляем сообщение

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
                f"Не удалось отправить сообщение пользователю {self.user.display_name}. Возможно, у него закрыты личные сообщения.",
                ephemeral=True,
            )

        # Отправляем подтверждение
        await interaction.response.send_message(
            f"Запрос от {self.user.display_name} выполнен!", ephemeral=True
        )
