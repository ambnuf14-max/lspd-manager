import json
import re
import traceback
from datetime import datetime

import discord

from bot.config import ADM_ROLES_CH, PRESET_ADMIN_ROLE_ID
from bot.logger import get_logger

logger = get_logger('roles_request')

# Импорт рангов LSPD для автоматического создания
try:
    from cogs.ranks import LSPD_RANKS, RANK_TO_GROUP_ROLE
except ImportError:
    LSPD_RANKS = []
    RANK_TO_GROUP_ROLE = {}


# ============== РАБОТА С ЭМОДЗИ ==============

def parse_emoji(emoji_str: str, guild: discord.Guild = None) -> discord.PartialEmoji | str | None:
    """
    Парсит строку эмодзи и возвращает объект для использования в Discord.

    Поддерживаемые форматы:
    - Unicode эмодзи: "🚔"
    - ID кастомного эмодзи: "1234567890"
    - Полный формат: "<:name:1234567890>" или "<a:name:1234567890>"
    """
    if not emoji_str:
        return None

    emoji_str = emoji_str.strip()

    if not emoji_str:
        return None

    try:
        # Проверяем полный формат кастомного эмодзи <:name:id> или <a:name:id>
        custom_match = re.match(r'<(a)?:(\w+):(\d+)>', emoji_str)
        if custom_match:
            animated = custom_match.group(1) == 'a'
            name = custom_match.group(2)
            emoji_id = int(custom_match.group(3))
            return discord.PartialEmoji(name=name, id=emoji_id, animated=animated)

        # Проверяем только ID (число)
        if emoji_str.isdigit():
            emoji_id = int(emoji_str)
            # Пытаемся найти эмодзи на сервере для получения имени
            if guild:
                emoji = discord.utils.get(guild.emojis, id=emoji_id)
                if emoji:
                    return discord.PartialEmoji(name=emoji.name, id=emoji.id, animated=emoji.animated)
            # Если не нашли на сервере - возвращаем None (невалидный ID)
            return None

        # Проверяем что это похоже на Unicode эмодзи (1-4 символа, не ASCII)
        if len(emoji_str) <= 4 and not emoji_str.isascii():
            return emoji_str

        # Невалидный формат
        return None

    except Exception:
        return None


def normalize_emoji_for_storage(emoji_str: str, guild: discord.Guild) -> str | None:
    """
    Нормализует эмодзи для сохранения в БД.
    Преобразует ID в полный формат <:name:id> если эмодзи найден на сервере.
    """
    if not emoji_str:
        return None

    emoji_str = emoji_str.strip()

    if not emoji_str:
        return None

    # Уже в полном формате - возвращаем как есть
    if re.match(r'<a?:\w+:\d+>', emoji_str):
        return emoji_str

    # Если это ID - преобразуем в полный формат
    if emoji_str.isdigit():
        emoji_id = int(emoji_str)
        if guild:
            emoji = discord.utils.get(guild.emojis, id=emoji_id)
            if emoji:
                prefix = 'a' if emoji.animated else ''
                return f"<{prefix}:{emoji.name}:{emoji.id}>"
        return None  # Невалидный ID

    # Unicode эмодзи - возвращаем как есть
    if len(emoji_str) <= 4 and not emoji_str.isascii():
        return emoji_str

    return None


# ============== ПРОВЕРКА ПРАВ ==============

async def is_preset_admin(user: discord.Member) -> bool:
    """Проверка прав на управление пресетами"""
    if user.guild_permissions.administrator:
        return True
    if user.guild.owner_id == user.id:
        return True
    if PRESET_ADMIN_ROLE_ID:
        try:
            admin_role_id = int(PRESET_ADMIN_ROLE_ID)
            admin_role = user.guild.get_role(admin_role_id)
            if admin_role and admin_role in user.roles:
                return True
        except (ValueError, TypeError):
            pass
    return False


# ============== ОСНОВНОЙ VIEW ДЛЯ ЗАПРОСА ==============

class PersistentView(discord.ui.View):
    def __init__(self, embed: discord.Embed, user: discord.User, bot, guild: discord.Guild = None):
        super().__init__(timeout=None)
        self.embed = embed
        self.user = user
        self.bot = bot
        self.guild = guild
        self._presets_loaded = False

        # Основные кнопки (row=0)
        self.add_item(DoneButton(embed, user))
        self.add_item(DropButton(embed, user, bot))
        self.add_item(SettingsButton(embed, user, bot))

    async def load_presets(self):
        """Загрузка категорий и пресетов для каскадного выбора."""
        if self._presets_loaded:
            return

        try:
            select = PresetCategorySelect(self.embed, self.user, self.bot, self.guild)
            await select.load_options()
            self.add_item(select)

            # Добавляем кнопки пагинации если нужно
            if select.total_pages > 1:
                self.add_item(PresetPrevPageButton(select))
                self.add_item(PresetNextPageButton(select))

            logger.info(f"Каскадный выбор пресетов загружен для запроса от {self.user.display_name}")
            self._presets_loaded = True
        except Exception as e:
            logger.error(f"Ошибка при загрузке пресетов: {e}", exc_info=True)


# ============== КАСКАДНЫЙ ВЫБОР ПРЕСЕТА ==============

class PresetCategorySelect(discord.ui.Select):
    """Первый уровень - выбор категории или пресета без категории"""

    def __init__(self, embed: discord.Embed, user: discord.User, bot, guild: discord.Guild, parent_category_id=None, page=0):
        self.embed = embed
        self.user = user
        self.bot = bot
        self.guild = guild
        self.parent_category_id = parent_category_id
        self.page = page

        super().__init__(
            placeholder="Загрузка...",
            options=[discord.SelectOption(label="Загрузка...", value="loading")],
            custom_id=f"preset_cat_select_{parent_category_id or 'root'}_{page}",
            row=1
        )

    async def load_options(self):
        """Асинхронная загрузка опций"""
        # Сохраняем информацию о текущей категории для placeholder
        self.current_category_name = None
        self.current_parent_name = None

        async with self.bot.db_pool.acquire() as conn:
            if self.parent_category_id is None:
                # Корневой уровень: категории + пресеты без категории
                categories = await conn.fetch(
                    "SELECT category_id, name, emoji FROM preset_categories WHERE parent_id IS NULL ORDER BY name"
                )
                uncategorized = await conn.fetch(
                    "SELECT preset_id, name, description, emoji FROM role_presets WHERE category_id IS NULL ORDER BY sort_order NULLS LAST, name"
                )
            else:
                # Загружаем информацию о текущей категории для placeholder
                current_cat = await conn.fetchrow(
                    """
                    SELECT c.name, p.name as parent_name
                    FROM preset_categories c
                    LEFT JOIN preset_categories p ON c.parent_id = p.category_id
                    WHERE c.category_id = $1
                    """,
                    self.parent_category_id
                )
                if current_cat:
                    self.current_category_name = current_cat['name']
                    self.current_parent_name = current_cat['parent_name']

                # Уровень подкатегорий: подкатегории + пресеты этой категории
                categories = await conn.fetch(
                    "SELECT category_id, name, emoji FROM preset_categories WHERE parent_id = $1 ORDER BY name",
                    self.parent_category_id
                )
                uncategorized = await conn.fetch(
                    "SELECT preset_id, name, description, emoji FROM role_presets WHERE category_id = $1 ORDER BY sort_order NULLS LAST, name",
                    self.parent_category_id
                )

        # Сохраняем полные списки для пагинации
        self.all_categories = list(categories)
        self.all_presets = list(uncategorized)

        options = []

        # Кнопка "Назад" если не корневой уровень
        back_option_count = 0
        if self.parent_category_id is not None:
            options.append(discord.SelectOption(
                label="Назад",
                value="back"
            ))
            back_option_count = 1

        # Вычисляем количество элементов на странице
        items_per_page = 20

        # Сначала показываем все категории (они не пагинируются)
        for cat in categories:
            # Используем пользовательский эмодзи, если указан
            emoji_str = cat.get('emoji')
            emoji = "📁"
            if emoji_str:
                parsed_emoji = parse_emoji(emoji_str, self.guild)
                if parsed_emoji:
                    emoji = parsed_emoji

            options.append(discord.SelectOption(
                label=cat['name'][:100],
                value=f"cat_{cat['category_id']}",
                emoji=emoji
            ))

        # Вычисляем offset и limit для пресетов с учетом категорий и кнопки "Назад"
        max_options = 25 - back_option_count - len(categories)
        start_idx = self.page * max_options
        end_idx = start_idx + max_options

        # Добавляем пресеты для текущей страницы
        presets_on_page = uncategorized[start_idx:end_idx]
        for preset in presets_on_page:
            description = preset.get('description')
            if description and len(description) > 100:
                description = description[:97] + "..."
            emoji = parse_emoji(preset.get('emoji'), self.guild)

            options.append(discord.SelectOption(
                label=preset['name'][:100],
                value=f"preset_{preset['preset_id']}",
                description=description,
                emoji=emoji
            ))

        if not options:
            options.append(discord.SelectOption(
                label="Нет пресетов",
                value="none",
                description="Создайте пресет через кнопку Настройки"
            ))

        self.options = options

        # Вычисляем общее количество страниц
        total_presets = len(uncategorized)
        self.total_pages = (total_presets + max_options - 1) // max_options if total_presets > 0 else 1

        # Формируем placeholder с путем к текущей категории
        if self.current_category_name:
            # Показываем путь к категории
            if self.current_parent_name:
                # Подкатегория - показываем: родитель -> категория
                category_path = f"{self.current_parent_name} → {self.current_category_name}"
            else:
                # Корневая категория
                category_path = self.current_category_name

            if self.total_pages > 1:
                self.placeholder = f"{category_path} (Стр. {self.page + 1}/{self.total_pages})"
            else:
                self.placeholder = category_path
        else:
            # Корневой уровень - стандартный placeholder
            if self.total_pages > 1:
                self.placeholder = f"Выберите роли для назначения... (Стр. {self.page + 1}/{self.total_pages})"
            else:
                self.placeholder = "Выберите роли для назначения..."

        # Ограничиваем длину placeholder (Discord лимит 150 символов)
        if len(self.placeholder) > 150:
            self.placeholder = self.placeholder[:147] + "..."

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]

        if selected_value == "none":
            await interaction.response.send_message(
                "Пресеты не созданы. Используйте кнопку **Настройки** для создания.",
                ephemeral=True
            )
            return

        if selected_value == "loading":
            await interaction.response.defer()
            return

        if selected_value == "back":
            # Возврат на уровень выше
            async with self.bot.db_pool.acquire() as conn:
                if self.parent_category_id:
                    parent = await conn.fetchrow(
                        "SELECT parent_id FROM preset_categories WHERE category_id = $1",
                        self.parent_category_id
                    )
                    new_parent_id = parent['parent_id'] if parent else None
                else:
                    new_parent_id = None

            new_select = PresetCategorySelect(self.embed, self.user, self.bot, self.guild, new_parent_id)
            await new_select.load_options()

            # Удаляем старый select и кнопки пагинации
            items_to_remove = [item for item in self.view.children if isinstance(item, (PresetCategorySelect, PresetPrevPageButton, PresetNextPageButton))]
            for item in items_to_remove:
                self.view.remove_item(item)

            # Добавляем новый select
            self.view.add_item(new_select)

            # Добавляем кнопки пагинации если нужно
            if new_select.total_pages > 1:
                self.view.add_item(PresetPrevPageButton(new_select))
                self.view.add_item(PresetNextPageButton(new_select))

            await interaction.response.edit_message(view=self.view)
            return

        if selected_value.startswith("cat_"):
            # Выбрана категория - переходим на уровень ниже
            category_id = int(selected_value.replace("cat_", ""))
            new_select = PresetCategorySelect(self.embed, self.user, self.bot, self.guild, category_id)
            await new_select.load_options()

            # Удаляем старый select и кнопки пагинации
            items_to_remove = [item for item in self.view.children if isinstance(item, (PresetCategorySelect, PresetPrevPageButton, PresetNextPageButton))]
            for item in items_to_remove:
                self.view.remove_item(item)

            # Добавляем новый select
            self.view.add_item(new_select)

            # Добавляем кнопки пагинации если нужно
            if new_select.total_pages > 1:
                self.view.add_item(PresetPrevPageButton(new_select))
                self.view.add_item(PresetNextPageButton(new_select))

            await interaction.response.edit_message(view=self.view)
            return

        if selected_value.startswith("preset_"):
            # Выбран пресет - показываем подтверждение
            preset_id = int(selected_value.replace("preset_", ""))

            async with self.bot.db_pool.acquire() as conn:
                preset = await conn.fetchrow(
                    "SELECT preset_id, name, role_ids, description, emoji, category_id, rank_group_role_id FROM role_presets WHERE preset_id = $1",
                    preset_id
                )

            if not preset:
                await interaction.response.send_message("Пресет не найден.", ephemeral=True)
                return

            guild = interaction.guild
            member = guild.get_member(self.user.id)

            if not member:
                await interaction.response.send_message(
                    "Пользователь больше не на сервере.",
                    ephemeral=True
                )
                return

            # Импортируем базовую роль LSPD
            from bot.config import BASE_LSPD_ROLE_ID

            # Собираем все роли, которые будут выданы (в порядке иерархии)
            all_role_ids = list(preset['role_ids'])  # Роли из пресета

            # Добавляем групповую роль ранга
            if preset.get('rank_group_role_id'):
                all_role_ids.append(preset['rank_group_role_id'])

            # Получаем роль отдела из категории
            department_role_id = None
            if preset.get('category_id'):
                async with self.bot.db_pool.acquire() as conn:
                    category = await conn.fetchrow(
                        "SELECT parent_id, department_role_id FROM preset_categories WHERE category_id = $1",
                        preset['category_id']
                    )

                    if category:
                        if category['parent_id'] is not None:
                            # Это подкатегория, получаем department_role_id родителя
                            parent = await conn.fetchrow(
                                "SELECT department_role_id FROM preset_categories WHERE category_id = $1",
                                category['parent_id']
                            )
                            if parent and parent['department_role_id']:
                                department_role_id = parent['department_role_id']
                        else:
                            # Это корневая категория
                            department_role_id = category['department_role_id']

            # Добавляем роль отдела
            if department_role_id:
                all_role_ids.append(department_role_id)

            # Добавляем основную роль LSPD в самый конец (она самая низкая в иерархии)
            if BASE_LSPD_ROLE_ID:
                try:
                    base_role_id = int(BASE_LSPD_ROLE_ID)
                    all_role_ids.append(base_role_id)
                except (ValueError, TypeError):
                    pass

            # Удаляем дубликаты, сохраняя порядок
            seen = set()
            unique_role_ids = []
            for role_id in all_role_ids:
                if role_id not in seen:
                    seen.add(role_id)
                    unique_role_ids.append(role_id)

            # Получаем названия всех ролей
            role_names = []
            for role_id in unique_role_ids:
                role = guild.get_role(role_id)
                if role:
                    role_names.append(role.name)
                else:
                    role_names.append(f"ID {role_id}")

            confirm_view = ConfirmPresetView(
                preset=dict(preset),
                embed=self.embed,
                user=self.user,
                bot=self.bot,
                original_message=interaction.message,
                original_view=self.view
            )

            # Формируем сообщение со списком ролей
            roles_list = '\n'.join(f"• {role_name}" for role_name in role_names)
            message = f"Игроку будут выданы роли:\n\n{roles_list}"

            await interaction.response.send_message(
                message,
                view=confirm_view,
                ephemeral=True
            )


