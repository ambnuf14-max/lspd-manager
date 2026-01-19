"""
Утилита для массового создания рангов LSPD
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

from bot.config import PRESET_ADMIN_ROLE_ID
from bot.logger import get_logger

logger = get_logger('ranks')

# Список всех рангов LSPD в порядке иерархии
LSPD_RANKS = [
    "Chief of Police",
    "Assistant Chief of Police",
    "Deputy Chief of Police",
    "Police Commander",
    "Police Captain III",
    "Police Captain II",
    "Police Captain I",
    "Police Lieutenant II",
    "Police Lieutenant I",
    "Police Sergeant II",
    "Police Sergeant I",
    "Police Detective III",
    "Police Detective II",
    "Police Detective I",
    "Police Officer III+1",
    "Police Officer III",
    "Police Officer II",
    "Police Officer I",
    "Recruit Officer"
]


class RanksUtility(commands.Cog):
    """Утилиты для работы с рангами LSPD"""

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

    @app_commands.command(name="bulk_create_ranks", description="Массовое создание пресетов для рангов LSPD")
    @app_commands.describe(
        category_id="ID категории, куда добавить ранги (подкатегория со статусами)",
        start_index="С какого ранга начать (1-19, по умолчанию 1)"
    )
    async def bulk_create_ranks(
        self,
        interaction: discord.Interaction,
        category_id: int,
        start_index: int = 1
    ):
        """Массовое создание пресетов для всех рангов LSPD"""
        if not await self.is_preset_admin(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет прав для управления пресетами.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Проверяем существование категории
        async with self.bot.db_pool.acquire() as conn:
            category = await conn.fetchrow(
                "SELECT category_id, name FROM preset_categories WHERE category_id = $1",
                category_id
            )

            if not category:
                await interaction.followup.send(
                    f"❌ Категория с ID {category_id} не найдена!",
                    ephemeral=True
                )
                return

        # Валидация start_index
        if start_index < 1 or start_index > len(LSPD_RANKS):
            await interaction.followup.send(
                f"❌ Неверный индекс! Должен быть от 1 до {len(LSPD_RANKS)}.",
                ephemeral=True
            )
            return

        created_ranks = []
        failed_ranks = []

        # Создаём пресеты для рангов
        for i in range(start_index - 1, len(LSPD_RANKS)):
            rank_name = LSPD_RANKS[i]

            try:
                # Ищем роль Discord по названию ранга
                role = discord.utils.get(interaction.guild.roles, name=rank_name)

                if not role:
                    failed_ranks.append(f"{rank_name} (роль не найдена на сервере)")
                    logger.warning(f"Роль '{rank_name}' не найдена на сервере")
                    continue

                # Создаём пресет для ранга
                async with self.bot.db_pool.acquire() as conn:
                    # Проверяем, существует ли уже пресет с таким названием
                    existing = await conn.fetchval(
                        "SELECT preset_id FROM role_presets WHERE name = $1",
                        rank_name
                    )

                    if existing:
                        failed_ranks.append(f"{rank_name} (уже существует)")
                        logger.info(f"Пресет для '{rank_name}' уже существует, пропускаем")
                        continue

                    # Создаём пресет
                    await conn.execute(
                        "INSERT INTO role_presets (name, role_ids, created_by, created_at, description, category_id) "
                        "VALUES ($1, $2, $3, $4, $5, $6)",
                        rank_name,
                        [role.id],
                        interaction.user.id,
                        datetime.now(),
                        f"Ранг LSPD: {rank_name}",
                        category_id
                    )

                created_ranks.append(rank_name)
                logger.info(f"Создан пресет для ранга '{rank_name}' в категории {category['name']}")

            except Exception as e:
                failed_ranks.append(f"{rank_name} (ошибка: {str(e)})")
                logger.error(f"Ошибка при создании пресета для '{rank_name}': {e}", exc_info=True)

        # Формируем ответ
        embed = discord.Embed(
            title="🎖️ Результаты массового создания рангов",
            color=discord.Color.green() if created_ranks else discord.Color.red(),
            timestamp=datetime.now()
        )

        if created_ranks:
            embed.add_field(
                name=f"✅ Создано ({len(created_ranks)})",
                value="\n".join(f"• {rank}" for rank in created_ranks) if len(created_ranks) <= 25 else f"{len(created_ranks)} рангов",
                inline=False
            )

        if failed_ranks:
            embed.add_field(
                name=f"❌ Не удалось создать ({len(failed_ranks)})",
                value="\n".join(f"• {rank}" for rank in failed_ranks[:25]),
                inline=False
            )

        embed.set_footer(text=f"Категория: {category['name']}")

        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"Массовое создание рангов завершено: создано {len(created_ranks)}, пропущено {len(failed_ranks)}")


async def setup(bot):
    await bot.add_cog(RanksUtility(bot))
