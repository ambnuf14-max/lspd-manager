import os

import discord
from dotenv import load_dotenv

load_dotenv()

# ============ DISCORD ============
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD = discord.Object(id=int(os.getenv("GUILD_ID")))
ADM_ROLES_CH = int(os.getenv("ADM_ROLES_CHANNEL_ID"))
CL_REQUEST_CH = int(os.getenv("CLIENT_REQUEST_CHANNEL_ID"))
APPLICATION_ID = int(os.getenv("APPLICATION_ID"))
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
BOT_ACTIVITY_NAME = os.getenv("BOT_ACTIVITY_NAME", "sa-es.su")

# Preset admin role
PRESET_ADMIN_ROLE_ID = os.getenv("PRESET_ADMIN_ROLE_ID")

# ============ DATABASE ============
DATABASE_URL = os.getenv("DATABASE_URL")

# ============ DISCORD ROLES ============
BASE_LSPD_ROLE_ID = int(os.getenv("BASE_LSPD_ROLE_ID", "1350364976682106982"))
FTO_ROLE_NAME = os.getenv("FTO_ROLE_NAME", "OO: Field Training Officer")
INTERN_ROLE_NAME = os.getenv("INTERN_ROLE_NAME", "Police Officer I")

# ============ GOOGLE SHEETS ============
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GSHEET_WORKSHEET_NAME = os.getenv("GSHEET_WORKSHEET_NAME", "Таблица состава")
GSHEET_USERNAME_COLUMN = int(os.getenv("GSHEET_USERNAME_COLUMN", "20"))
GSHEET_UPDATE_COLUMN = int(os.getenv("GSHEET_UPDATE_COLUMN", "13"))
GSHEET_UPDATE_TIMES = [int(x) for x in os.getenv("GSHEET_UPDATE_TIMES", "6,12,18,23").split(",")]

# ============ TIMERS ============
FTO_QUEUE_CLEANUP_HOURS = int(os.getenv("FTO_QUEUE_CLEANUP_HOURS", "3"))
FTO_QUEUE_CHECK_MINUTES = int(os.getenv("FTO_QUEUE_CHECK_MINUTES", "1"))

# ============ REMINDERS ============
REMINDER_CHECK_MINUTES = int(os.getenv("REMINDER_CHECK_MINUTES", "5"))
REMINDER_FIRST_HOURS = int(os.getenv("REMINDER_FIRST_HOURS", "2"))
REMINDER_SECOND_HOURS = int(os.getenv("REMINDER_SECOND_HOURS", "6"))

# ============ ENVIRONMENT ============
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")

# ============ API GATEWAY ============
API_GATEWAY_URL = os.getenv("API_GATEWAY_URL", "http://localhost:8000")
API_GATEWAY_KEY = os.getenv("API_GATEWAY_KEY", "")

# ============ TEAMSPEAK 3 ============
TS3_SERVER_ADDRESS = os.getenv("TS3_SERVER_ADDRESS", "ts3.example.com")
TS3_SERVER_PORT = int(os.getenv("TS3_SERVER_PORT", "9987"))

# ============ FEATURES ============
ENABLE_GSHEETS = os.getenv("ENABLE_GSHEETS", "false").lower() == "true"
ENABLE_FTO_AUTO_MESSAGE = os.getenv("ENABLE_FTO_AUTO_MESSAGE", "false").lower() == "true"
ENABLE_API_SERVER = os.getenv("ENABLE_API_SERVER", "true").lower() == "true"

# ============ API SERVER ============
API_SERVER_HOST = os.getenv("API_SERVER_HOST", "0.0.0.0")
API_SERVER_PORT = int(os.getenv("API_SERVER_PORT", "8080"))
API_SERVER_KEY = os.getenv("API_SERVER_KEY", "")


# ============ VALIDATION ============
def validate_config():
    """Валидация критических переменных окружения"""
    required = {
        "DISCORD_TOKEN": TOKEN,
        "DATABASE_URL": DATABASE_URL,
    }

    # Если Google Sheets включен, требуем конфигурацию
    if ENABLE_GSHEETS:
        required["GOOGLE_SHEET_NAME"] = GOOGLE_SHEET_NAME

    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


validate_config()
