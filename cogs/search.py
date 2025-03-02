import traceback

import discord
from discord import app_commands
from discord.ext import commands
from bot.config import ADM_ROLES_CH


class SearchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print("Search Cog loaded.")

    @commands.command()
    async def sync(self, ctx) -> None:
        if ctx.author.guild_permissions.administrator:
            fmt = await ctx.bot.tree.sync(guild=ctx.guild)
            await ctx.send(f"Synced {len(fmt)} commands.")
        else:
            await ctx.send("❌ У вас нет прав для выполнения этой команды.")

    @app_commands.command(name="search", description="Поиск запросов пользователя")
    @app_commands.describe(member="Пользователь, которого нужно найти")
    @app_commands.checks.has_role("Discord Administrator")
    async def search(
        self, interaction: discord.Interaction, member: discord.Member = None
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            async with self.bot.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT message_id, status, finished_by, created_at, finished_at, reject_reason FROM requests "
                    "WHERE user_id = $1",
                    member.id,
                )

            if not rows:
                await interaction.followup.send(
                    "❌ Запросов не найдено.", ephemeral=True
                )
                return

            embed = discord.Embed(
                title=f"📜 История запросов {member.display_name}",
                color=discord.Color.blue(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)

            for row in rows:
                message_id = row["message_id"]
                status = row["status"].capitalize()
                finished_by = row["finished_by"]
                created_at = (
                    row["created_at"].strftime("%d.%m.%Y %H:%M")
                    if row["created_at"]
                    else "—"
                )
                finished_at = (
                    row["finished_at"].strftime("%d.%m.%Y %H:%M")
                    if row["finished_at"]
                    else "⏳ В процессе"
                )
                reject_reason = row["reject_reason"] if row["reject_reason"] else "—"

                message_link = f"https://discord.com/channels/{interaction.guild.id}/{ADM_ROLES_CH}/{message_id}"

                finished_by_mention = f"<@{finished_by}>" if finished_by else "—"

                reason_text = (
                    f"\n**Причина отклонения:** {reject_reason}"
                    if status.lower() == "rejected"
                    else ""
                )

                embed.add_field(
                    name=f"🔹 [Запрос {message_id}] ({message_link})",
                    value=f"**Статус:** {status}\n"
                    f"**Создан:** {created_at}\n"
                    f"**Завершён:** {finished_at}\n"
                    f"**Завершил:** {finished_by_mention}"
                    f"{reason_text}",
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            error_message = "❌ Ошибка при обработке запроса."
            await interaction.followup.send(error_message, ephemeral=True)
            traceback.print_exception(type(e), e, e.__traceback__)

    @search.error
    async def search_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message(
                "❌ У вас недостаточно прав для выполнения этой команды.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Произошла ошибка при выполнении команды.", ephemeral=True
            )
            traceback.print_exception(
                type(error), error, error.__traceback__
            )

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild):
        """Автоматическая регистрация слэш-команд при старте бота"""
        # self.bot.tree.clear_commands(guild=guild)
        self.bot.tree.add_command(self.search, guild=guild)
        await self.bot.tree.sync(guild=guild)
        print(f"Slash commands synced for {guild.name}")


async def setup(bot):
    await bot.add_cog(SearchCog(bot))
