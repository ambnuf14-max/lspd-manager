from datetime import datetime

import discord
from discord.ext import commands, tasks

from bot.config import (
    ADM_ROLES_CH,
    REMINDER_CHECK_MINUTES,
    REMINDER_FIRST_HOURS,
    REMINDER_SECOND_HOURS
)
from bot.logger import get_logger

logger = get_logger('reminders')


class RemindersCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._task_started = False

    def cog_unload(self):
        if self._task_started:
            self.reminder_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        """Запуск задачи напоминаний после готовности бота."""
        if not self._task_started:
            self.reminder_task.start()
            self._task_started = True
            logger.info("Задача напоминаний запущена")

    @tasks.loop(minutes=REMINDER_CHECK_MINUTES)
    async def reminder_task(self):
        """Проверка pending запросов и отправка напоминаний."""
        try:
            await self.check_pending_requests()
        except Exception as e:
            logger.error(f"Ошибка в задаче напоминаний: {e}", exc_info=True)

    async def check_pending_requests(self):
        """Проверяет все pending запросы и отправляет напоминания при необходимости."""
        # Проверка наличия пула БД
        if not hasattr(self.bot, 'db_pool') or self.bot.db_pool is None:
            logger.warning("База данных еще не инициализирована, пропускаем проверку")
            return

        channel = self.bot.get_channel(ADM_ROLES_CH)
        if not channel:
            logger.warning(f"Канал {ADM_ROLES_CH} не найден для напоминаний")
            return

        async with self.bot.db_pool.acquire() as conn:
            # Получаем все pending запросы
            rows = await conn.fetch(
                """
                SELECT message_id, created_at, reminder_count
                FROM requests
                WHERE status = 'pending'
                """
            )

        now = datetime.utcnow()

        for row in rows:
            try:
                await self.process_request_reminder(channel, row, now)
            except Exception as e:
                logger.error(f"Ошибка при обработке напоминания для {row['message_id']}: {e}", exc_info=True)

    async def process_request_reminder(self, channel: discord.TextChannel, row, now: datetime):
        """Обрабатывает одну запись и отправляет напоминание если нужно."""
        message_id = row['message_id']
        created_at = row['created_at']
        reminder_count = row['reminder_count'] or 0

        # Уже отправили максимум напоминаний
        if reminder_count >= 2:
            return

        hours_since_creation = (now - created_at).total_seconds() / 3600
        should_remind = False
        is_first_reminder = False

        if reminder_count == 0 and hours_since_creation >= REMINDER_FIRST_HOURS:
            # Первое напоминание: прошло >= 2 часов от создания
            should_remind = True
            is_first_reminder = True
        elif reminder_count == 1 and hours_since_creation >= REMINDER_SECOND_HOURS:
            # Второе напоминание: прошло >= 6 часов от создания
            should_remind = True
            is_first_reminder = False

        if not should_remind:
            return

        # Получаем сообщение
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            # Сообщение удалено - обновляем статус в БД
            logger.warning(f"Сообщение {message_id} не найдено, помечаем как deleted")
            async with self.bot.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE requests SET status = 'deleted' WHERE message_id = $1",
                    message_id
                )
            return
        except discord.Forbidden:
            logger.error(f"Нет доступа к сообщению {message_id}")
            return

        # Отправляем напоминание как ответ на сообщение
        if is_first_reminder:
            reminder_text = (
                f"@here\n\n"
                f"⏰ Неотработанный запрос на получение ролей. Прошло более {REMINDER_FIRST_HOURS} часов."
            )
        else:
            reminder_text = (
                f"@here\n\n"
                f"⏰ Неотработанный запрос на получение ролей. Прошло более {REMINDER_SECOND_HOURS} часов."
            )

        try:
            await message.reply(reminder_text, allowed_mentions=discord.AllowedMentions(everyone=True))
            logger.info(f"Отправлено напоминание #{reminder_count + 1} для запроса {message_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить напоминание для {message_id}: {e}")
            return

        # Обновляем БД
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE requests
                SET last_reminder_at = $1, reminder_count = $2
                WHERE message_id = $3
                """,
                now,
                reminder_count + 1,
                message_id
            )


async def setup(bot):
    await bot.add_cog(RemindersCog(bot))
