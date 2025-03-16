import datetime
import traceback
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from events.update_gsheet import update_roles


class gSheets(commands.Cog):
    moscow_tz = ZoneInfo("Europe/Moscow")

    times = [
        datetime.time(hour=6, tzinfo=moscow_tz),
        datetime.time(hour=12, tzinfo=moscow_tz),
        datetime.time(hour=18, tzinfo=moscow_tz),
        datetime.time(hour=23, tzinfo=moscow_tz),
    ]

    def __init__(self, bot):
        self.bot = bot
        self.update_gsheet.start()

    @commands.Cog.listener()
    async def on_ready(self):
        print("gSheets Cog loaded.")
        # print(zoneinfo.available_timezones())
        if not self.update_gsheet.is_running():  # Проверяем, запущена ли задача
            self.update_gsheet.start()  # Запускаем задачу

    @tasks.loop(time=times)
    async def update_gsheet(self):
        print(f"Задача запущена в {datetime.datetime.now(self.moscow_tz)}")
        try:
            await update_roles(self.bot)
        except Exception as e:
            print(f"Ошибка в задаче update_gsheet: {e}")
            traceback.print_exc()
        print(f"Задача завершена в {datetime.datetime.now(self.moscow_tz)}")

    @update_gsheet.error
    async def update_gsheet_error(self, error):
        print(f"Ошибка при выполнении задачи: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)

    @update_gsheet.before_loop
    async def before_update_roles(self):
        await self.bot.wait_until_ready()
        print(f"[DEBUG] update_gsheet должна запуститься в {self.times}")
        print(f"[DEBUG] Текущее время: {datetime.datetime.now(self.moscow_tz)}")

    @update_gsheet.after_loop
    async def after_update_roles(self):
        print("Ну вроде успешно обновил таблицу :-)!")

    @app_commands.command(name="update", description="Обновить роли в таблице")
    @app_commands.checks.has_role("Discord Administrator")
    async def update(self, interaction=discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await update_roles(bot=self.bot)
            await interaction.followup.send(
                "Таблица успешно обновлена!", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"Ошибка при обновлении таблицы: {e}", ephemeral=True
            )

    @update.error
    async def update_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message(
                "❌ У вас недостаточно прав для выполнения этой команды.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Произошла ошибка при выполнении команды.", ephemeral=True
            )
            traceback.print_exception(type(error), error, error.__traceback__)

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild):
        """Автоматическая регистрация слэш-команд при старте бота"""
        # self.bot.tree.clear_commands(guild=guild)
        self.bot.tree.add_command(self.update, guild=guild)
        await self.bot.tree.sync(guild=guild)
        print(f"Slash commands synced for {guild.name}")


async def setup(bot):
    await bot.add_cog(gSheets(bot))
