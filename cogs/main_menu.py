"""
Main Menu Cog

Главное меню Discord бота с функционалом:
- Получить инвайт в игре (SAMP через RakBot)
- Получить группы в TS3 (через SinusBot)
- Запросить роли (существующая функциональность)
"""
import httpx
import discord
from discord import app_commands
from discord.ext import commands
from typing import List

from bot.config import (
    BASE_LSPD_ROLE_ID,
    API_GATEWAY_URL,
    API_GATEWAY_KEY,
    TS3_SERVER_ADDRESS,
    TS3_SERVER_PORT
)
from bot.logger import get_logger

logger = get_logger('main_menu')


class TS3UIDModal(discord.ui.Modal, title="TeamSpeak 3 Unique ID"):
    """Модальное окно для ввода TS3 UID"""

    ts3_uid = discord.ui.TextInput(
        label="TS3 Unique ID",
        placeholder="Введите ваш TeamSpeak 3 Unique ID",
        style=discord.TextStyle.short,
        required=True,
        max_length=64
    )

    def __init__(self, view_instance):
        super().__init__()
        self.view_instance = view_instance

    async def on_submit(self, interaction: discord.Interaction):
        """Обработка submit модального окна"""
        await self.view_instance.handle_ts3_groups(interaction, self.ts3_uid.value)


class MainMenuView(discord.ui.View):
    """UI View с кнопками главного меню"""

    def __init__(self):
        super().__init__(timeout=None)

    def _get_user_role_ids(self, member: discord.Member) -> List[int]:
        """Получить список Discord Role IDs пользователя"""
        return [role.id for role in member.roles]

    @discord.ui.button(
        label="Получить инвайт",
        style=discord.ButtonStyle.primary,
        emoji="🎮",
        custom_id="get_invite_button"
    )
    async def get_invite_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        """Обработчик кнопки 'Получить инвайт'"""
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Собираем данные пользователя
        user_roles = self._get_user_role_ids(interaction.user)

        try:
            # HTTP POST к API Gateway
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{API_GATEWAY_URL}/discord/get-invite",
                    headers={"X-API-Key": API_GATEWAY_KEY},
                    json={
                        "discord_id": interaction.user.id,
                        "discord_username": interaction.user.name,
                        "discord_roles": user_roles
                    }
                )

            # Обработка ответа
            if response.status_code == 200:
                data = response.json()
                embed = discord.Embed(
                    title="✅ Инвайт отправлен",
                    description=data.get("message", "Инвайт успешно отправлен"),
                    color=discord.Color.green()
                )
                if nickname := data.get("nickname"):
                    embed.add_field(name="Никнейм в игре", value=nickname, inline=False)
                    embed.add_field(
                        name="Что дальше?",
                        value="Зайдите на сервер и примите инвайт командой `/accept`",
                        inline=False
                    )

            elif response.status_code == 403:
                data = response.json()
                embed = discord.Embed(
                    title="❌ Доступ запрещен",
                    description=data.get("detail", "У вас нет прав на получение инвайта"),
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="Возможные причины",
                    value=(
                        "• Ваш профиль не найден на форуме pd.ls-es.su\n"
                        "• У вас нет соответствующей группы на форуме\n"
                        "• У вас нет соответствующей роли в Discord"
                    ),
                    inline=False
                )

            elif response.status_code == 429:
                data = response.json()
                embed = discord.Embed(
                    title="⏳ Слишком много запросов",
                    description=data.get("detail", "Попробуйте позже"),
                    color=discord.Color.orange()
                )
                if retry_after := response.headers.get("Retry-After"):
                    minutes = int(retry_after) // 60
                    embed.add_field(
                        name="Попробуйте снова через",
                        value=f"{minutes} минут",
                        inline=False
                    )

            else:
                embed = discord.Embed(
                    title="❌ Ошибка сервиса",
                    description="Произошла ошибка при обработке запроса. Попробуйте позже.",
                    color=discord.Color.red()
                )
                logger.error(f"API Gateway error: {response.status_code} {response.text}")

        except httpx.TimeoutException:
            embed = discord.Embed(
                title="❌ Превышено время ожидания",
                description="Сервер не ответил вовремя. Попробуйте позже.",
                color=discord.Color.red()
            )
            logger.error("API Gateway timeout")

        except Exception as e:
            embed = discord.Embed(
                title="❌ Непредвиденная ошибка",
                description="Произошла ошибка. Обратитесь к администрации.",
                color=discord.Color.red()
            )
            logger.error(f"Unexpected error in get_invite: {e}", exc_info=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Получить группы в TS3",
        style=discord.ButtonStyle.secondary,
        emoji="🎙️",
        custom_id="get_ts3_groups_button"
    )
    async def get_ts3_groups_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        """Обработчик кнопки 'Получить группы в TS3'"""
        # Открываем модальное окно для ввода TS3 UID
        modal = TS3UIDModal(view_instance=self)
        await interaction.response.send_modal(modal)

    async def handle_ts3_groups(self, interaction: discord.Interaction, ts3_uid: str):
        """Обработка запроса TS3 групп после ввода UID"""
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Собираем данные пользователя
        user_roles = self._get_user_role_ids(interaction.user)

        try:
            # HTTP POST к API Gateway
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{API_GATEWAY_URL}/discord/get-ts3-groups",
                    headers={"X-API-Key": API_GATEWAY_KEY},
                    json={
                        "discord_id": interaction.user.id,
                        "discord_username": interaction.user.name,
                        "discord_roles": user_roles,
                        "ts3_uid": ts3_uid
                    }
                )

            # Обработка ответа
            if response.status_code == 200:
                data = response.json()
                embed = discord.Embed(
                    title="✅ Группы назначены",
                    description=data.get("message", "Группы успешно назначены в TeamSpeak 3"),
                    color=discord.Color.green()
                )

                if assigned := data.get("assigned_groups"):
                    embed.add_field(
                        name="Назначенные группы",
                        value=f"Количество: {len(assigned)}",
                        inline=False
                    )

                if failed := data.get("failed_groups"):
                    embed.add_field(
                        name="⚠️ Не удалось назначить",
                        value=f"Количество: {len(failed)}",
                        inline=False
                    )

                embed.add_field(
                    name="Сервер TeamSpeak 3",
                    value=f"`{TS3_SERVER_ADDRESS}:{TS3_SERVER_PORT}`",
                    inline=False
                )

            elif response.status_code == 403:
                data = response.json()
                embed = discord.Embed(
                    title="❌ Доступ запрещен",
                    description=data.get("detail", "У вас нет прав на получение TS3 групп"),
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="Возможные причины",
                    value=(
                        "• Ваш профиль не найден на форуме pd.ls-es.su\n"
                        "• У вас нет соответствующей группы на форуме\n"
                        "• У вас нет соответствующей роли в Discord"
                    ),
                    inline=False
                )

            elif response.status_code == 429:
                data = response.json()
                embed = discord.Embed(
                    title="⏳ Слишком много запросов",
                    description=data.get("detail", "Попробуйте позже"),
                    color=discord.Color.orange()
                )
                if retry_after := response.headers.get("Retry-After"):
                    minutes = int(retry_after) // 60
                    embed.add_field(
                        name="Попробуйте снова через",
                        value=f"{minutes} минут",
                        inline=False
                    )

            else:
                embed = discord.Embed(
                    title="❌ Ошибка сервиса",
                    description="Произошла ошибка при обработке запроса. Попробуйте позже.",
                    color=discord.Color.red()
                )
                logger.error(f"API Gateway error: {response.status_code} {response.text}")

        except httpx.TimeoutException:
            embed = discord.Embed(
                title="❌ Превышено время ожидания",
                description="Сервер не ответил вовремя. Попробуйте позже.",
                color=discord.Color.red()
            )
            logger.error("API Gateway timeout")

        except Exception as e:
            embed = discord.Embed(
                title="❌ Непредвиденная ошибка",
                description="Произошла ошибка. Обратитесь к администрации.",
                color=discord.Color.red()
            )
            logger.error(f"Unexpected error in handle_ts3_groups: {e}", exc_info=True)

        await interaction.followup.send(embed=embed, ephemeral=True)


