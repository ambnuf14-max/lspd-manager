import os

import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD = discord.Object(id=1340242793255145474)
ADM_ROLES_CH = 1340258373597270077
CL_REQUEST_CH = 1340242793901199384