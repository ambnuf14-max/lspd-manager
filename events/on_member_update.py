from discord import Member
from discord.ext import commands

from bot.config import ENABLE_GSHEETS

if ENABLE_GSHEETS:
    from events.update_gsheet import update_roles_comment


async def setup_on_member_update(bot: commands.Bot):
    @bot.event
    async def on_member_update(before: Member, after: Member):
        if before.roles != after.roles:
            if ENABLE_GSHEETS:
                print(f"Роли пользователя {after.name} изменены. Обновляем таблицу...")
                await update_roles_comment(after)
            else:
                print(f"Роли пользователя {after.name} изменены (Google Sheets отключен).")