# ============== КНОПКИ ПАГИНАЦИИ ==============

class PresetPrevPageButton(discord.ui.Button):
    """Кнопка для перехода на предыдущую страницу пресетов"""

    def __init__(self, select: PresetCategorySelect):
        super().__init__(
            label="Пред",
            style=discord.ButtonStyle.gray,
            custom_id=f"preset_prev_{select.parent_category_id or 'root'}_{select.page}",
            row=2,
            disabled=(select.page == 0)
        )
        self.select = select

    async def callback(self, interaction: discord.Interaction):
        # Создаем новый select с предыдущей страницей
        new_select = PresetCategorySelect(
            self.select.embed,
            self.select.user,
            self.select.bot,
            self.select.guild,
            self.select.parent_category_id,
            self.select.page - 1
        )
        await new_select.load_options()

        # Удаляем старый select и кнопки пагинации
        items_to_remove = [item for item in self.view.children if isinstance(item, (PresetCategorySelect, PresetPrevPageButton, PresetNextPageButton))]
        for item in items_to_remove:
            self.view.remove_item(item)

        # Добавляем новый select
        self.view.add_item(new_select)

        # Добавляем новые кнопки пагинации если нужно
        if new_select.total_pages > 1:
            self.view.add_item(PresetPrevPageButton(new_select))
            self.view.add_item(PresetNextPageButton(new_select))

        await interaction.response.edit_message(view=self.view)


class PresetNextPageButton(discord.ui.Button):
    """Кнопка для перехода на следующую страницу пресетов"""

    def __init__(self, select: PresetCategorySelect):
        super().__init__(
            label="След",
            style=discord.ButtonStyle.gray,
            custom_id=f"preset_next_{select.parent_category_id or 'root'}_{select.page}",
            row=2,
            disabled=(select.page >= select.total_pages - 1)
        )
        self.select = select

    async def callback(self, interaction: discord.Interaction):
        # Создаем новый select с следующей страницей
        new_select = PresetCategorySelect(
            self.select.embed,
            self.select.user,
            self.select.bot,
            self.select.guild,
            self.select.parent_category_id,
            self.select.page + 1
        )
        await new_select.load_options()

        # Удаляем старый select и кнопки пагинации
        items_to_remove = [item for item in self.view.children if isinstance(item, (PresetCategorySelect, PresetPrevPageButton, PresetNextPageButton))]
        for item in items_to_remove:
            self.view.remove_item(item)

        # Добавляем новый select
        self.view.add_item(new_select)

        # Добавляем новые кнопки пагинации если нужно
        if new_select.total_pages > 1:
            self.view.add_item(PresetPrevPageButton(new_select))
            self.view.add_item(PresetNextPageButton(new_select))

        await interaction.response.edit_message(view=self.view)


# ============== ПОДТВЕРЖДЕНИЕ ПРЕСЕТА ==============

