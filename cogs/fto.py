import traceback

import discord
from discord import app_commands
from discord.ext import commands

from models.fto_request import FTOView
from models.roles_request import is_preset_admin


class FTOCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print("FTO Cog loaded")

    @app_commands.command(name="fto", description="Отправить сообщение об очереди ФТО")
    async def fto(self, interaction: discord.Interaction):
        if not await is_preset_admin(interaction.user):
            await interaction.response.send_message(
                "❌ У вас недостаточно прав для выполнения этой команды.",
                ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            embed = discord.Embed(title="FTO Search")
            embed.set_thumbnail(url="https://i.imgur.com/89q0Cdj.png")
            embed.set_image(url="https://i.imgur.com/rZvJhyw.png")
            embed.add_field(
                name="",
                value="Этот модуль предназначен для поиска наставника или стажера. Выбрав соответствующий "
                "пункт в меню - вы встанете в очередь или сразу переключитесь на свободного "
                "наставника. Учтите, что очередь очищается каждые три часа.",
                inline=False,
            )
            embed.add_field(
                name="Стажеры в очереди", value="Нет стажеров в очереди", inline=False
            )
            embed.add_field(name="Свободные FTO", value="Нет FTO", inline=False)
            embed.set_footer(
                text="Los Santos Police Department. Разработчик: Chlorine, специально для Moon."
            )

            await interaction.followup.send(
                embed=embed, view=FTOView(interaction.client)
            )
        except Exception as e:
            error_message = "❌ Ошибка при обработке запроса."
            await interaction.followup.send(error_message, ephemeral=True)
            traceback.print_exception(type(e), e, e.__traceback__)  # Логируем ошибку

    @fto.error
    async def fto_error(self, interaction: discord.Interaction, error):
        await interaction.response.send_message(
            "❌ Произошла ошибка при выполнении команды.", ephemeral=True
        )
        traceback.print_exception(type(error), error, error.__traceback__)

async def setup(bot):
    await bot.add_cog(FTOCog(bot))
