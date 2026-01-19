import asyncio
import traceback

import gspread
from discord import Member
from oauth2client.service_account import ServiceAccountCredentials

from bot import config
from bot.config import (
    GOOGLE_SHEET_NAME,
    GOOGLE_CREDENTIALS_FILE,
    GSHEET_WORKSHEET_NAME,
    GSHEET_USERNAME_COLUMN,
    GSHEET_UPDATE_COLUMN
)

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


def _get_gsheet_client():
    """Синхронная функция для получения клиента Google Sheets"""
    creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_FILE, scope)
    return gspread.authorize(creds)


def _sync_update_roles(members_data: list[tuple[str, list[str]]]):
    """
    Синхронная функция для обновления ролей в Google Sheets.
    members_data: список кортежей (discord_username, roles_list)
    """
    client = _get_gsheet_client()
    sheet = client.open(GOOGLE_SHEET_NAME).worksheet(GSHEET_WORKSHEET_NAME)
    data = sheet.get_all_values()

    requests = []
    for member_str, roles in members_data:
        for i, row in enumerate(data):
            discord_username = row[GSHEET_USERNAME_COLUMN]
            if discord_username == member_str:
                if roles:
                    comment = "Роли:\n" + "\n".join(roles)
                    requests.append(
                        {
                            "updateCells": {
                                "range": {
                                    "sheetId": sheet.id,
                                    "startRowIndex": i,
                                    "endRowIndex": i + 1,
                                    "startColumnIndex": GSHEET_UPDATE_COLUMN,
                                    "endColumnIndex": GSHEET_UPDATE_COLUMN + 1,
                                },
                                "rows": [
                                    {
                                        "values": [
                                            {
                                                "userEnteredValue": {
                                                    "stringValue": "+"
                                                },
                                                "note": comment,
                                            }
                                        ]
                                    }
                                ],
                                "fields": "userEnteredValue,note",
                            }
                        }
                    )
                else:
                    requests.append(
                        {
                            "updateCells": {
                                "range": {
                                    "sheetId": sheet.id,
                                    "startRowIndex": i,
                                    "endRowIndex": i + 1,
                                    "startColumnIndex": GSHEET_UPDATE_COLUMN,
                                    "endColumnIndex": GSHEET_UPDATE_COLUMN + 1,
                                },
                                "rows": [
                                    {
                                        "values": [
                                            {
                                                "userEnteredValue": {
                                                    "stringValue": "-"
                                                },
                                                "note": "",
                                            }
                                        ]
                                    }
                                ],
                                "fields": "userEnteredValue,note",
                            }
                        }
                    )

    if requests:
        sheet.spreadsheet.batch_update({"requests": requests})
        return len(requests)
    return 0


def _sync_update_roles_comment(member_str: str, roles: list[str]):
    """Синхронная функция для обновления комментария одного пользователя"""
    client = _get_gsheet_client()
    main_sheet = client.open(GOOGLE_SHEET_NAME).worksheet(GSHEET_WORKSHEET_NAME)
    main_data = main_sheet.get_all_values()

    for i, row in enumerate(main_data):
        discord_username = row[GSHEET_USERNAME_COLUMN]
        if discord_username == member_str:
            if roles:
                comment = "Роли:\n" + "\n".join(roles)
                cell_value = "+"
            else:
                comment = ""
                cell_value = "-"

            requests = {
                "updateCells": {
                    "range": {
                        "sheetId": main_sheet.id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": GSHEET_UPDATE_COLUMN,
                        "endColumnIndex": GSHEET_UPDATE_COLUMN + 1,
                    },
                    "rows": [
                        {
                            "values": [
                                {
                                    "userEnteredValue": {
                                        "stringValue": cell_value
                                    },
                                    "note": comment,
                                }
                            ]
                        }
                    ],
                    "fields": "userEnteredValue,note",
                }
            }
            main_sheet.spreadsheet.batch_update({"requests": requests})
            return True
    return False


async def update_roles(bot):
    """Асинхронная функция для обновления ролей в Google Sheets"""
    try:
        guild = bot.get_guild(config.GUILD.id)
        if guild is None:
            print(
                f"Ошибка: гильдия с ID {config.GUILD} не найдена. Убедитесь, что бот находится на сервере."
            )
            return

        print(f"Гильдия найдена: {guild.name} (ID: {guild.id})")

        # Подготовка данных в основном потоке
        members_data = []
        for member in guild.members:
            roles = [role.name for role in member.roles if role.name != "@everyone"]
            members_data.append((str(member), roles))

        # Выполнение блокирующих операций в отдельном потоке
        count = await asyncio.to_thread(_sync_update_roles, members_data)

        if count:
            print(f"Добавлено {count} комментариев.")
        else:
            print("Нет данных для обновления.")

    except Exception as e:
        print(f"Ошибка при парсинге пользователей: {e}")
        traceback.print_exc()


async def update_roles_comment(member: Member):
    """Асинхронная функция для обновления комментария одного пользователя"""
    try:
        member_str = str(member)
        roles = [role.name for role in member.roles if role.name != "@everyone"]

        # Выполнение блокирующих операций в отдельном потоке
        success = await asyncio.to_thread(_sync_update_roles_comment, member_str, roles)

        if success:
            print(f"Комментарий для {member.name} обновлен.")

    except Exception as e:
        print(f"Ошибка при обновлении комментария для {member.name}: {e}")
        traceback.print_exc()
