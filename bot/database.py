import asyncpg

DATABASE_URL = "postgresql://bot_user:securepassword@localhost:5432/discord_bot"


async def create_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)


async def setup_db(bot):
    try:
        print("Initializing database...")
        bot.db_pool = await create_db_pool()
        if bot.db_pool is None:
            raise RuntimeError("Database setup failed!")
    except Exception as e:
        print(f"Database setup error: {e}")
        bot.db_pool = (
            None
        )
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