class MainMenu(commands.Cog):
    """Главное меню Discord бота"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="menu", description="Открыть главное меню LSPD бота")
    async def menu(self, interaction: discord.Interaction):
        """
        Главное меню с функциями:
        - Получить инвайт в игре
        - Получить группы в TS3
        - Запросить роли (если нет базовой роли)
        """
        # Проверяем наличие базовой роли LSPD
        has_lspd_role = any(role.id == BASE_LSPD_ROLE_ID for role in interaction.user.roles)

        if has_lspd_role:
            # Показываем полное меню с кнопками
            embed = discord.Embed(
                title="📋 Главное меню LSPD",
                description="Выберите действие:",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="🎮 Получить инвайт",
                value="Получить инвайт в фракцию на игровом сервере",
                inline=False
            )
            embed.add_field(
                name="🎙️ Получить группы в TS3",
                value="Назначить группы в TeamSpeak 3 на основе ваших ролей",
                inline=False
            )

            view = MainMenuView()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        else:
            # Показываем сообщение о необходимости запросить роли
            embed = discord.Embed(
                title="❌ Доступ ограничен",
                description=(
                    "У вас нет базовой роли LSPD.\n\n"
                    "Для доступа к функционалу бота сначала запросите роли "
                    "через существующую систему запроса ролей."
                ),
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(MainMenu(bot))
