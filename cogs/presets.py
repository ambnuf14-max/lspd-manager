"""
Улучшенная система управления пресетами ролей (v2)
"""
import traceback
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import PRESET_ADMIN_ROLE_ID
from bot.logger import get_logger
import json

logger = get_logger('presets')


async def log_preset_audit(bot, preset_id, preset_name, action, performed_by, old_value=None, new_value=None, details=None):
    """Логирование изменений пресетов в audit log"""
    try:
        async with bot.db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO preset_audit (preset_id, preset_name, action, performed_by, timestamp, old_value, new_value, details) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                preset_id,
                preset_name,
                action,
                performed_by,
                datetime.now(),
                json.dumps(old_value) if old_value else None,
                json.dumps(new_value) if new_value else None,
                details
            )
        logger.info(f"Audit log: {action} пресета '{preset_name}' пользователем {performed_by}")
    except Exception as e:
        logger.error(f"Ошибка записи в audit log: {e}", exc_info=True)


class PresetsV2(commands.Cog):
    """Улучшенная система управления пресетами ролей"""

    def __init__(self, bot):
        self.bot = bot

    async def is_preset_admin(self, user: discord.Member) -> bool:
        """Проверка прав на управление пресетами"""
        if user.guild.owner_id == user.id:
            return True

        if PRESET_ADMIN_ROLE_ID:
            try:
                admin_role_id = int(PRESET_ADMIN_ROLE_ID)
                admin_role = user.guild.get_role(admin_role_id)
                if admin_role and admin_role in user.roles:
                    return True
            except (ValueError, TypeError):
                pass

        return False

    # Группа команд /preset
    preset_group = app_commands.Group(name="preset", description="Управление пресетами ролей")

    @preset_group.command(name="create", description="Создать новый пресет (откроет окно выбора)")
    async def preset_create(self, interaction: discord.Interaction):
        """Создать новый пресет через модальное окно"""
        if not await self.is_preset_admin(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет прав для управления пресетами.",
                ephemeral=True
            )
            return

        modal = PresetCreateModal(self.bot, interaction.guild)
        await interaction.response.send_modal(modal)

    @preset_group.command(name="list", description="Показать все пресеты")
    async def preset_list(self, interaction: discord.Interaction):
        """Показать все пресеты"""
        try:
            async with self.bot.db_pool.acquire() as conn:
                presets = await conn.fetch(
                    "SELECT preset_id, name, role_ids, created_by, created_at FROM role_presets ORDER BY name"
                )

            if not presets:
                await interaction.response.send_message(
                    "ℹ️ Нет созданных пресетов.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="📋 Список пресетов ролей",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )

            for preset in presets[:25]:
                role_names = []
                for role_id in preset['role_ids']:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        role_names.append(role.name)
                    else:
                        role_names.append(f"❌ ID {role_id}")

                creator = interaction.guild.get_member(preset['created_by'])
                creator_name = creator.display_name if creator else f"ID {preset['created_by']}"

                embed.add_field(
                    name=f"**{preset['name']}** (ID: {preset['preset_id']})",
                    value=f"Роли: {', '.join(role_names)}\nСоздал: {creator_name}",
                    inline=False
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Список пресетов запрошен пользователем {interaction.user.display_name}")

        except Exception as e:
            logger.error(f"Ошибка при получении списка пресетов: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Ошибка при получении списка пресетов: {e}",
                ephemeral=True
            )

    @preset_group.command(name="delete", description="Удалить пресет")
    @app_commands.describe(name="Название пресета для удаления")
    async def preset_delete(self, interaction: discord.Interaction, name: str):
        """Удаление пресета"""
        if not await self.is_preset_admin(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет прав для управления пресетами.",
                ephemeral=True
            )
            return

        try:
            async with self.bot.db_pool.acquire() as conn:
                # Получаем информацию о пресете перед удалением для логирования
                preset = await conn.fetchrow(
                    "SELECT preset_id, name, role_ids, description FROM role_presets WHERE name = $1",
                    name
                )

                if not preset:
                    await interaction.response.send_message(
                        f"❌ Пресет '{name}' не найден.",
                        ephemeral=True
                    )
                    return

                # Удаляем пресет
                result = await conn.execute(
                    "DELETE FROM role_presets WHERE name = $1",
                    name
                )

            # Логирование удаления пресета
            await log_preset_audit(
                self.bot,
                preset['preset_id'],
                preset['name'],
                "delete",
                interaction.user.id,
                old_value={
                    "role_ids": preset['role_ids'],
                    "description": preset['description']
                },
                details=f"Пресет удален"
            )

            await interaction.response.send_message(
                f"✅ Пресет '{name}' удален.",
                ephemeral=True
            )
            logger.info(f"Пресет '{name}' удален пользователем {interaction.user.display_name}")

        except Exception as e:
            logger.error(f"Ошибка при удалении пресета: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Ошибка при удалении пресета: {e}",
                ephemeral=True
            )

    @preset_group.command(name="history", description="История изменений пресета")
    @app_commands.describe(name="Название пресета (опционально - показать всю историю)")
    async def preset_history(self, interaction: discord.Interaction, name: str = None):
        """Показать историю изменений пресета"""
        try:
            async with self.bot.db_pool.acquire() as conn:
                if name:
                    # История конкретного пресета
                    history = await conn.fetch(
                        "SELECT * FROM preset_audit WHERE preset_name = $1 ORDER BY timestamp DESC LIMIT 50",
                        name
                    )
                    title = f"📜 История пресета: {name}"
                else:
                    # Вся история
                    history = await conn.fetch(
                        "SELECT * FROM preset_audit ORDER BY timestamp DESC LIMIT 50"
                    )
                    title = "📜 История всех пресетов"

            if not history:
                await interaction.response.send_message(
                    "ℹ️ История изменений пуста.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title=title,
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )

            for entry in history[:25]:
                action_emoji = {
                    'create': '✅',
                    'delete': '❌',
                    'update': '✏️'
                }.get(entry['action'], '📝')

                performer = interaction.guild.get_member(entry['performed_by'])
                performer_name = performer.display_name if performer else f"ID {entry['performed_by']}"

                value = f"**Действие:** {action_emoji} {entry['action']}\n"
                value += f"**Кто:** {performer_name}\n"
                value += f"**Когда:** {entry['timestamp'].strftime('%d.%m.%Y %H:%M')}\n"
                if entry['details']:
                    value += f"**Детали:** {entry['details']}\n"

                embed.add_field(
                    name=f"{entry['preset_name']} (ID: {entry['audit_id']})",
                    value=value,
                    inline=False
                )

            embed.set_footer(text=f"Показано записей: {len(history[:25])}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"История пресетов запрошена пользователем {interaction.user.display_name}")

        except Exception as e:
            logger.error(f"Ошибка при получении истории пресетов: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Ошибка при получении истории: {e}",
                ephemeral=True
            )

    @preset_group.command(name="stats", description="Статистика по пресетам")
    async def preset_stats(self, interaction: discord.Interaction):
        """Показать статистику по пресетам"""
        try:
            async with self.bot.db_pool.acquire() as conn:
                # Общее количество пресетов
                total_presets = await conn.fetchval("SELECT COUNT(*) FROM role_presets")

                # Количество действий в audit log
                total_actions = await conn.fetchval("SELECT COUNT(*) FROM preset_audit")

                # Количество действий по типам
                action_stats = await conn.fetch(
                    "SELECT action, COUNT(*) as count FROM preset_audit GROUP BY action ORDER BY count DESC"
                )

                # Топ-5 активных пользователей
                top_users = await conn.fetch(
                    """
                    SELECT performed_by, COUNT(*) as count
                    FROM preset_audit
                    GROUP BY performed_by
                    ORDER BY count DESC
                    LIMIT 5
                    """
                )

                # Последние 5 действий
                recent_actions = await conn.fetch(
                    """
                    SELECT preset_name, action, performed_by, timestamp
                    FROM preset_audit
                    ORDER BY timestamp DESC
                    LIMIT 5
                    """
                )

            embed = discord.Embed(
                title="📊 Статистика по пресетам",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )

            # Общая информация
            embed.add_field(
                name="📈 Общая информация",
                value=f"**Всего пресетов:** {total_presets}\n**Всего действий:** {total_actions}",
                inline=False
            )

            # Статистика по действиям
            if action_stats:
                actions_text = "\n".join([
                    f"**{row['action']}:** {row['count']}" for row in action_stats
                ])
                embed.add_field(
                    name="📝 Действия по типам",
                    value=actions_text,
                    inline=False
                )

            # Топ пользователей
            if top_users:
                users_text = ""
                for i, row in enumerate(top_users, 1):
                    user = interaction.guild.get_member(row['performed_by'])
                    user_name = user.display_name if user else f"ID {row['performed_by']}"
                    users_text += f"{i}. **{user_name}** - {row['count']} действий\n"
                embed.add_field(
                    name="🏆 Топ-5 активных пользователей",
                    value=users_text,
                    inline=False
                )

            # Последние действия
            if recent_actions:
                recent_text = ""
                for row in recent_actions:
                    action_emoji = {
                        'create': '✅',
                        'delete': '❌',
                        'update': '✏️'
                    }.get(row['action'], '📝')
                    user = interaction.guild.get_member(row['performed_by'])
                    user_name = user.display_name if user else f"ID {row['performed_by']}"
                    recent_text += f"{action_emoji} **{row['preset_name']}** - {row['action']} ({user_name})\n"
                embed.add_field(
                    name="🕒 Последние 5 действий",
                    value=recent_text,
                    inline=False
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Статистика пресетов запрошена пользователем {interaction.user.display_name}")

        except Exception as e:
            logger.error(f"Ошибка при получении статистики: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Ошибка при получении статистики: {e}",
                ephemeral=True
            )

    @preset_group.command(name="info", description="Показать детали пресета")
    @app_commands.describe(name="Название пресета")
    async def preset_info(self, interaction: discord.Interaction, name: str):
        """Показать детали пресета"""
        try:
            async with self.bot.db_pool.acquire() as conn:
                preset = await conn.fetchrow(
                    "SELECT * FROM role_presets WHERE name = $1",
                    name
                )

            if not preset:
                await interaction.response.send_message(
                    f"❌ Пресет '{name}' не найден.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title=f"📋 Пресет: {preset['name']}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )

            role_names = []
            for role_id in preset['role_ids']:
                role = interaction.guild.get_role(role_id)
                if role:
                    role_names.append(f"✅ {role.name}")
                else:
                    role_names.append(f"❌ ID {role_id} (удалена)")

            embed.add_field(name="Роли", value="\n".join(role_names), inline=False)

            creator = interaction.guild.get_member(preset['created_by'])
            creator_name = creator.mention if creator else f"ID {preset['created_by']}"
            embed.add_field(name="Создал", value=creator_name, inline=True)
            embed.add_field(
                name="Дата создания",
                value=preset['created_at'].strftime('%d.%m.%Y %H:%M'),
                inline=True
            )

            if preset.get('description'):
                embed.add_field(name="Описание", value=preset['description'], inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Ошибка при получении информации о пресете: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Ошибка: {e}",
                ephemeral=True
            )

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("Presets V2 Cog загружен")


class PresetCreateModal(discord.ui.Modal, title="Создать пресет ролей"):
    """Модальное окно для создания пресета"""

    preset_name = discord.ui.TextInput(
        label="Название пресета",
        placeholder="Например: Офицер патруля",
        required=True,
        max_length=50
    )

    role_ids_input = discord.ui.TextInput(
        label="ID ролей через запятую",
        placeholder="123456789, 987654321, 111222333",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    description = discord.ui.TextInput(
        label="Описание (опционально)",
        placeholder="Краткое описание пресета",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=200
    )

    def __init__(self, bot, guild):
        super().__init__()
        self.bot = bot
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Парсинг ID ролей
            role_ids_str = self.role_ids_input.value.replace(" ", "")
            role_ids = [int(rid.strip()) for rid in role_ids_str.split(",") if rid.strip()]

            if not role_ids:
                await interaction.response.send_message(
                    "❌ Не указаны ID ролей!",
                    ephemeral=True
                )
                return

            # Валидация ролей
            invalid_roles = []
            valid_roles = []
            bot_top_role = self.guild.me.top_role

            for role_id in role_ids:
                role = self.guild.get_role(role_id)
                if not role:
                    invalid_roles.append(f"ID {role_id} (не найдена)")
                elif role >= bot_top_role:
                    invalid_roles.append(f"{role.name} (выше роли бота)")
                else:
                    valid_roles.append(role)

            if invalid_roles:
                await interaction.response.send_message(
                    f"❌ Проблемы с ролями:\n" + "\n".join(f"• {r}" for r in invalid_roles),
                    ephemeral=True
                )
                return

            # Сохранение в БД
            async with self.bot.db_pool.acquire() as conn:
                preset_id = await conn.fetchval(
                    "INSERT INTO role_presets (name, role_ids, created_by, created_at, description) "
                    "VALUES ($1, $2, $3, $4, $5) RETURNING preset_id",
                    self.preset_name.value,
                    role_ids,
                    interaction.user.id,
                    datetime.now(),
                    self.description.value if self.description.value else None
                )

            # Логирование создания пресета
            await log_preset_audit(
                self.bot,
                preset_id,
                self.preset_name.value,
                "create",
                interaction.user.id,
                new_value={
                    "role_ids": role_ids,
                    "description": self.description.value if self.description.value else None
                },
                details=f"Создан пресет с {len(valid_roles)} ролями"
            )

            role_list = ", ".join([r.name for r in valid_roles])
            await interaction.response.send_message(
                f"✅ Пресет **'{self.preset_name.value}'** создан!\n"
                f"Роли: {role_list}",
                ephemeral=True
            )

            logger.info(
                f"Пресет '{self.preset_name.value}' создан пользователем {interaction.user.display_name} "
                f"с {len(valid_roles)} ролями"
            )

        except ValueError:
            await interaction.response.send_message(
                "❌ Неверный формат ID ролей! Используйте только числа через запятую.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Ошибка при создании пресета: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Ошибка при создании пресета: {e}",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(PresetsV2(bot))
