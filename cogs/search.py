import traceback

import discord
from discord import app_commands
from discord.ext import commands
from bot.config import ADM_ROLES_CH
from models.roles_request import is_preset_admin


class SearchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print("Search Cog loaded.")

    @commands.command()
    async def sync(self, ctx) -> None:
        if not await is_preset_admin(ctx.author):
            await ctx.send("❌ У вас нет прав для выполнения этой команды.")
            return
        # Копируем глобальные команды в guild
        self.bot.tree.copy_global_to(guild=ctx.guild)
        fmt = await ctx.bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"Synced {len(fmt)} commands.")

    @app_commands.command(name="search", description="Поиск запросов пользователя")
    @app_commands.describe(member="Пользователь, которого нужно найти")
    async def search(
        self, interaction: discord.Interaction, member: discord.Member = None
    ):
        if not await is_preset_admin(interaction.user):
            await interaction.response.send_message(
                "❌ У вас недостаточно прав для выполнения этой команды.",
                ephemeral=True
            )
            return

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
        await interaction.response.send_message(
            "❌ Произошла ошибка при выполнении команды.", ephemeral=True
        )
        traceback.print_exception(type(error), error, error.__traceback__)

async def setup(bot):
    await bot.add_cog(SearchCog(bot))
