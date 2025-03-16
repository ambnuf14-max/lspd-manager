import traceback

import gspread
from discord import Member
from oauth2client.service_account import ServiceAccountCredentials

from bot import config

SHEET_NAME = "LSPD Faction by Moon"
JSON_KEYFILE = "credentials.json"
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


async def update_roles(bot):
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE, scope)
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).worksheet("Таблица состава")
        data = sheet.get_all_values()

        guild = bot.get_guild(config.GUILD.id)
        if guild is None:
            print(
                f"Ошибка: гильдия с ID {config.GUILD} не найдена. Убедитесь, что бот находится на сервере."
            )
        else:
            print(f"Гильдия найдена: {guild.name} (ID: {guild.id})")
        members = guild.members
        requests = []
        for member in members:
            for i, row in enumerate(data):
                discord_username = row[
                    20
                ]  # Предположим, что никнейм Discord находится во 21 столбце (индекс 20)
                if discord_username == str(member):
                    roles = [
                        role.name for role in member.roles if role.name != "@everyone"
                    ]
                    if roles:
                        comment = "Роли:\n" + "\n".join(roles)
                    else:
                        comment = "-"

                    requests.append(
                        {
                            "updateCells": {
                                "range": {
                                    "sheetId": sheet.id,
                                    "startRowIndex": i,  # Индексация с 0
                                    "endRowIndex": i + 1,
                                    "startColumnIndex": 13,  # Столбец N (индекс 13)
                                    "endColumnIndex": 14,
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

        if requests:
            sheet.spreadsheet.batch_update({"requests": requests})
            print(f"Добавлено {len(requests)} комментариев.")
        else:
            print("Нет данных для обновления.")

    except Exception as e:
        print(f"Ошибка при парсинге пользователей: {e}")
        traceback.print_exc()
        return


async def update_roles_comment(member: Member):
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE, scope)
        client = gspread.authorize(creds)

        main_sheet = client.open(SHEET_NAME).worksheet("Таблица состава")
        main_data = main_sheet.get_all_values()

        for i, row in enumerate(main_data):
            discord_username = row[
                20
            ]  # Предположим, что никнейм Discord находится во 21 столбце (индекс 20)
            if discord_username == str(member):
                roles = [role.name for role in member.roles if role.name != "@everyone"]
                comment = "Роли:\n" + "\n".join(roles)
                requests = {
                    "updateCells": {
                        "range": {
                            "sheetId": main_sheet.id,
                            "startRowIndex": i,  # Индексация с 0
                            "endRowIndex": i + 1,
                            "startColumnIndex": 13,  # Столбец N (индекс 13)
                            "endColumnIndex": 14,
                        },
                        "rows": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": "+"},
                                        "note": comment,
                                    }
                                ]
                            }
                        ],
                        "fields": "userEnteredValue,note",
                    }
                }
                main_sheet.spreadsheet.batch_update({"requests": requests})
                print(f"Комментарий для {member.name} обновлен.")
                # break
    except Exception as e:
        print(f"Ошибка при обновлении комментария для {member.name}: {e}")
        traceback.print_exc()
