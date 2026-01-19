import asyncpg
from bot.config import DATABASE_URL
from bot.logger import get_logger

logger = get_logger('database')


async def create_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)


async def setup_db(bot):
    try:
        logger.info("Инициализация базы данных...")
        bot.db_pool = await create_db_pool()
        if bot.db_pool is None:
            raise RuntimeError("Database setup failed!")
        logger.info("База данных успешно подключена")
    except Exception as e:
        logger.error(f"Ошибка настройки базы данных: {e}", exc_info=True)
        bot.db_pool = None
        raise
    async with bot.db_pool.acquire() as conn:
        await conn.execute("SET client_encoding = 'UTF8'")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                message_id BIGINT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                embed JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                finished_by BIGINT,
                created_at TIMESTAMP WITHOUT TIME ZONE,
                finished_at TIMESTAMP WITHOUT TIME ZONE,
                reject_reason TEXT
            )
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS queue (
                queue_id SERIAL PRIMARY KEY,
                probationary_id BIGINT,
                officer_id BIGINT,
                display_name TEXT,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                finished_at TIMESTAMP WITHOUT TIME ZONE
            )
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS role_presets (
                preset_id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                role_ids BIGINT[] NOT NULL,
                created_by BIGINT NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                description TEXT,
                emoji TEXT
            )
        """
        )
        # Миграция: добавляем колонку emoji если её нет
        await conn.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'role_presets' AND column_name = 'emoji'
                ) THEN
                    ALTER TABLE role_presets ADD COLUMN emoji TEXT;
                END IF;
            END $$;
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS preset_audit (
                audit_id SERIAL PRIMARY KEY,
                preset_id INT,
                preset_name TEXT NOT NULL,
                action TEXT NOT NULL,
                performed_by BIGINT NOT NULL,
                timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                old_value JSONB,
                new_value JSONB,
                details TEXT
            )
        """
        )
        logger.info("Таблицы БД созданы/проверены успешно")
