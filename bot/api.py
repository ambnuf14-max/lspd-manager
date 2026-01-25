import asyncio
from aiohttp import web
import discord
from bot.logger import get_logger

logger = get_logger('api')


class APIServer:
    def __init__(self, bot: discord.Client, host: str = '0.0.0.0', port: int = 8080):
        self.bot = bot
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner = None
        self._setup_routes()

    def _setup_routes(self):
        """Настройка маршрутов API"""
        self.app.router.add_get('/api/roles', self.get_roles)

    async def get_roles(self, request: web.Request):
        """
        Получить роли пользователя на сервере

        Query параметры:
        - guild_id: ID сервера Discord
        - user_id: ID пользователя Discord

        Ответ:
        {
            "success": true,
            "roles": [
                {
                    "id": "123456789",
                    "name": "Admin",
                    "color": "#FF0000",
                    "position": 10,
                    "permissions": "8"
                },
                ...
            ]
        }
        """
        try:
            # Получаем параметры запроса
            guild_id = request.query.get('guild_id')
            user_id = request.query.get('user_id')

            # Валидация параметров
            if not guild_id:
                return web.json_response({
                    'success': False,
                    'error': 'guild_id parameter is required'
                }, status=400)

            if not user_id:
                return web.json_response({
                    'success': False,
                    'error': 'user_id parameter is required'
                }, status=400)

            # Конвертируем в int
            try:
                guild_id = int(guild_id)
                user_id = int(user_id)
            except ValueError:
                return web.json_response({
                    'success': False,
                    'error': 'guild_id and user_id must be valid integers'
                }, status=400)

            # Получаем guild
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return web.json_response({
                    'success': False,
                    'error': f'Guild with id {guild_id} not found'
                }, status=404)

            # Получаем member
            member = guild.get_member(user_id)
            if not member:
                # Пробуем fetch если не в кеше
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    return web.json_response({
                        'success': False,
                        'error': f'Member with id {user_id} not found in guild {guild_id}'
                    }, status=404)
                except discord.HTTPException as e:
                    logger.error(f"Error fetching member: {e}")
                    return web.json_response({
                        'success': False,
                        'error': f'Failed to fetch member: {str(e)}'
                    }, status=500)

            # Формируем список ролей
            roles = []
            for role in member.roles:
                # Пропускаем @everyone роль если нужно
                if role.is_default():
                    continue

                roles.append({
                    'id': str(role.id),
                    'name': role.name,
                    'color': str(role.color),
                    'position': role.position,
                    'permissions': str(role.permissions.value),
                    'mentionable': role.mentionable,
                    'hoist': role.hoist
                })

            # Сортируем роли по позиции (от высшей к низшей)
            roles.sort(key=lambda x: x['position'], reverse=True)

            return web.json_response({
                'success': True,
                'roles': roles,
                'user': {
                    'id': str(member.id),
                    'username': member.name,
                    'discriminator': member.discriminator,
                    'display_name': member.display_name,
                    'avatar_url': str(member.display_avatar.url) if member.display_avatar else None
                },
                'guild': {
                    'id': str(guild.id),
                    'name': guild.name
                }
            })

        except Exception as e:
            logger.error(f"Unexpected error in get_roles: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'error': f'Internal server error: {str(e)}'
            }, status=500)

    async def start(self):
        """Запуск API сервера"""
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, self.host, self.port)
            await site.start()
            logger.info(f"API сервер запущен на http://{self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Ошибка запуска API сервера: {e}", exc_info=True)
            raise

    async def stop(self):
        """Остановка API сервера"""
        if self.runner:
            await self.runner.cleanup()
            logger.info("API сервер остановлен")