class ConfirmPresetView(discord.ui.View):
    """View для подтверждения применения пресета"""

    def __init__(self, preset: dict, embed: discord.Embed, user: discord.User, bot, original_message, original_view):
        super().__init__(timeout=60)
        self.preset = preset
        self.embed = embed
        self.user = user
        self.bot = bot
        self.original_message = original_message
        self.original_view = original_view

    @discord.ui.button(label="Да", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Подтверждение применения пресета"""
        # Немедленно подтверждаем получение взаимодействия
        await interaction.response.defer()

        from bot.config import BASE_LSPD_ROLE_ID

        guild = interaction.guild
        member = guild.get_member(self.user.id)

        preset_name = self.preset['name']
        role_ids = list(self.preset['role_ids'])  # Копируем список ролей из пресета

        logger.info(f"Пресет '{preset_name}' применяется к {self.user.display_name} ({self.user.id}) администратором {interaction.user.display_name}")

        if not member:
            logger.warning(f"Пользователь {self.user.display_name} ({self.user.id}) не найден на сервере")
            await interaction.edit_original_response(content="Пользователь больше не на сервере.", view=None)
            return

        # Получаем корневую категорию (отдел) для получения department_role_id
        department_role_id = None
        if self.preset.get('category_id'):
            async with self.bot.db_pool.acquire() as conn:
                # Получаем корневую категорию (отдел)
                # Если это подкатегория, получаем её родителя
                category = await conn.fetchrow(
                    "SELECT category_id, parent_id, department_role_id FROM preset_categories WHERE category_id = $1",
                    self.preset['category_id']
                )

                if category:
                    if category['parent_id'] is not None:
                        # Это подкатегория, получаем родительскую категорию (отдел)
                        parent_category = await conn.fetchrow(
                            "SELECT department_role_id FROM preset_categories WHERE category_id = $1",
                            category['parent_id']
                        )
                        if parent_category:
                            department_role_id = parent_category['department_role_id']
                    else:
                        # Это корневая категория (отдел)
                        department_role_id = category['department_role_id']

        # Формируем итоговый список ролей: Базовая роль LSPD + Роль отдела + Групповая роль + Роли из пресета
        all_role_ids = []

        # 1. Добавляем базовую роль LSPD
        all_role_ids.append(BASE_LSPD_ROLE_ID)

        # 2. Добавляем роль отдела (если есть)
        if department_role_id:
            all_role_ids.append(department_role_id)

        # 3. Добавляем групповую роль ранга (если есть)
        if self.preset.get('rank_group_role_id'):
            all_role_ids.append(self.preset['rank_group_role_id'])

        # 4. Добавляем роли из пресета (ранг)
        all_role_ids.extend(role_ids)

        # Выдача ролей
        success_roles = []
        failed_roles = []

        for role_id in all_role_ids:
            role = guild.get_role(role_id)
            if not role:
                failed_roles.append(f"ID {role_id} (роль не найдена)")
                logger.warning(f"Роль с ID {role_id} не найдена на сервере")
                continue

            try:
                await member.add_roles(role, reason=f"Пресет '{preset_name}' применен {interaction.user.display_name}")
                success_roles.append(role.name)
                logger.info(f"Роль '{role.name}' выдана пользователю {member.display_name}")
            except discord.Forbidden:
                failed_roles.append(f"{role.name} (нет прав)")
                logger.error(f"Нет прав для выдачи роли '{role.name}' пользователю {member.display_name}")
            except discord.HTTPException as e:
                failed_roles.append(f"{role.name} (ошибка: {e})")
                logger.error(f"HTTP ошибка при выдаче роли '{role.name}': {e}")

        # Обновление embed
        self.embed.color = discord.Color.green()

        if success_roles:
            footer_text = f"{', '.join(success_roles)} выданы пользователем {interaction.user.display_name}"
        else:
            footer_text = f"Роли выданы пользователем {interaction.user.display_name}"

        if failed_roles:
            footer_text += f"\n⚠ Не удалось выдать: {', '.join(failed_roles)}"

        self.embed.set_footer(text=footer_text)

        # Очистка компонентов и обновление оригинального сообщения
        self.original_view.clear_items()
        await self.original_message.edit(embed=self.embed, view=self.original_view)

        # Обновление БД
        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = 'approved', finished_by = $1, finished_at = $2 WHERE message_id = $3",
                interaction.user.id,
                datetime.now(),
                self.original_message.id
            )

        # Уведомление пользователя
        try:
            msg = f"Ваш запрос на получение ролей был одобрен!\nВыданы роли: {', '.join(success_roles)}"
            if failed_roles:
                msg += f"\n\nНекоторые роли не были выданы автоматически, обратитесь к администратору."
            await self.user.send(msg)
        except discord.Forbidden:
            pass

        # Обновление ephemeral сообщения
        if success_roles:
            response_msg = f"Были выданы роли:\n{', '.join(success_roles)}"
        else:
            response_msg = "Ни одна роль не была выдана."

        if failed_roles:
            response_msg += f"\n\n⚠ Ошибки: {', '.join(failed_roles)}"

        await interaction.edit_original_response(content=response_msg, view=None)

    @discord.ui.button(label="Нет", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Отмена применения пресета"""
        await interaction.response.defer()
        await interaction.edit_original_response(content="Отменено.", view=None)


# ============== КНОПКА НАСТРОЕК ==============

class SettingsButton(discord.ui.Button):
    def __init__(self, embed: discord.Embed, user: discord.User, bot):
        super().__init__(
            label="Настройки",
            style=discord.ButtonStyle.gray,
            custom_id="settings_button",
            emoji="⚙",
            row=0
        )
        self.embed = embed
        self.user = user
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        try:
            if not await is_preset_admin(interaction.user):
                await interaction.response.send_message(
                    "У вас нет прав для управления настройками.",
                    ephemeral=True
                )
                return

            # Открываем главное меню настроек
            view = SettingsMenuView(self.bot, interaction.guild, self.embed, self.user, interaction.message, self.view)

            embed = discord.Embed(
                title="⚙ Настройки",
                description="Выберите раздел настроек",
                color=discord.Color.blue()
            )

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка в кнопке настроек: {e}", exc_info=True)
            try:
                await interaction.response.send_message(
                    f"Произошла ошибка: {e}",
                    ephemeral=True
                )
            except:
                pass


# ============== ГЛАВНОЕ МЕНЮ НАСТРОЕК ==============

class SettingsMenuView(discord.ui.View):
    """Главное меню настроек"""

    def __init__(self, bot, guild, embed, user, original_message, original_view):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.embed = embed
        self.user = user
        self.original_message = original_message
        self.original_view = original_view

    @discord.ui.button(label="Управление", style=discord.ButtonStyle.primary, emoji="📁", row=0)
    async def management_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = CategoryManagementView(self.bot, self, interaction.guild)
        await view.refresh_categories()

        embed = discord.Embed(
            title="📁 Управление категориями и пресетами",
            description="Категории позволяют группировать пресеты.\n"
                        "Структура: **Категория → Подкатегория → Пресет**\n\n"
                        "Выберите категорию для просмотра пресетов или создайте новую",
            color=discord.Color.blue()
        )

        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Настройка причин отказа", style=discord.ButtonStyle.primary, emoji="📋", row=1)
    async def reject_reasons_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = RejectReasonsManagementView(self.bot, self)
        await view.refresh_reasons()

        embed = discord.Embed(
            title="📋 Настройка причин отказа",
            description="Выберите причину для редактирования или создайте новую\n\n"
                        "💡 **Подсказка:** Вы можете настроить текст сообщения, которое будет отправлено пользователю в ЛС",
            color=discord.Color.blue()
        )

        await interaction.response.edit_message(embed=embed, view=view)


# ============== УПРАВЛЕНИЕ КАТЕГОРИЯМИ ==============

class CategoryManagementView(discord.ui.View):
    """View для управления категориями пресетов"""

    def __init__(self, bot, settings_menu_view, guild):
        super().__init__(timeout=300)
        self.bot = bot
        self.settings_menu_view = settings_menu_view
        self.guild = guild
        self.categories = []

    async def refresh_categories(self):
        """Загрузка корневых категорий из БД"""
        async with self.bot.db_pool.acquire() as conn:
            # Загружаем только корневые категории (parent_id IS NULL)
            self.categories = await conn.fetch(
                """
                SELECT c.category_id, c.name, c.parent_id, c.emoji,
                       NULL as parent_name,
                       (SELECT COUNT(*) FROM role_presets WHERE category_id = c.category_id) as preset_count,
                       (SELECT COUNT(*) FROM preset_categories WHERE parent_id = c.category_id) as subcategory_count
                FROM preset_categories c
                WHERE c.parent_id IS NULL
                ORDER BY c.name
                """
            )

        self.clear_items()
        self.add_item(CategoryManagementSelect(self.categories, self.bot, self, self.guild))
        self.add_item(BackToSettingsMenuButton(self.settings_menu_view))


class CategoryManagementSelect(discord.ui.Select):
    """Select для управления категориями"""

    def __init__(self, categories: list, bot, parent_view, guild):
        self.categories_data = {str(c['category_id']): c for c in categories}
        self.bot = bot
        self.parent_view = parent_view
        self.guild = guild

        options = [
            discord.SelectOption(
                label="Создать категорию",
                value="create_category",
                emoji="➕",
                description="Создать новую корневую категорию"
            )
        ]

        for cat in categories[:24]:
            label = cat['name']
            default_emoji = "📁"

            # Используем пользовательский эмодзи, если указан
            emoji_str = cat.get('emoji')
            emoji = default_emoji
            if emoji_str:
                parsed_emoji = parse_emoji(emoji_str, guild)
                if parsed_emoji:
                    emoji = parsed_emoji

            if len(label) > 100:
                label = label[:97] + "..."

            # Показываем количество подкатегорий и пресетов
            subcats = cat.get('subcategory_count', 0)
            presets = cat.get('preset_count', 0)
            description = f"Подкатегорий: {subcats} | Пресетов: {presets}"

            options.append(discord.SelectOption(
                label=label,
                value=str(cat['category_id']),
                description=description,
                emoji=emoji
            ))

        super().__init__(
            placeholder="Выберите действие или категорию...",
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]

        if selected_value == "create_category":
            # Сразу открываем модальное окно для создания корневой категории
            modal = CategoryCreateModal(self.bot, self.parent_view, parent_id=None)
            await interaction.response.send_modal(modal)
            return

        category = self.categories_data.get(selected_value)
        if not category:
            await interaction.response.send_message("Категория не найдена.", ephemeral=True)
            return

        # Показываем содержимое категории (подкатегории и пресеты)
        view = CategoryContentView(category, self.bot, self.parent_view, interaction.guild)
        await view.load_content()

        emoji = category.get('emoji')
        emoji_str = '📁'
        if emoji:
            parsed_emoji = parse_emoji(emoji, interaction.guild)
            if parsed_emoji:
                emoji_str = str(parsed_emoji)
        embed = discord.Embed(
            title=f"{emoji_str} {category['name']}",
            description=f"Подкатегорий: {category.get('subcategory_count', 0)} | Пресетов: {category.get('preset_count', 0)}\n\n"
                        f"Выберите подкатегорию, пресет или действие",
            color=discord.Color.blue()
        )

        await interaction.response.edit_message(embed=embed, view=view)


class CategoryContentView(discord.ui.View):
    """View для отображения содержимого категории (подкатегории + пресеты)"""

    def __init__(self, category: dict, bot, parent_view, guild):
        super().__init__(timeout=300)
        # Преобразуем asyncpg.Record в dict для возможности изменения
        self.category = dict(category) if hasattr(category, '__getitem__') else category
        self.bot = bot
        self.parent_view = parent_view
        self.guild = guild
        self.subcategories = []
        self.presets = []

    async def load_content(self):
        """Загрузка подкатегорий и пресетов"""
        async with self.bot.db_pool.acquire() as conn:
            # Загружаем подкатегории
            self.subcategories = await conn.fetch(
                """
                SELECT c.category_id, c.name, c.emoji, c.parent_id,
                       p.name as parent_name,
                       (SELECT COUNT(*) FROM role_presets WHERE category_id = c.category_id) as preset_count,
                       (SELECT COUNT(*) FROM preset_categories WHERE parent_id = c.category_id) as subcategory_count
                FROM preset_categories c
                LEFT JOIN preset_categories p ON c.parent_id = p.category_id
                WHERE c.parent_id = $1
                ORDER BY c.name
                """,
                self.category['category_id']
            )

            # Загружаем пресеты
            self.presets = await conn.fetch(
                """
                SELECT preset_id, name, emoji, role_ids
                FROM role_presets
                WHERE category_id = $1
                ORDER BY sort_order NULLS LAST, name
                """,
                self.category['category_id']
            )

        self.clear_items()
        select = CategoryContentSelect(self.subcategories, self.presets, self.bot, self)
        self.add_item(select)

        # Кнопка "Добавить подкатегорию" только для корневых категорий (parent_id IS NULL)
        if self.category.get('parent_id') is None:
            self.add_item(AddSubcategoryButton(self.category, self.bot, self))

        self.add_item(AddPresetButton(self.category, self.bot, self.guild, self))
        self.add_item(EditCategoryButton(self.category, self.bot, self))
        self.add_item(DeleteCategoryButton(self.category, self.bot, self))
        self.add_item(BackToCategoriesButton(self.parent_view))

        # Добавляем кнопки пагинации если нужно
        if select.total_pages > 1:
            self.add_item(CategoryContentPrevPageButton(select))
            self.add_item(CategoryContentNextPageButton(select))


class CategoryContentSelect(discord.ui.Select):
    """Select для выбора подкатегории или пресета"""

    def __init__(self, subcategories: list, presets: list, bot, parent_view, page=0):
        self.all_subcategories = list(subcategories)
        self.all_presets = list(presets)
        self.subcategories_data = {f"subcat_{s['category_id']}": s for s in subcategories}
        self.presets_data = {f"preset_{p['preset_id']}": p for p in presets}
        self.bot = bot
        self.parent_view = parent_view
        self.page = page

        options = []

        # Сначала показываем все подкатегории (они не пагинируются)
        for subcat in subcategories:
            emoji_str = subcat.get('emoji')
            emoji = "📂"
            if emoji_str:
                parsed_emoji = parse_emoji(emoji_str, parent_view.guild)
                if parsed_emoji:
                    emoji = parsed_emoji
            subcats = subcat.get('subcategory_count', 0)
            presets_count = subcat.get('preset_count', 0)
            options.append(discord.SelectOption(
                label=subcat['name'],
                value=f"subcat_{subcat['category_id']}",
                description=f"Подкатегорий: {subcats} | Пресетов: {presets_count}",
                emoji=emoji
            ))

        # Вычисляем количество элементов на странице
        max_options = 25 - len(subcategories)
        start_idx = self.page * max_options
        end_idx = start_idx + max_options

        # Добавляем пресеты для текущей страницы
        presets_on_page = presets[start_idx:end_idx]
        for preset in presets_on_page:
            emoji_str = preset.get('emoji')
            emoji = None
            if emoji_str:
                parsed_emoji = parse_emoji(emoji_str, parent_view.guild)
                if parsed_emoji:
                    emoji = parsed_emoji

            # Используем description из базы данных, если есть
            preset_description = preset.get('description', None)

            options.append(discord.SelectOption(
                label=preset['name'],
                value=f"preset_{preset['preset_id']}",
                description=preset_description if preset_description else None,
                emoji=emoji
            ))

        if not options:
            options.append(discord.SelectOption(
                label="Пусто",
                value="empty",
                description="Нет подкатегорий и пресетов",
                emoji="📭"
            ))

        # Вычисляем общее количество страниц
        total_presets = len(presets)
        self.total_pages = (total_presets + max_options - 1) // max_options if total_presets > 0 else 1

        # Формируем placeholder с путем к текущей категории
        current_category = parent_view.category
        if current_category.get('parent_id') is None:
            # Корневая категория
            category_path = current_category['name']
        else:
            # Подкатегория - показываем путь: родитель -> категория
            parent_name = current_category.get('parent_name', 'Категория')
            category_path = f"{parent_name} → {current_category['name']}"

        placeholder = category_path
        if self.total_pages > 1:
            placeholder = f"{category_path} (Стр. {self.page + 1}/{self.total_pages})"

        # Ограничиваем длину placeholder (Discord лимит 150 символов)
        if len(placeholder) > 150:
            placeholder = placeholder[:147] + "..."

        super().__init__(
            placeholder=placeholder,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]

        if selected_value == "empty":
            await interaction.response.send_message(
                "В этой категории пока нет подкатегорий и пресетов.",
                ephemeral=True
            )
            return

        # Проверяем, это подкатегория или пресет
        if selected_value.startswith("subcat_"):
            subcat = self.subcategories_data.get(selected_value)
            if not subcat:
                await interaction.response.send_message("Подкатегория не найдена.", ephemeral=True)
                return

            # Показываем содержимое подкатегории
            view = CategoryContentView(subcat, self.bot, self.parent_view.parent_view, interaction.guild)
            await view.load_content()

            emoji = subcat.get('emoji')
            emoji_str = '📂'
            if emoji:
                parsed_emoji = parse_emoji(emoji, interaction.guild)
                if parsed_emoji:
                    emoji_str = str(parsed_emoji)

            # Формируем заголовок с путем
            parent_name = subcat.get('parent_name', 'Категория')
            title = f"{emoji_str} {parent_name} → {subcat['name']}"

            embed = discord.Embed(
                title=title,
                description=f"Подкатегорий: {subcat.get('subcategory_count', 0)} | Пресетов: {subcat.get('preset_count', 0)}\n\n"
                            f"Выберите подкатегорию, пресет или действие",
                color=discord.Color.blue()
            )

            await interaction.response.edit_message(embed=embed, view=view)

        elif selected_value.startswith("preset_"):
            preset = self.presets_data.get(selected_value)
            if not preset:
                await interaction.response.send_message("Пресет не найден.", ephemeral=True)
                return

            # Показываем меню редактирования пресета
            view = PresetEditView(preset, self.bot, interaction.guild, self.parent_view)

            role_names = []
            for role_id in preset['role_ids']:
                role = interaction.guild.get_role(role_id)
                if role:
                    role_names.append(f"✅ {role.name}")
                else:
                    role_names.append(f"❌ ID {role_id} (удалена)")

            emoji = preset.get('emoji')
            emoji_str = ''
            if emoji:
                parsed_emoji = parse_emoji(emoji, interaction.guild)
                if parsed_emoji:
                    emoji_str = f"{str(parsed_emoji)} "
            embed = discord.Embed(
                title=f"{emoji_str}{preset['name']}",
                description="Выберите действие для редактирования пресета",
                color=discord.Color.blue()
            )
            embed.add_field(name="Роли", value="\n".join(role_names), inline=False)

            await interaction.response.edit_message(embed=embed, view=view)


class CategoryContentPrevPageButton(discord.ui.Button):
    """Кнопка для перехода на предыдущую страницу пресетов в категории"""

    def __init__(self, select: CategoryContentSelect):
        super().__init__(
            label="Пред",
            style=discord.ButtonStyle.gray,
            custom_id=f"category_content_prev_{select.page}",
            row=3,
            disabled=(select.page == 0)
        )
        self.select = select

    async def callback(self, interaction: discord.Interaction):
        # Создаем новый select с предыдущей страницей
        new_select = CategoryContentSelect(
            self.select.all_subcategories,
            self.select.all_presets,
            self.select.bot,
            self.select.parent_view,
            self.select.page - 1
        )

        # Удаляем старый select и кнопки пагинации
        items_to_remove = [item for item in self.view.children if isinstance(item, (CategoryContentSelect, CategoryContentPrevPageButton, CategoryContentNextPageButton))]
        for item in items_to_remove:
            self.view.remove_item(item)

        # Добавляем новый select (в начало, row=0)
        self.view.children.insert(0, new_select)

        # Добавляем новые кнопки пагинации если нужно
        if new_select.total_pages > 1:
            self.view.add_item(CategoryContentPrevPageButton(new_select))
            self.view.add_item(CategoryContentNextPageButton(new_select))

        await interaction.response.edit_message(view=self.view)


class CategoryContentNextPageButton(discord.ui.Button):
    """Кнопка для перехода на следующую страницу пресетов в категории"""

    def __init__(self, select: CategoryContentSelect):
        super().__init__(
            label="След",
            style=discord.ButtonStyle.gray,
            custom_id=f"category_content_next_{select.page}",
            row=3,
            disabled=(select.page >= select.total_pages - 1)
        )
        self.select = select

    async def callback(self, interaction: discord.Interaction):
        # Создаем новый select с следующей страницей
        new_select = CategoryContentSelect(
            self.select.all_subcategories,
            self.select.all_presets,
            self.select.bot,
            self.select.parent_view,
            self.select.page + 1
        )

        # Удаляем старый select и кнопки пагинации
        items_to_remove = [item for item in self.view.children if isinstance(item, (CategoryContentSelect, CategoryContentPrevPageButton, CategoryContentNextPageButton))]
        for item in items_to_remove:
            self.view.remove_item(item)

        # Добавляем новый select (в начало, row=0)
        self.view.children.insert(0, new_select)

        # Добавляем новые кнопки пагинации если нужно
        if new_select.total_pages > 1:
            self.view.add_item(CategoryContentPrevPageButton(new_select))
            self.view.add_item(CategoryContentNextPageButton(new_select))

        await interaction.response.edit_message(view=self.view)


class AddSubcategoryButton(discord.ui.Button):
    """Кнопка для добавления подкатегории"""

    def __init__(self, category: dict, bot, parent_view):
        super().__init__(
            label="Добавить подкатегорию",
            style=discord.ButtonStyle.success,
            emoji="➕",
            row=1
        )
        self.category = category
        self.bot = bot
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        modal = CategoryCreateModal(self.bot, self.parent_view, parent_id=self.category['category_id'])
        await interaction.response.send_modal(modal)


class AddPresetButton(discord.ui.Button):
    """Кнопка для добавления пресета"""

    def __init__(self, category: dict, bot, guild, parent_view):
        super().__init__(
            label="Добавить пресет",
            style=discord.ButtonStyle.success,
            emoji="➕",
            row=1
        )
        self.category = category
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        modal = PresetCreateModal(self.bot, self.guild, self.parent_view, self.category['category_id'])
        await interaction.response.send_modal(modal)


class EditCategoryButton(discord.ui.Button):
    """Кнопка для редактирования категории"""

    def __init__(self, category: dict, bot, parent_view):
        super().__init__(
            label="Редактировать",
            style=discord.ButtonStyle.primary,
            emoji="✏",
            row=2
        )
        self.category = category
        self.bot = bot
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        modal = CategoryRenameModal(self.category, self.bot, self.parent_view)
        await interaction.response.send_modal(modal)


class DeleteCategoryButton(discord.ui.Button):
    """Кнопка для удаления категории"""

    def __init__(self, category: dict, bot, parent_view):
        super().__init__(
            label="Удалить",
            style=discord.ButtonStyle.danger,
            emoji="🗑",
            row=2
        )
        self.category = category
        self.bot = bot
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        view = ConfirmDeleteCategoryView(self.category, self.bot, self.parent_view)
        embed = discord.Embed(
            title="⚠ Подтверждение удаления",
            description=f"Вы уверены, что хотите удалить категорию **«{self.category['name']}»**?\n\n"
                        f"⚠ Это также удалит все подкатегории внутри!",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=view)


class CreateCategoryTypeView(discord.ui.View):
    """View для выбора типа создаваемой категории"""

    def __init__(self, bot, parent_view):
        super().__init__(timeout=60)
        self.bot = bot
        self.parent_view = parent_view

    @discord.ui.button(label="Корневая категория", style=discord.ButtonStyle.primary, emoji="📁", row=0)
    async def root_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CategoryCreateModal(self.bot, self.parent_view, parent_id=None)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Подкатегория", style=discord.ButtonStyle.primary, emoji="📂", row=0)
    async def sub_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Показываем выбор родительской категории
        view = SelectParentCategoryView(self.bot, self.parent_view, interaction.guild)
        await view.load_root_categories()

        await interaction.response.edit_message(
            content="**Выберите родительскую категорию:**",
            embed=None,
            view=view
        )

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.gray, emoji="✖", row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        await self.parent_view.refresh_categories()
        embed = discord.Embed(
            title="📁 Управление категориями",
            description="Категории позволяют группировать пресеты.\n"
                        "Структура: **Категория → Подкатегория → Пресет**",
            color=discord.Color.blue()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)


class SelectParentCategoryView(discord.ui.View):
    """View для выбора родительской категории"""

    def __init__(self, bot, parent_view, guild):
        super().__init__(timeout=120)
        self.bot = bot
        self.parent_view = parent_view
        self.guild = guild

    async def load_root_categories(self):
        """Загрузка корневых категорий"""
        async with self.bot.db_pool.acquire() as conn:
            categories = await conn.fetch(
                "SELECT category_id, name, emoji FROM preset_categories WHERE parent_id IS NULL ORDER BY name"
            )

        if not categories:
            self.add_item(discord.ui.Button(label="Нет корневых категорий", disabled=True, row=0))
        else:
            options = []
            for cat in categories[:25]:
                # Используем пользовательский эмодзи, если указан
                emoji_str = cat.get('emoji')
                emoji = "📁"
                if emoji_str:
                    parsed_emoji = parse_emoji(emoji_str, self.guild)
                    if parsed_emoji:
                        emoji = parsed_emoji

                options.append(discord.SelectOption(
                    label=cat['name'],
                    value=str(cat['category_id']),
                    emoji=emoji
                ))

            select = ParentCategorySelect(options, self.bot, self.parent_view)
            self.add_item(select)

        self.add_item(BackToCategoriesButton(self.parent_view))


class ParentCategorySelect(discord.ui.Select):
    """Select для выбора родительской категории"""

    def __init__(self, options, bot, parent_view):
        super().__init__(
            placeholder="Выберите родительскую категорию...",
            options=options,
            row=0
        )
        self.bot = bot
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        parent_id = int(self.values[0])
        modal = CategoryCreateModal(self.bot, self.parent_view, parent_id=parent_id)
        await interaction.response.send_modal(modal)


class CategoryCreateModal(discord.ui.Modal, title="Создать категорию"):
    """Модальное окно для создания категории"""

    category_name = discord.ui.TextInput(
        label="Название категории",
        placeholder="Например: Rampart Area",
        required=True,
        max_length=100
    )

    emoji = discord.ui.TextInput(
        label="Эмодзи (опционально)",
        placeholder="📁 или ID кастомного: 1234567890",
        required=False,
        max_length=50
    )

    department_role_id_input = discord.ui.TextInput(
        label="ID роли отдела (для корневых категорий)",
        placeholder="Оставьте пустым для подкатегорий",
        required=False,
        max_length=30
    )

    def __init__(self, bot, parent_view, parent_id=None):
        super().__init__()
        self.bot = bot
        self.parent_view = parent_view
        self.parent_id = parent_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Проверка: если указан parent_id, убедимся что это корневая категория (max 2 уровня)
            if self.parent_id is not None:
                async with self.bot.db_pool.acquire() as conn:
                    parent_category = await conn.fetchrow(
                        "SELECT parent_id FROM preset_categories WHERE category_id = $1",
                        self.parent_id
                    )
                    if parent_category and parent_category['parent_id'] is not None:
                        await interaction.response.send_message(
                            "❌ Нельзя создать подкатегорию подкатегории! Максимум 2 уровня: Категория → Подкатегория.",
                            ephemeral=True
                        )
                        return

            emoji_input = self.emoji.value.strip() if self.emoji.value else None
            # Нормализуем эмодзи для сохранения в БД
            emoji_value = normalize_emoji_for_storage(emoji_input, interaction.guild) if emoji_input else None

            # Парсинг ID роли отдела
            department_role_id = None
            if self.department_role_id_input.value and self.department_role_id_input.value.strip():
                try:
                    department_role_id = int(self.department_role_id_input.value.strip())
                    # Проверяем существование роли
                    role = interaction.guild.get_role(department_role_id)
                    if not role:
                        await interaction.response.send_message(
                            f"⚠️ Роль с ID {department_role_id} не найдена на сервере!",
                            ephemeral=True
                        )
                        return
                except ValueError:
                    await interaction.response.send_message(
                        "❌ Неверный формат ID роли! Используйте только числа.",
                        ephemeral=True
                    )
                    return

            ranks_created_count = 0  # Счетчик созданных рангов

            async with self.bot.db_pool.acquire() as conn:
                # Создаем категорию и получаем её ID
                new_category_id = await conn.fetchval(
                    "INSERT INTO preset_categories (name, parent_id, created_by, created_at, emoji, department_role_id) "
                    "VALUES ($1, $2, $3, NOW(), $4, $5) RETURNING category_id",
                    self.category_name.value,
                    self.parent_id,
                    interaction.user.id,
                    emoji_value,
                    department_role_id
                )

                # Если это подкатегория (parent_id не None), автоматически создаём ранги
                if self.parent_id is not None and LSPD_RANKS:
                    for i, rank_name in enumerate(LSPD_RANKS):
                        # Ищем роль Discord по названию ранга
                        role = discord.utils.get(interaction.guild.roles, name=rank_name)
                        if not role:
                            logger.warning(f"Роль '{rank_name}' не найдена при создании подкатегории '{self.category_name.value}'")
                            continue

                        # Определяем групповую роль
                        group_role_name = RANK_TO_GROUP_ROLE.get(rank_name)
                        group_role_id = None
                        if group_role_name:
                            group_role = discord.utils.get(interaction.guild.roles, name=group_role_name)
                            if group_role:
                                group_role_id = group_role.id

                        # Создаём пресет ранга
                        await conn.execute(
                            "INSERT INTO role_presets (name, role_ids, created_by, created_at, description, category_id, rank_group_role_id, sort_order) "
                            "VALUES ($1, $2, $3, NOW(), $4, $5, $6, $7)",
                            rank_name,
                            [role.id],
                            interaction.user.id,
                            f"Ранг LSPD: {rank_name}",
                            new_category_id,
                            group_role_id,
                            i  # Порядок сортировки
                        )
                        ranks_created_count += 1

                    logger.info(f"Создано {ranks_created_count} рангов для подкатегории '{self.category_name.value}'")

            logger.info(f"Категория '{self.category_name.value}' создана пользователем {interaction.user.display_name}")

            # Парсим эмодзи для отображения
            emoji_str = ""
            if emoji_value:
                parsed_emoji = parse_emoji(emoji_value, interaction.guild)
                if parsed_emoji:
                    emoji_str = f"{parsed_emoji} "

            # Проверяем, является ли parent_view CategoryContentView
            if hasattr(self.parent_view, 'load_content'):
                # Это CategoryContentView
                await self.parent_view.load_content()
                parent_emoji = self.parent_view.category.get('emoji')
                parent_emoji_str = '📁'
                if parent_emoji:
                    parsed_parent_emoji = parse_emoji(parent_emoji, interaction.guild)
                    if parsed_parent_emoji:
                        parent_emoji_str = str(parsed_parent_emoji)
                description_text = f"Подкатегория {emoji_str}**«{self.category_name.value}»** создана!"
                if ranks_created_count > 0:
                    description_text += f"\n\nАвтоматически созданы ранги: {ranks_created_count} из {len(LSPD_RANKS)}"
                embed = discord.Embed(
                    title=f"{parent_emoji_str} {self.parent_view.category['name']}",
                    description=description_text,
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(content=None, embed=embed, view=self.parent_view)
            else:
                # Это CategoryManagementView
                await self.parent_view.refresh_categories()
                embed = discord.Embed(
                    title="📁 Управление категориями и пресетами",
                    description=f"Категория {emoji_str}**«{self.category_name.value}»** создана!",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(content=None, embed=embed, view=self.parent_view)

        except Exception as e:
            logger.error(f"Ошибка при создании категории: {e}", exc_info=True)
            await interaction.response.send_message(f"Ошибка: {e}", ephemeral=True)


class CategoryEditView(discord.ui.View):
    """View для редактирования категории"""

    def __init__(self, category: dict, bot, parent_view):
        super().__init__(timeout=300)
        # Преобразуем asyncpg.Record в dict для возможности изменения
        self.category = dict(category) if hasattr(category, '__getitem__') else category
        self.bot = bot
        self.parent_view = parent_view

    @discord.ui.button(label="Переименовать", style=discord.ButtonStyle.primary, emoji="✏", row=0)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CategoryRenameModal(self.category, self.bot, self.parent_view)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Создать подкатегорию", style=discord.ButtonStyle.primary, emoji="📂", row=0)
    async def create_sub(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Только для корневых категорий
        if self.category['parent_id'] is not None:
            await interaction.response.send_message(
                "Подкатегории можно создавать только для корневых категорий (максимум 2 уровня).",
                ephemeral=True
            )
            return
        modal = CategoryCreateModal(self.bot, self.parent_view, parent_id=self.category['category_id'])
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.danger, emoji="🗑", row=1)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ConfirmDeleteCategoryView(self.category, self.bot, self.parent_view)
        await interaction.response.edit_message(
            content=f"**Удалить категорию «{self.category['name']}»?**\n\n"
                    f"⚠️ Все подкатегории также будут удалены!\n"
                    f"Пресеты останутся, но будут без категории.",
            embed=None,
            view=view
        )

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.gray, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Используем defer для предотвращения таймаута
        await interaction.response.defer()

        # Загружаем данные
        await self.parent_view.refresh_categories()

        # Обновляем сообщение
        embed = discord.Embed(
            title="📁 Управление категориями",
            description="Категории позволяют группировать пресеты.",
            color=discord.Color.blue()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)


class CategoryRenameModal(discord.ui.Modal, title="Редактировать категорию"):
    """Модальное окно для редактирования категории"""

    def __init__(self, category: dict, bot, parent_view):
        super().__init__()
        # Преобразуем asyncpg.Record в dict для возможности изменения
        self.category = dict(category) if hasattr(category, '__getitem__') else category
        self.bot = bot
        self.parent_view = parent_view

        self.category_name = discord.ui.TextInput(
            label="Название",
            default=self.category['name'],
            required=True,
            max_length=100
        )
        self.add_item(self.category_name)

        self.emoji = discord.ui.TextInput(
            label="Эмодзи (опционально)",
            placeholder="📁 или ID кастомного: 1234567890",
            default=self.category.get('emoji', ''),
            required=False,
            max_length=50
        )
        self.add_item(self.emoji)

        # Поле для ID роли отдела (только для корневых категорий)
        dept_role_default = str(self.category.get('department_role_id', '')) if self.category.get('department_role_id') else ''
        self.department_role_id_input = discord.ui.TextInput(
            label="ID роли отдела (для корневых)",
            placeholder="Оставьте пустым чтобы убрать",
            default=dept_role_default,
            required=False,
            max_length=30
        )
        self.add_item(self.department_role_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            emoji_input = self.emoji.value.strip() if self.emoji.value else None
            # Нормализуем эмодзи для сохранения в БД
            emoji_value = normalize_emoji_for_storage(emoji_input, interaction.guild) if emoji_input else None

            # Парсинг ID роли отдела
            department_role_id = None
            if self.department_role_id_input.value and self.department_role_id_input.value.strip():
                try:
                    department_role_id = int(self.department_role_id_input.value.strip())
                    # Проверяем существование роли
                    role = interaction.guild.get_role(department_role_id)
                    if not role:
                        await interaction.response.send_message(
                            f"⚠️ Роль с ID {department_role_id} не найдена на сервере!",
                            ephemeral=True
                        )
                        return
                except ValueError:
                    await interaction.response.send_message(
                        "❌ Неверный формат ID роли! Используйте только числа.",
                        ephemeral=True
                    )
                    return

            async with self.bot.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE preset_categories SET name = $1, emoji = $2, department_role_id = $3 WHERE category_id = $4",
                    self.category_name.value,
                    emoji_value,
                    department_role_id,
                    self.category['category_id']
                )

            # Парсим эмодзи для отображения
            emoji_str = ""
            if emoji_value:
                parsed_emoji = parse_emoji(emoji_value, interaction.guild)
                if parsed_emoji:
                    emoji_str = f"{parsed_emoji} "

            # Проверяем, является ли parent_view CategoryContentView
            if hasattr(self.parent_view, 'load_content'):
                # Это CategoryContentView - обновляем её категорию
                self.parent_view.category['name'] = self.category_name.value
                self.parent_view.category['emoji'] = emoji_value
                await self.parent_view.load_content()
                embed = discord.Embed(
                    title=f"{emoji_str}{self.category_name.value}",
                    description=f"Категория обновлена!",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(content=None, embed=embed, view=self.parent_view)
            else:
                # Это CategoryManagementView
                await self.parent_view.refresh_categories()
                embed = discord.Embed(
                    title="📁 Управление категориями и пресетами",
                    description=f"Категория {emoji_str}**«{self.category_name.value}»** обновлена!",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(content=None, embed=embed, view=self.parent_view)

        except Exception as e:
            await interaction.response.send_message(f"Ошибка: {e}", ephemeral=True)


class ConfirmDeleteCategoryView(discord.ui.View):
    """View для подтверждения удаления категории"""

    def __init__(self, category: dict, bot, parent_view):
        super().__init__(timeout=60)
        # Преобразуем asyncpg.Record в dict для возможности изменения
        self.category = dict(category) if hasattr(category, '__getitem__') else category
        self.bot = bot
        self.parent_view = parent_view

    @discord.ui.button(label="Да, удалить", style=discord.ButtonStyle.danger, emoji="🗑")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM preset_categories WHERE category_id = $1",
                self.category['category_id']
            )

        logger.info(f"Категория '{self.category['name']}' удалена пользователем {interaction.user.display_name}")

        # Проверяем, является ли parent_view CategoryContentView
        if hasattr(self.parent_view, 'load_content'):
            # Это CategoryContentView - возвращаемся к списку категорий
            real_parent = self.parent_view.parent_view
            await real_parent.refresh_categories()
            embed = discord.Embed(
                title="📁 Управление категориями и пресетами",
                description=f"Категория **«{self.category['name']}»** удалена!",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(content=None, embed=embed, view=real_parent)
        else:
            # Это CategoryManagementView
            await self.parent_view.refresh_categories()
            embed = discord.Embed(
                title="📁 Управление категориями и пресетами",
                description=f"Категория **«{self.category['name']}»** удалена!",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.gray, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        # Проверяем, является ли parent_view CategoryContentView
        if hasattr(self.parent_view, 'load_content'):
            # Это CategoryContentView
            await self.parent_view.load_content()
            emoji_str = self.parent_view.category.get('emoji', '📁')
            embed = discord.Embed(
                title=f"{emoji_str} {self.parent_view.category['name']}",
                description="Удаление отменено",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)
        else:
            # Это CategoryManagementView
            await self.parent_view.refresh_categories()
            embed = discord.Embed(
                title="📁 Управление категориями и пресетами",
                description="Удаление отменено",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)


class BackToCategoriesButton(discord.ui.Button):
    """Кнопка возврата к управлению категориями"""

    def __init__(self, parent_view):
        super().__init__(
            label="Назад",
            style=discord.ButtonStyle.gray,
            row=2
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        await self.parent_view.refresh_categories()
        embed = discord.Embed(
            title="📁 Управление категориями и пресетами",
            description="Категории позволяют группировать пресеты.\n"
                        "Структура: **Категория → Подкатегория → Пресет**\n\n"
                        "Выберите категорию для просмотра пресетов или создайте новую",
            color=discord.Color.blue()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)


# ============== УПРАВЛЕНИЕ ПРЕСЕТАМИ ==============

class PresetManagementView(discord.ui.View):
    """View для управления пресетами"""

    def __init__(self, bot, guild, embed, user, original_message, original_view, settings_menu_view=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.embed = embed
        self.user = user
        self.original_message = original_message
        self.original_view = original_view
        self.settings_menu_view = settings_menu_view
        self.presets = []

    async def refresh_presets(self):
        """Загрузка пресетов из БД"""
        async with self.bot.db_pool.acquire() as conn:
            self.presets = await conn.fetch(
                """
                SELECT p.preset_id, p.name, p.role_ids, p.description, p.emoji, p.category_id,
                       c.name as category_name
                FROM role_presets p
                LEFT JOIN preset_categories c ON p.category_id = c.category_id
                ORDER BY p.sort_order NULLS LAST, p.name
                """
            )

        # Очищаем и добавляем компоненты
        self.clear_items()

        # Select для управления пресетами (включая опцию создания)
        self.add_item(PresetManagementSelect(self.presets, self.bot, self.guild, self))

        # Кнопка возврата в главное меню (row=2)
        self.add_item(BackToSettingsMenuButton(self.settings_menu_view))


class BackToSettingsMenuButton(discord.ui.Button):
    """Кнопка возврата в главное меню настроек"""

    def __init__(self, settings_menu_view):
        super().__init__(
            label="Назад",
            style=discord.ButtonStyle.gray,
            row=2
        )
        self.settings_menu_view = settings_menu_view

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⚙ Настройки",
            description="Выберите раздел настроек",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=self.settings_menu_view)


# ============== УПРАВЛЕНИЕ ПРИЧИНАМИ ОТКАЗА ==============

class RejectReasonsManagementView(discord.ui.View):
    """View для управления причинами отказа"""

    def __init__(self, bot, settings_menu_view):
        super().__init__(timeout=300)
        self.bot = bot
        self.settings_menu_view = settings_menu_view
        self.reasons = []

    async def refresh_reasons(self):
        """Загрузка причин из БД"""
        async with self.bot.db_pool.acquire() as conn:
            self.reasons = await conn.fetch(
                "SELECT reason_id, reason_text, dm_template FROM reject_reasons ORDER BY reason_id"
            )

        self.clear_items()
        self.add_item(RejectReasonsManagementSelect(self.reasons, self.bot, self))

        # Кнопка возврата (row=2)
        self.add_item(BackToSettingsMenuButton(self.settings_menu_view))


class RejectReasonsManagementSelect(discord.ui.Select):
    """Select для управления причинами отказа"""

    def __init__(self, reasons: list, bot, parent_view):
        self.reasons_data = {str(r['reason_id']): r for r in reasons}
        self.bot = bot
        self.parent_view = parent_view

        options = [
            discord.SelectOption(
                label="Добавить причину",
                value="create_reason",
                emoji="➕",
                description="Создать новую причину отказа"
            )
        ]

        for reason in reasons[:24]:
            text = reason['reason_text']
            if len(text) > 100:
                text = text[:97] + "..."

            # Показываем есть ли кастомный текст ЛС
            has_dm = "✉ Свой текст ЛС" if reason.get('dm_template') else "Стандартный текст ЛС"

            options.append(discord.SelectOption(
                label=text,
                value=str(reason['reason_id']),
                description=has_dm,
                emoji="📋"
            ))

        super().__init__(
            placeholder="Выберите действие или причину...",
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]

        if selected_value == "create_reason":
            modal = RejectReasonCreateModal(self.bot, self.parent_view)
            await interaction.response.send_modal(modal)
            return

        reason = self.reasons_data.get(selected_value)
        if not reason:
            await interaction.response.send_message("Причина не найдена.", ephemeral=True)
            return

        # Показываем меню редактирования причины
        view = RejectReasonEditView(reason, self.bot, self.parent_view)

        dm_info = f"**Текст ЛС:** {reason['dm_template']}" if reason.get('dm_template') else "**Текст ЛС:** _Стандартный_"

        embed = discord.Embed(
            title=f"📋 Редактирование причины",
            description=f"**Причина:** {reason['reason_text']}\n\n{dm_info}",
            color=discord.Color.blue()
        )

        await interaction.response.edit_message(embed=embed, view=view)


class RejectReasonEditView(discord.ui.View):
    """View для редактирования причины отказа"""

    def __init__(self, reason: dict, bot, parent_view):
        super().__init__(timeout=300)
        self.reason = reason
        self.bot = bot
        self.parent_view = parent_view

    @discord.ui.button(label="Изменить текст", style=discord.ButtonStyle.primary, emoji="✏", row=0)
    async def edit_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RejectReasonEditModal(self.reason, self.bot, self.parent_view)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.danger, emoji="🗑", row=0)
    async def delete_reason(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ConfirmDeleteReasonView(self.reason, self.bot, self.parent_view)
        await interaction.response.edit_message(
            content=f"**Удалить причину «{self.reason['reason_text']}»?**\n\nЭто действие необратимо!",
            embed=None,
            view=view
        )

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.gray, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Используем defer для предотвращения таймаута
        await interaction.response.defer()

        # Загружаем данные
        await self.parent_view.refresh_reasons()

        # Обновляем сообщение
        embed = discord.Embed(
            title="📋 Настройка причин отказа",
            description="Выберите причину для редактирования или создайте новую\n\n"
                       "💡 **Подсказка:** Вы можете настроить текст сообщения, которое будет отправлено пользователю в ЛС",
            color=discord.Color.blue()
        )
        await interaction.edit_original_response(embed=embed, view=self.parent_view)


class RejectReasonCreateModal(discord.ui.Modal, title="Добавить причину отказа"):
    """Модальное окно для создания новой причины отказа"""

    reason_text = discord.ui.TextInput(
        label="Текст причины (для выбора)",
        placeholder="Например: Никнейм не по формату",
        required=True,
        max_length=200
    )

    dm_template = discord.ui.TextInput(
        label="Текст сообщения в ЛС (опционально)",
        placeholder="Оставьте пустым для стандартного текста",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )

    def __init__(self, bot, parent_view):
        super().__init__()
        self.bot = bot
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            dm_value = self.dm_template.value.strip() if self.dm_template.value else None

            async with self.bot.db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO reject_reasons (reason_text, dm_template, created_by, created_at) VALUES ($1, $2, $3, NOW())",
                    self.reason_text.value,
                    dm_value,
                    interaction.user.id
                )

            logger.info(f"Причина отказа '{self.reason_text.value}' создана пользователем {interaction.user.display_name}")

            await self.parent_view.refresh_reasons()
            embed = discord.Embed(
                title="📋 Настройка причин отказа",
                description=f"Причина **«{self.reason_text.value}»** добавлена!",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(content=None, embed=embed, view=self.parent_view)

        except Exception as e:
            logger.error(f"Ошибка при создании причины отказа: {e}", exc_info=True)
            await interaction.response.send_message(
                f"Ошибка при создании причины: {e}",
                ephemeral=True
            )


class RejectReasonEditModal(discord.ui.Modal, title="Редактировать причину"):
    """Модальное окно для редактирования причины отказа"""

    def __init__(self, reason: dict, bot, parent_view):
        super().__init__()
        self.reason = reason
        self.bot = bot
        self.parent_view = parent_view

        self.reason_text = discord.ui.TextInput(
            label="Текст причины (для выбора)",
            default=reason['reason_text'],
            required=True,
            max_length=200
        )

        self.dm_template = discord.ui.TextInput(
            label="Текст сообщения в ЛС (опционально)",
            placeholder="Оставьте пустым для стандартного текста",
            default=reason.get('dm_template') or "",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500
        )

        self.add_item(self.reason_text)
        self.add_item(self.dm_template)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            dm_value = self.dm_template.value.strip() if self.dm_template.value else None

            async with self.bot.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE reject_reasons SET reason_text = $1, dm_template = $2 WHERE reason_id = $3",
                    self.reason_text.value,
                    dm_value,
                    self.reason['reason_id']
                )

            logger.info(f"Причина отказа '{self.reason_text.value}' обновлена пользователем {interaction.user.display_name}")

            await self.parent_view.refresh_reasons()
            embed = discord.Embed(
                title="📋 Настройка причин отказа",
                description=f"Причина **«{self.reason_text.value}»** обновлена!",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(content=None, embed=embed, view=self.parent_view)

        except Exception as e:
            logger.error(f"Ошибка при обновлении причины отказа: {e}", exc_info=True)
            await interaction.response.send_message(
                f"Ошибка при обновлении причины: {e}",
                ephemeral=True
            )


class ConfirmDeleteReasonView(discord.ui.View):
    """View для подтверждения удаления причины отказа"""

    def __init__(self, reason: dict, bot, parent_view):
        super().__init__(timeout=60)
        self.reason = reason
        self.bot = bot
        self.parent_view = parent_view

    @discord.ui.button(label="Да, удалить", style=discord.ButtonStyle.danger, emoji="🗑")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM reject_reasons WHERE reason_id = $1",
                self.reason['reason_id']
            )

        logger.info(f"Причина отказа '{self.reason['reason_text']}' удалена пользователем {interaction.user.display_name}")

        await self.parent_view.refresh_reasons()
        embed = discord.Embed(
            title="📋 Настройка причин отказа",
            description=f"Причина **«{self.reason['reason_text']}»** удалена!",
            color=discord.Color.red()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.gray, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        await self.parent_view.refresh_reasons()
        embed = discord.Embed(
            title="📋 Настройка причин отказа",
            description="Удаление отменено",
            color=discord.Color.blue()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)


# ============== ВЫБОР КАТЕГОРИИ ПРИ СОЗДАНИИ ПРЕСЕТА ==============

class SelectPresetCategoryForCreateView(discord.ui.View):
    """View для выбора категории при создании пресета"""

    def __init__(self, bot, guild, parent_view):
        super().__init__(timeout=120)
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view

    async def load_categories(self):
        """Загрузка категорий из БД"""
        async with self.bot.db_pool.acquire() as conn:
            categories = await conn.fetch(
                """
                SELECT c.category_id, c.name, c.parent_id, c.emoji, p.name as parent_name
                FROM preset_categories c
                LEFT JOIN preset_categories p ON c.parent_id = p.category_id
                ORDER BY p.name NULLS FIRST, c.name
                """
            )

        self.add_item(PresetCategoryForCreateSelect(categories, self.bot, self.guild, self.parent_view))

        # Кнопка "Без категории"
        self.add_item(NoCategoryButton(self.bot, self.guild, self.parent_view))

        # Кнопка "Назад"
        self.add_item(BackToPresetsButton(self.parent_view))


class PresetCategoryForCreateSelect(discord.ui.Select):
    """Select для выбора категории при создании пресета"""

    def __init__(self, categories: list, bot, guild, parent_view):
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view
        self.categories_data = {str(c['category_id']): c for c in categories}

        options = []

        if not categories:
            options.append(discord.SelectOption(
                label="Нет категорий",
                value="none",
                description="Создайте категорию в разделе 'Управление категориями'"
            ))
        else:
            for cat in categories[:25]:
                # Формируем название с учётом родителя
                if cat['parent_name']:
                    label = f"{cat['parent_name']} → {cat['name']}"
                    default_emoji = "📂"
                else:
                    label = cat['name']
                    default_emoji = "📁"

                # Используем пользовательский эмодзи, если указан
                emoji_str = cat.get('emoji')
                emoji = default_emoji
                if emoji_str:
                    parsed_emoji = parse_emoji(emoji_str, self.guild)
                    if parsed_emoji:
                        emoji = parsed_emoji

                if len(label) > 100:
                    label = label[:97] + "..."

                options.append(discord.SelectOption(
                    label=label,
                    value=str(cat['category_id']),
                    emoji=emoji
                ))

        super().__init__(
            placeholder="Выберите категорию...",
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]

        if selected_value == "none":
            await interaction.response.send_message(
                "Категории не созданы. Создайте их в разделе 'Управление категориями'.",
                ephemeral=True
            )
            return

        category = self.categories_data.get(selected_value)
        if not category:
            await interaction.response.send_message("Категория не найдена.", ephemeral=True)
            return

        # Открываем модальное окно с выбранной категорией
        modal = PresetCreateModal(self.bot, self.guild, self.parent_view, category_id=int(selected_value))
        await interaction.response.send_modal(modal)


class NoCategoryButton(discord.ui.Button):
    """Кнопка 'Без категории' при создании пресета"""

    def __init__(self, bot, guild, parent_view):
        super().__init__(
            label="Без категории",
            style=discord.ButtonStyle.secondary,
            emoji="📄",
            row=1
        )
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        modal = PresetCreateModal(self.bot, self.guild, self.parent_view, category_id=None)
        await interaction.response.send_modal(modal)


class BackToPresetsButton(discord.ui.Button):
    """Кнопка возврата к управлению пресетами"""

    def __init__(self, parent_view):
        super().__init__(
            label="Назад",
            style=discord.ButtonStyle.gray,
            row=1
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        await self.parent_view.refresh_presets()
        embed = discord.Embed(
            title="🎭 Управление пресетами",
            description="Выберите действие или пресет для редактирования",
            color=discord.Color.blue()
        )
        await interaction.edit_original_response(embed=embed, view=self.parent_view)


class PresetManagementSelect(discord.ui.Select):
    """Select для управления пресетами (создание/редактирование)"""

    def __init__(self, presets: list, bot, guild, parent_view):
        self.presets_data = {str(p['preset_id']): p for p in presets}
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view

        # Первая опция - создать пресет
        options = [
            discord.SelectOption(
                label="Создать пресет",
                value="create_preset",
                emoji="➕",
                description="Создать новый пресет ролей"
            )
        ]

        # Остальные пресеты для редактирования
        for preset in presets[:24]:
            emoji_str = preset.get('emoji')
            emoji = None
            if emoji_str:
                parsed_emoji = parse_emoji(emoji_str, guild)
                if parsed_emoji:
                    emoji = parsed_emoji
            description = preset.get('description') or f"Ролей: {len(preset['role_ids'])}"
            if len(description) > 100:
                description = description[:97] + "..."

            options.append(discord.SelectOption(
                label=preset['name'][:100],
                value=str(preset['preset_id']),
                description=description,
                emoji=emoji
            ))

        super().__init__(
            placeholder="Выберите действие или пресет...",
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]

        # Создание нового пресета - сначала выбор категории
        if selected_value == "create_preset":
            view = SelectPresetCategoryForCreateView(self.bot, self.guild, self.parent_view)
            await view.load_categories()

            embed = discord.Embed(
                title="📁 Выберите категорию для пресета",
                description="Выберите категорию, в которую будет добавлен пресет, или создайте пресет без категории.",
                color=discord.Color.blue()
            )
            await interaction.response.edit_message(embed=embed, view=view)
            return

        preset = self.presets_data.get(selected_value)

        if not preset:
            await interaction.response.send_message("Пресет не найден.", ephemeral=True)
            return

        # Показываем информацию о пресете и кнопки редактирования/удаления
        view = PresetEditView(preset, self.bot, self.guild, self.parent_view)

        # Получаем названия ролей
        role_names = []
        for role_id in preset['role_ids']:
            role = self.guild.get_role(role_id)
            if role:
                role_names.append(role.name)
            else:
                role_names.append(f"ID {role_id} (удалена)")

        emoji_str = f"{preset.get('emoji')} " if preset.get('emoji') else ""
        embed = discord.Embed(
            title=f"{emoji_str}{preset['name']}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Роли", value="\n".join(role_names) if role_names else "Нет ролей", inline=False)
        if preset.get('description'):
            embed.add_field(name="Описание", value=preset['description'], inline=False)
        embed.add_field(name="Категория", value=preset.get('category_name') or "Без категории", inline=True)
        embed.add_field(name="ID", value=str(preset['preset_id']), inline=True)

        await interaction.response.edit_message(embed=embed, view=view)


# ============== РЕДАКТИРОВАНИЕ ПРЕСЕТА ==============

class PresetEditView(discord.ui.View):
    """View для редактирования/удаления пресета"""

    def __init__(self, preset: dict, bot, guild, parent_view):
        super().__init__(timeout=300)
        self.preset = preset
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view

    @discord.ui.button(label="Изменить название/описание/эмодзи", style=discord.ButtonStyle.primary, emoji="✏", row=0)
    async def edit_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PresetEditInfoModal(self.preset, self.bot, self.parent_view)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Изменить роли", style=discord.ButtonStyle.primary, emoji="🎭", row=0)
    async def edit_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = RoleSelectView(self.preset, self.bot, self.guild, self.parent_view)
        await interaction.response.edit_message(
            content=f"**Редактирование ролей пресета «{self.preset['name']}»**\n\nВыберите роли из списка ниже. Можно выбрать несколько.",
            embed=None,
            view=view
        )

    @discord.ui.button(label="Изменить категорию", style=discord.ButtonStyle.primary, emoji="📁", row=1)
    async def change_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ChangePresetCategoryView(self.preset, self.bot, self.guild, self.parent_view)
        await view.load_categories()

        current_cat = self.preset.get('category_name') or "Без категории"
        embed = discord.Embed(
            title=f"📁 Изменить категорию пресета «{self.preset['name']}»",
            description=f"**Текущая категория:** {current_cat}\n\nВыберите новую категорию или уберите из категории.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Удалить пресет", style=discord.ButtonStyle.danger, emoji="🗑", row=2)
    async def delete_preset(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ConfirmDeleteView(self.preset, self.bot, self.parent_view)
        await interaction.response.edit_message(
            content=f"**Вы уверены, что хотите удалить пресет «{self.preset['name']}»?**\n\nЭто действие необратимо!",
            embed=None,
            view=view
        )

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.gray, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Проверяем, что за parent_view - CategoryContentView или PresetsManagementView
        if hasattr(self.parent_view, 'load_content'):
            # Это CategoryContentView (просмотр содержимого подкатегории)
            await self.parent_view.load_content()

            # Формируем заголовок с путем к категории
            category = self.parent_view.category
            emoji = category.get('emoji')
            emoji_str = '📁'
            if emoji:
                parsed_emoji = parse_emoji(emoji, interaction.guild)
                if parsed_emoji:
                    emoji_str = str(parsed_emoji)

            if category.get('parent_name'):
                title = f"{emoji_str} {category['parent_name']} → {category['name']}"
            else:
                title = f"{emoji_str} {category['name']}"

            # Подсчитываем актуальное количество
            subcategory_count = len(self.parent_view.subcategories)
            preset_count = len(self.parent_view.presets)

            embed = discord.Embed(
                title=title,
                description=f"Подкатегорий: {subcategory_count} | Пресетов: {preset_count}\n\n"
                            f"Выберите подкатегорию, пресет или действие",
                color=discord.Color.blue()
            )
        else:
            # Это PresetsManagementView (управление пресетами)
            await self.parent_view.refresh_presets()
            embed = discord.Embed(
                title="⚙ Управление пресетами",
                description="Выберите действие или пресет для редактирования",
                color=discord.Color.blue()
            )

        await interaction.response.edit_message(embed=embed, view=self.parent_view)


# ============== ИЗМЕНЕНИЕ КАТЕГОРИИ ПРЕСЕТА ==============

class ChangePresetCategoryView(discord.ui.View):
    """View для изменения категории пресета"""

    def __init__(self, preset: dict, bot, guild, parent_view):
        super().__init__(timeout=120)
        self.preset = preset
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view

    async def load_categories(self):
        """Загрузка категорий из БД"""
        async with self.bot.db_pool.acquire() as conn:
            categories = await conn.fetch(
                """
                SELECT c.category_id, c.name, c.parent_id, c.emoji, p.name as parent_name
                FROM preset_categories c
                LEFT JOIN preset_categories p ON c.parent_id = p.category_id
                ORDER BY p.name NULLS FIRST, c.name
                """
            )

        self.add_item(ChangePresetCategorySelect(categories, self.preset, self.bot, self.parent_view, self.guild))

        # Кнопка "Убрать из категории"
        self.add_item(RemoveFromCategoryButton(self.preset, self.bot, self.parent_view))

        # Кнопка "Назад"
        self.add_item(BackToPresetEditButton(self.preset, self.bot, self.guild, self.parent_view))


class ChangePresetCategorySelect(discord.ui.Select):
    """Select для изменения категории пресета"""

    def __init__(self, categories: list, preset: dict, bot, parent_view, guild):
        self.preset = preset
        self.bot = bot
        self.parent_view = parent_view
        self.guild = guild

        options = []

        if not categories:
            options.append(discord.SelectOption(
                label="Нет категорий",
                value="none",
                description="Создайте категорию в разделе 'Управление категориями'"
            ))
        else:
            for cat in categories[:25]:
                # Формируем название с учётом родителя
                if cat['parent_name']:
                    label = f"{cat['parent_name']} → {cat['name']}"
                    default_emoji = "📂"
                else:
                    label = cat['name']
                    default_emoji = "📁"

                # Используем пользовательский эмодзи, если указан
                emoji_str = cat.get('emoji')
                emoji = default_emoji
                if emoji_str:
                    parsed_emoji = parse_emoji(emoji_str, self.guild)
                    if parsed_emoji:
                        emoji = parsed_emoji

                if len(label) > 100:
                    label = label[:97] + "..."

                # Отмечаем текущую категорию
                is_current = preset.get('category_id') == cat['category_id']

                options.append(discord.SelectOption(
                    label=label,
                    value=str(cat['category_id']),
                    emoji=emoji,
                    default=is_current
                ))

        super().__init__(
            placeholder="Выберите новую категорию...",
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]

        if selected_value == "none":
            await interaction.response.send_message(
                "Категории не созданы. Создайте их в разделе 'Управление категориями'.",
                ephemeral=True
            )
            return

        category_id = int(selected_value)

        # Обновляем категорию пресета в БД
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE role_presets SET category_id = $1 WHERE preset_id = $2",
                category_id,
                self.preset['preset_id']
            )

            # Получаем название категории
            cat_name = await conn.fetchval(
                "SELECT name FROM preset_categories WHERE category_id = $1",
                category_id
            )

        logger.info(f"Категория пресета '{self.preset['name']}' изменена на '{cat_name}' пользователем {interaction.user.display_name}")

        await interaction.response.defer()

        await self.parent_view.refresh_presets()
        embed = discord.Embed(
            title="🎭 Управление пресетами",
            description=f"Категория пресета **{self.preset['name']}** изменена на **{cat_name}**!",
            color=discord.Color.green()
        )
        await interaction.edit_original_response(embed=embed, view=self.parent_view)


class RemoveFromCategoryButton(discord.ui.Button):
    """Кнопка для удаления пресета из категории"""

    def __init__(self, preset: dict, bot, parent_view):
        super().__init__(
            label="Убрать из категории",
            style=discord.ButtonStyle.secondary,
            emoji="📄",
            row=1
        )
        self.preset = preset
        self.bot = bot
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        # Убираем категорию
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE role_presets SET category_id = NULL WHERE preset_id = $1",
                self.preset['preset_id']
            )

        logger.info(f"Пресет '{self.preset['name']}' убран из категории пользователем {interaction.user.display_name}")

        await interaction.response.defer()

        await self.parent_view.refresh_presets()
        embed = discord.Embed(
            title="🎭 Управление пресетами",
            description=f"Пресет **{self.preset['name']}** убран из категории!",
            color=discord.Color.green()
        )
        await interaction.edit_original_response(embed=embed, view=self.parent_view)


class BackToPresetEditButton(discord.ui.Button):
    """Кнопка возврата к редактированию пресета"""

    def __init__(self, preset: dict, bot, guild, parent_view):
        super().__init__(
            label="Назад",
            style=discord.ButtonStyle.gray,
            row=1
        )
        self.preset = preset
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        # Сначала быстро отвечаем
        await interaction.response.defer()

        # Перезагружаем данные пресета для актуальности
        async with self.bot.db_pool.acquire() as conn:
            updated_preset = await conn.fetchrow(
                """
                SELECT p.preset_id, p.name, p.role_ids, p.description, p.emoji, p.category_id,
                       c.name as category_name
                FROM role_presets p
                LEFT JOIN preset_categories c ON p.category_id = c.category_id
                WHERE p.preset_id = $1
                """,
                self.preset['preset_id']
            )

        if not updated_preset:
            await interaction.edit_original_response(content="Пресет не найден.")
            return

        preset_dict = dict(updated_preset)
        view = PresetEditView(preset_dict, self.bot, self.guild, self.parent_view)

        # Получаем названия ролей
        role_names = []
        for role_id in preset_dict['role_ids']:
            role = self.guild.get_role(role_id)
            if role:
                role_names.append(role.name)
            else:
                role_names.append(f"ID {role_id} (удалена)")

        emoji_str = f"{preset_dict.get('emoji')} " if preset_dict.get('emoji') else ""
        embed = discord.Embed(
            title=f"{emoji_str}{preset_dict['name']}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Роли", value="\n".join(role_names) if role_names else "Нет ролей", inline=False)
        if preset_dict.get('description'):
            embed.add_field(name="Описание", value=preset_dict['description'], inline=False)
        embed.add_field(name="Категория", value=preset_dict.get('category_name') or "Без категории", inline=True)
        embed.add_field(name="ID", value=str(preset_dict['preset_id']), inline=True)

        await interaction.edit_original_response(embed=embed, view=view)


# ============== ВЫБОР РОЛЕЙ ==============

class RoleSelectView(discord.ui.View):
    """View для выбора ролей при редактировании пресета"""

    def __init__(self, preset: dict, bot, guild, parent_view):
        super().__init__(timeout=300)
        self.preset = preset
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view
        self.selected_roles = list(preset['role_ids'])

        # Добавляем Select с ролями
        self._add_role_selects()

    def _add_role_selects(self):
        # Получаем роли сервера (исключаем @everyone и роли выше бота)
        bot_top_role = self.guild.me.top_role
        available_roles = [
            role for role in self.guild.roles
            if role.name != "@everyone" and role < bot_top_role and not role.managed
        ]

        # Discord ограничивает до 25 опций в Select, разбиваем на части
        for i, chunk in enumerate(self._chunk_list(available_roles[:75], 25)):
            options = []
            for role in chunk:
                is_selected = role.id in self.selected_roles
                options.append(discord.SelectOption(
                    label=role.name[:100],
                    value=str(role.id),
                    default=is_selected
                ))

            select = RoleMultiSelect(
                options=options,
                placeholder=f"Выберите роли (часть {i+1})...",
                row=i,
                parent_view=self
            )
            self.add_item(select)

        # Кнопки сохранения и отмены
        self.add_item(SaveRolesButton(self))
        self.add_item(CancelRolesButton(self))

    @staticmethod
    def _chunk_list(lst, n):
        """Разбивает список на части по n элементов"""
        for i in range(0, len(lst), n):
            yield lst[i:i + n]


class RoleMultiSelect(discord.ui.Select):
    """Мультиселект для выбора ролей"""

    def __init__(self, options, placeholder, row, parent_view):
        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=0,
            max_values=len(options),
            row=row
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        # Обновляем выбранные роли
        # Убираем роли из этого селекта из общего списка
        for option in self.options:
            role_id = int(option.value)
            if role_id in self.parent_view.selected_roles:
                self.parent_view.selected_roles.remove(role_id)

        # Добавляем выбранные
        for value in self.values:
            role_id = int(value)
            if role_id not in self.parent_view.selected_roles:
                self.parent_view.selected_roles.append(role_id)

        await interaction.response.defer()


class SaveRolesButton(discord.ui.Button):
    """Кнопка сохранения ролей"""

    def __init__(self, parent_view):
        super().__init__(
            label="Сохранить",
            style=discord.ButtonStyle.green,
            emoji="✅",
            row=4
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.selected_roles:
            await interaction.response.send_message(
                "Выберите хотя бы одну роль!",
                ephemeral=True
            )
            return

        # Сохраняем в БД
        async with self.parent_view.bot.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE role_presets SET role_ids = $1 WHERE preset_id = $2",
                self.parent_view.selected_roles,
                self.parent_view.preset['preset_id']
            )

        logger.info(f"Роли пресета '{self.parent_view.preset['name']}' обновлены пользователем {interaction.user.display_name}")

        await interaction.response.defer()

        # Возвращаемся к списку пресетов
        await self.parent_view.parent_view.refresh_presets()
        embed = discord.Embed(
            title="⚙ Управление пресетами",
            description=f"Роли пресета **{self.parent_view.preset['name']}** успешно обновлены!",
            color=discord.Color.green()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view.parent_view)


class CancelRolesButton(discord.ui.Button):
    """Кнопка отмены редактирования ролей"""

    def __init__(self, parent_view):
        super().__init__(
            label="Отмена",
            style=discord.ButtonStyle.gray,
            emoji="✖",
            row=4
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        await self.parent_view.parent_view.refresh_presets()
        embed = discord.Embed(
            title="⚙ Управление пресетами",
            description="Редактирование отменено",
            color=discord.Color.blue()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view.parent_view)


# ============== ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ ==============

class ConfirmDeleteView(discord.ui.View):
    """View для подтверждения удаления пресета"""

    def __init__(self, preset: dict, bot, parent_view):
        super().__init__(timeout=60)
        self.preset = preset
        self.bot = bot
        self.parent_view = parent_view

    @discord.ui.button(label="Да, удалить", style=discord.ButtonStyle.danger, emoji="🗑")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM role_presets WHERE preset_id = $1",
                self.preset['preset_id']
            )

        logger.info(f"Пресет '{self.preset['name']}' удален пользователем {interaction.user.display_name}")

        await self.parent_view.refresh_presets()
        embed = discord.Embed(
            title="⚙ Управление пресетами",
            description=f"Пресет **{self.preset['name']}** удален!",
            color=discord.Color.red()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.gray, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        await self.parent_view.refresh_presets()
        embed = discord.Embed(
            title="⚙ Управление пресетами",
            description="Удаление отменено",
            color=discord.Color.blue()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=self.parent_view)


# ============== МОДАЛЬНЫЕ ОКНА ==============

class PresetCreateModal(discord.ui.Modal, title="Создать пресет"):
    """Модальное окно для создания пресета"""

    preset_name = discord.ui.TextInput(
        label="Название пресета",
        placeholder="Например: Офицер патруля",
        required=True,
        max_length=50
    )

    description = discord.ui.TextInput(
        label="Описание",
        placeholder="Краткое описание пресета",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=100
    )

    emoji = discord.ui.TextInput(
        label="Эмодзи",
        placeholder="🚔 или ID кастомного: 1234567890",
        required=False,
        max_length=50
    )

    role_ids_input = discord.ui.TextInput(
        label="ID ролей через запятую",
        placeholder="123456789, 987654321, 111222333",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    rank_group_role_id_input = discord.ui.TextInput(
        label="ID групповой роли (Officers/Detectives)",
        placeholder="Опционально: ID роли для группы рангов",
        required=False,
        max_length=30
    )

    def __init__(self, bot, guild, parent_view=None, category_id=None):
        super().__init__()
        self.bot = bot
        self.guild = guild
        self.parent_view = parent_view
        self.category_id = category_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Парсинг ID ролей
            role_ids_str = self.role_ids_input.value.replace(" ", "")
            role_ids = [int(rid.strip()) for rid in role_ids_str.split(",") if rid.strip()]

            if not role_ids:
                await interaction.response.send_message(
                    "Не указаны ID ролей!",
                    ephemeral=True
                )
                return

            # Валидация ролей
            invalid_roles = []
            valid_roles = []
            bot_top_role = self.guild.me.top_role

            for role_id in role_ids:
                role = self.guild.get_role(role_id)
                if not role:
                    invalid_roles.append(f"ID {role_id} (не найдена)")
                elif role >= bot_top_role:
                    invalid_roles.append(f"{role.name} (выше роли бота)")
                else:
                    valid_roles.append(role)

            if invalid_roles:
                await interaction.response.send_message(
                    f"Проблемы с ролями:\n" + "\n".join(f"• {r}" for r in invalid_roles),
                    ephemeral=True
                )
                return

            # Валидация и нормализация эмодзи
            emoji_input = self.emoji.value.strip() if self.emoji.value else None
            emoji_value = normalize_emoji_for_storage(emoji_input, self.guild) if emoji_input else None

            # Парсинг ID групповой роли
            rank_group_role_id = None
            if self.rank_group_role_id_input.value and self.rank_group_role_id_input.value.strip():
                try:
                    rank_group_role_id = int(self.rank_group_role_id_input.value.strip())
                    # Проверяем существование роли
                    role = self.guild.get_role(rank_group_role_id)
                    if not role:
                        await interaction.response.send_message(
                            f"⚠️ Групповая роль с ID {rank_group_role_id} не найдена на сервере!",
                            ephemeral=True
                        )
                        return
                except ValueError:
                    await interaction.response.send_message(
                        "❌ Неверный формат ID групповой роли! Используйте только числа.",
                        ephemeral=True
                    )
                    return

            # Все валидации прошли, используем defer для продления времени ответа
            await interaction.response.defer()

            # Сохранение в БД с категорией
            async with self.bot.db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO role_presets (name, role_ids, created_by, created_at, description, emoji, category_id, rank_group_role_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    self.preset_name.value,
                    role_ids,
                    interaction.user.id,
                    datetime.now(),
                    self.description.value if self.description.value else None,
                    emoji_value,
                    self.category_id,
                    rank_group_role_id
                )

            logger.info(
                f"Пресет '{self.preset_name.value}' создан пользователем {interaction.user.display_name} "
                f"с {len(valid_roles)} ролями, категория: {self.category_id}"
            )

            role_list = ", ".join([r.name for r in valid_roles])

            if self.parent_view:
                # Проверяем, является ли parent_view CategoryContentView
                if hasattr(self.parent_view, 'load_content'):
                    # Это CategoryContentView
                    await self.parent_view.load_content()
                    cat_emoji = self.parent_view.category.get('emoji')
                    emoji_str = '📁'
                    if cat_emoji:
                        parsed_cat_emoji = parse_emoji(cat_emoji, interaction.guild)
                        if parsed_cat_emoji:
                            emoji_str = str(parsed_cat_emoji)
                    embed = discord.Embed(
                        title=f"{emoji_str} {self.parent_view.category['name']}",
                        description=f"Пресет **{self.preset_name.value}** создан!\nРоли: {role_list}",
                        color=discord.Color.green()
                    )
                    await interaction.edit_original_response(embed=embed, view=self.parent_view)
                else:
                    # Это PresetManagementView
                    await self.parent_view.refresh_presets()
                    embed = discord.Embed(
                        title="⚙ Управление пресетами",
                        description=f"Пресет **{self.preset_name.value}** создан!\nРоли: {role_list}",
                        color=discord.Color.green()
                    )
                    await interaction.edit_original_response(embed=embed, view=self.parent_view)
            else:
                await interaction.followup.send(
                    f"Пресет **'{self.preset_name.value}'** создан!\nРоли: {role_list}",
                    ephemeral=True
                )

        except ValueError:
            await interaction.response.send_message(
                "Неверный формат ID ролей! Используйте только числа через запятую.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Ошибка при создании пресета: {e}", exc_info=True)
            await interaction.response.send_message(
                f"Ошибка при создании пресета: {e}",
                ephemeral=True
            )


class PresetEditInfoModal(discord.ui.Modal, title="Редактировать пресет"):
    """Модальное окно для редактирования информации о пресете"""

    def __init__(self, preset: dict, bot, parent_view):
        super().__init__()
        self.preset = preset
        self.bot = bot
        self.parent_view = parent_view

        # Заполняем поля текущими значениями
        self.preset_name = discord.ui.TextInput(
            label="Название пресета",
            default=preset['name'],
            required=True,
            max_length=50
        )

        self.description = discord.ui.TextInput(
            label="Описание",
            default=preset.get('description') or "",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=100
        )

        self.emoji = discord.ui.TextInput(
            label="Эмодзи",
            placeholder="🚔 или ID кастомного: 1234567890",
            default=preset.get('emoji') or "",
            required=False,
            max_length=50
        )

        rank_group_default = str(preset.get('rank_group_role_id', '')) if preset.get('rank_group_role_id') else ''
        self.rank_group_role_id_input = discord.ui.TextInput(
            label="ID групповой роли (Officers/Detectives)",
            placeholder="Оставьте пустым чтобы убрать",
            default=rank_group_default,
            required=False,
            max_length=30
        )

        self.add_item(self.preset_name)
        self.add_item(self.description)
        self.add_item(self.emoji)
        self.add_item(self.rank_group_role_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Нормализуем эмодзи перед сохранением
            emoji_input = self.emoji.value.strip() if self.emoji.value else None
            emoji_value = normalize_emoji_for_storage(emoji_input, interaction.guild) if emoji_input else None

            # Парсинг ID групповой роли
            rank_group_role_id = None
            if self.rank_group_role_id_input.value and self.rank_group_role_id_input.value.strip():
                try:
                    rank_group_role_id = int(self.rank_group_role_id_input.value.strip())
                    # Проверяем существование роли
                    role = interaction.guild.get_role(rank_group_role_id)
                    if not role:
                        await interaction.response.send_message(
                            f"⚠️ Групповая роль с ID {rank_group_role_id} не найдена на сервере!",
                            ephemeral=True
                        )
                        return
                except ValueError:
                    await interaction.response.send_message(
                        "❌ Неверный формат ID групповой роли! Используйте только числа.",
                        ephemeral=True
                    )
                    return

            # Все валидации прошли, используем defer для продления времени ответа
            await interaction.response.defer()

            async with self.bot.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE role_presets SET name = $1, description = $2, emoji = $3, rank_group_role_id = $4 WHERE preset_id = $5",
                    self.preset_name.value,
                    self.description.value if self.description.value else None,
                    emoji_value,
                    rank_group_role_id,
                    self.preset['preset_id']
                )

            logger.info(f"Пресет '{self.preset['name']}' обновлен пользователем {interaction.user.display_name}")

            await self.parent_view.refresh_presets()
            embed = discord.Embed(
                title="⚙ Управление пресетами",
                description=f"Пресет **{self.preset_name.value}** обновлен!",
                color=discord.Color.green()
            )
            await interaction.edit_original_response(embed=embed, view=self.parent_view)

        except Exception as e:
            logger.error(f"Ошибка при обновлении пресета: {e}", exc_info=True)
            await interaction.response.send_message(
                f"Ошибка при обновлении пресета: {e}",
                ephemeral=True
            )


# ============== ФОРМА ЗАПРОСА РОЛЕЙ ==============

class FeedbackModal(discord.ui.Modal, title="Новый запрос ролей"):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.bot = None

    ic_nickname = discord.ui.TextInput(
        label="Игровой никнейм персонажа",
        style=discord.TextStyle.short,
        placeholder="Например: John Doe",
        required=True,
        max_length=100,
    )

    ooc_nickname = discord.ui.TextInput(
        label="Ваш OOC никнейм",
        style=discord.TextStyle.short,
        placeholder="Например: JohnPlayer",
        required=True,
        max_length=100,
    )

    feedback = discord.ui.TextInput(
        label="Запрашиваемые роли",
        style=discord.TextStyle.long,
        placeholder="Например: Rampart Area, Detective I",
        required=True,
        max_length=300,
    )

    forum = discord.ui.TextInput(
        label="Форумный аккаунт (pd.ls-es.su)",
        style=discord.TextStyle.short,
        placeholder="Удостоверьтесь, что указали Discord в профиле",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        is_admin = interaction.user.guild_permissions.administrator
        has_preset_role = False

        if PRESET_ADMIN_ROLE_ID:
            try:
                preset_role = interaction.guild.get_role(int(PRESET_ADMIN_ROLE_ID))
                has_preset_role = preset_role and preset_role in interaction.user.roles
            except (ValueError, TypeError):
                pass

        if not is_admin and not has_preset_role:
            async with interaction.client.db_pool.acquire() as conn:
                last_request = await conn.fetchrow(
                    "SELECT created_at FROM requests WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1",
                    self.user.id
                )

            if last_request and last_request['created_at']:
                time_diff = datetime.now() - last_request['created_at']
                cooldown_minutes = 10
                if time_diff.total_seconds() < cooldown_minutes * 60:
                    remaining = cooldown_minutes - int(time_diff.total_seconds() / 60)
                    await interaction.response.send_message(
                        f"Подождите ещё {remaining} мин. перед созданием нового запроса.",
                        ephemeral=True
                    )
                    return

        channel = interaction.guild.get_channel(ADM_ROLES_CH)
        member = interaction.guild.get_member(self.user.id)

        # Получаем текущие роли пользователя (кроме @everyone)
        current_roles = [role.mention for role in member.roles if role.name != "@everyone"] if member else []
        roles_text = ", ".join(current_roles) if current_roles else "Нет ролей"

        # Дата захода на сервер
        joined_at = member.joined_at.strftime("%d.%m.%Y %H:%M") if member and member.joined_at else "Неизвестно"
        # Дата регистрации Discord
        created_at = self.user.created_at.strftime("%d.%m.%Y") if self.user.created_at else "Неизвестно"

        embed = discord.Embed(
            title="Новый запрос ролей",
            description=f"**От {self.user.mention} (ID: {self.user.id})**",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )

        embed.set_author(
            name=self.user.display_name,
            icon_url=self.user.display_avatar.url,
            url=f"https://discord.com/users/{self.user.id}",
        )

        embed.add_field(name=self.ic_nickname.label, value=self.ic_nickname.value, inline=True)
        embed.add_field(name=self.ooc_nickname.label, value=self.ooc_nickname.value, inline=True)
        embed.add_field(name=self.forum.label, value=self.forum.value, inline=False)
        embed.add_field(name=self.feedback.label, value=self.feedback.value, inline=False)
        embed.add_field(name="На сервере с", value=joined_at, inline=True)
        embed.add_field(name="Аккаунт создан", value=created_at, inline=True)
        embed.add_field(name="Текущие роли", value=roles_text[:1024], inline=False)

        view = PersistentView(embed, self.user, self.bot, interaction.guild)
        await view.load_presets()

        message = await channel.send(embed=embed, view=view)

        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO requests (message_id, user_id, embed, status, created_at) VALUES ($1, $2, $3, $4, $5)",
                message.id,
                self.user.id,
                json.dumps(embed.to_dict()),
                "pending",
                datetime.now(),
            )

        await interaction.response.send_message(
            f"Скоро вы получите свои роли, {self.user.mention}!", ephemeral=True
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await interaction.response.send_message(
            "Упс! Что-то пошло не так.", ephemeral=True
        )
        traceback.print_exception(type(error), error, error.__traceback__)


# ============== КНОПКИ ОДОБРЕНИЯ/ОТКЛОНЕНИЯ ==============

# ============== ВЫБОР ПРИЧИНЫ ОТКАЗА ==============

class RejectReasonView(discord.ui.View):
    """View для выбора причины отказа"""

    def __init__(self, embed: discord.Embed, user: discord.User, bot, original_message, original_view):
        super().__init__(timeout=120)
        self.embed = embed
        self.user = user
        self.bot = bot
        self.original_message = original_message
        self.original_view = original_view

    async def load_reasons(self):
        """Загрузка причин из БД и добавление Select"""
        try:
            async with self.bot.db_pool.acquire() as conn:
                reasons = await conn.fetch(
                    "SELECT reason_id, reason_text, dm_template FROM reject_reasons ORDER BY reason_id"
                )

            self.add_item(RejectReasonSelect(
                reasons=reasons,
                embed=self.embed,
                user=self.user,
                bot=self.bot,
                original_message=self.original_message,
                original_view=self.original_view
            ))
        except Exception as e:
            logger.error(f"Ошибка при загрузке причин отказа: {e}", exc_info=True)


class RejectReasonSelect(discord.ui.Select):
    """Выпадающий список для выбора причины отказа"""

    def __init__(self, reasons: list, embed: discord.Embed, user: discord.User, bot, original_message, original_view):
        self.embed = embed
        self.user = user
        self.bot = bot
        self.original_message = original_message
        self.original_view = original_view

        options = []

        # Добавляем стандартные причины
        for reason in reasons[:24]:
            options.append(discord.SelectOption(
                label=reason['reason_text'][:100],
                value=str(reason['reason_id']),
                emoji="📋"
            ))

        # Добавляем опцию "Свой текст"
        options.append(discord.SelectOption(
            label="Свой текст...",
            value="custom",
            emoji="✏",
            description="Написать свою причину отказа"
        ))

        super().__init__(
            placeholder="Выберите причину отказа...",
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]

        # Если выбран "Свой текст" - открываем модальное окно
        if selected_value == "custom":
            modal = DropModal(self.embed, self.user, self.original_view, self.original_message)
            await interaction.response.send_modal(modal)
            return

        # Получаем данные причины из БД
        async with self.bot.db_pool.acquire() as conn:
            reason_data = await conn.fetchrow(
                "SELECT reason_text, dm_template FROM reject_reasons WHERE reason_id = $1",
                int(selected_value)
            )

        if not reason_data:
            await interaction.response.send_message("Причина не найдена.", ephemeral=True)
            return

        # Применяем отказ
        await self._apply_rejection(interaction, reason_data['reason_text'], reason_data.get('dm_template'))

    async def _apply_rejection(self, interaction: discord.Interaction, reason: str, dm_template: str = None):
        """Применение отказа с указанной причиной"""
        self.embed.color = discord.Color.red()
        self.embed.set_footer(
            text=f"Отклонено пользователем {interaction.user.display_name}. Причина: {reason}"
        )

        self.original_view.clear_items()
        await self.original_message.edit(embed=self.embed, view=self.original_view)

        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = 'rejected', finished_by = $1, finished_at = $2, reject_reason = $3"
                " WHERE message_id = $4",
                interaction.user.id,
                datetime.now(),
                reason,
                self.original_message.id,
            )

        await interaction.response.edit_message(
            content=f"Запрос от {self.user.display_name} отклонён!\nПричина: {reason}",
            view=None
        )

        # Используем кастомный текст ЛС если задан, иначе стандартный
        dm_message = dm_template if dm_template else f"Ваш запрос на получение ролей был отклонён. Причина: {reason}"

        try:
            await self.user.send(dm_message)
        except discord.Forbidden:
            await interaction.followup.send(
                f"Не удалось отправить сообщение пользователю {self.user.display_name}. "
                f"Возможно, у него закрыты личные сообщения.",
                ephemeral=True,
            )


class DropModal(discord.ui.Modal, title="Причина отказа"):
    def __init__(self, embed: discord.Embed, user: discord.User, view: discord.ui.View, original_message=None):
        super().__init__()
        self.embed = embed
        self.user = user
        self.view = view
        self.original_message = original_message

    reason = discord.ui.TextInput(
        label="Укажите причину отказа",
        style=discord.TextStyle.long,
        placeholder="Например, недостаточно информации",
        required=True,
        max_length=300,
    )

    async def on_submit(self, interaction: discord.Interaction):
        self.embed.color = discord.Color.red()
        self.embed.set_footer(
            text=f"Отклонено пользователем {interaction.user.display_name}. Причина: {self.reason.value}"
        )

        self.view.clear_items()

        # Используем original_message если передан, иначе interaction.message
        message_to_edit = self.original_message or interaction.message
        await message_to_edit.edit(embed=self.embed, view=self.view)

        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = 'rejected', finished_by = $1, finished_at = $2, reject_reason = $3"
                " WHERE message_id = $4",
                interaction.user.id,
                datetime.now(),
                self.reason.value,
                message_to_edit.id,
            )

        await interaction.response.send_message(
            f"Запрос от {self.user.display_name} отклонён!\nПричина: {self.reason.value}",
            ephemeral=True
        )

        try:
            await self.user.send(
                f"Ваш запрос на получение ролей был отклонён. Причина: {self.reason.value}"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Не удалось отправить сообщение пользователю {self.user.display_name}. Возможно, у него закрыты "
                f"личные сообщения.",
                ephemeral=True,
            )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await interaction.response.send_message(
            "Упс! Что-то пошло не так.", ephemeral=True
        )
        traceback.print_exception(type(error), error, error.__traceback__)


class ButtonView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Получить роли",
        custom_id="register_button",
        style=discord.ButtonStyle.red,
    )
    async def registerbtn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        feedback_modal = FeedbackModal()
        feedback_modal.user = interaction.user
        feedback_modal.bot = self.bot
        await interaction.response.send_modal(feedback_modal)


class DropButton(discord.ui.Button):
    def __init__(self, embed: discord.Embed, user: discord.User, bot=None):
        super().__init__(
            label="Отклонить",
            style=discord.ButtonStyle.red,
            custom_id="drop_button",
            row=0
        )
        self.embed = embed
        self.user = user
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        # Показываем ephemeral сообщение с выбором причины
        bot = self.bot or interaction.client
        reject_view = RejectReasonView(
            embed=self.embed,
            user=self.user,
            bot=bot,
            original_message=interaction.message,
            original_view=self.view
        )
        await reject_view.load_reasons()

        await interaction.response.send_message(
            "**Выберите причину отказа:**",
            view=reject_view,
            ephemeral=True
        )


class DoneButton(discord.ui.Button):
    def __init__(self, embed: discord.Embed, user: discord.User):
        super().__init__(
            label="Выполнено",
            style=discord.ButtonStyle.green,
            custom_id="done_button",
            row=0
        )
        self.embed = embed
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        self.embed.color = discord.Color.green()
        self.embed.set_footer(
            text=f"Запрос выполнен пользователем {interaction.user.display_name}"
        )

        self.view.clear_items()
        await interaction.message.edit(embed=self.embed, view=self.view)

        async with interaction.client.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE requests SET status = 'approved', finished_by = $1, finished_at = $2 WHERE message_id = $3",
                interaction.user.id,
                datetime.now(),
                interaction.message.id,
            )

        await interaction.response.send_message(
            f"Запрос от {self.user.display_name} выполнен!", ephemeral=True
        )

        try:
            await self.user.send("Ваш запрос на получение ролей был одобрен.")
        except discord.Forbidden:
            await interaction.followup.send(
                f"Не удалось отправить сообщение пользователю {self.user.display_name}. Возможно, у него закрыты "
                f"личные сообщения.",
                ephemeral=True,
            )
