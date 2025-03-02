import traceback


async def setup_on_error(bot):
    @bot.event
    async def on_error(event, *args, **kwargs):
        print(f"Ошибка в событии {event}: {args} {kwargs}")
        traceback.print_exc()
