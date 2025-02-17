import asyncpg

DATABASE_URL = "postgresql://bot_user:securepassword@localhost:5432/discord_bot"
db_pool = None


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
        bot.db_pool = None  # Ensure db_pool is explicitly set to None in case of failure
    async with bot.db_pool.acquire() as conn:
        await conn.execute("SET client_encoding = 'UTF8'")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                message_id BIGINT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                embed JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """)

    return db_pool
