"""
Централизованная система логирования для бота
"""
import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(name='lspd_bot', log_file='logs/bot.log', level=logging.INFO):
    """
    Настройка логгера с ротацией файлов

    Args:
        name: Название логгера
        log_file: Путь к файлу логов
        level: Уровень логирования

    Returns:
        logging.Logger: Настроенный логгер
    """
    # Создаем директорию для логов если её нет
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Создаем логгер
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Если уже есть handlers, не добавляем новые (избегаем дублирования)
    if logger.handlers:
        return logger

    # Формат логов
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Handler для файла с ротацией (макс 10MB, 5 backup файлов)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Handler для консоли
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    # Добавляем handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# Создаем глобальный экземпляр логгера
logger = setup_logger()


def get_logger(module_name=None):
    """
    Получить логгер для конкретного модуля

    Args:
        module_name: Название модуля (опционально)

    Returns:
        logging.Logger: Логгер
    """
    if module_name:
        return logging.getLogger(f'lspd_bot.{module_name}')
    return logger
