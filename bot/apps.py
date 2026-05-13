"""
Конфигурация Django-приложения `bot`.

При старте проекта автоматически устанавливает Telegram webhook,
если задана переменная окружения RENDER_EXTERNAL_URL
или RENDER_EXTERNAL_HOSTNAME.
"""

import logging
import os
from typing import Optional
import requests
from django.apps import AppConfig

logger = logging.getLogger(__name__)

# Флаг, чтобы ready() не вызывал set_webhook дважды при автоперезагрузке
_webhook_set = False


def _get_webhook_url() -> Optional[str]:
    """Сформировать полный URL вебхука на основе переменных окружения Render."""
    render_url = os.environ.get('RENDER_EXTERNAL_URL')
    if render_url:
        return f"{render_url.rstrip('/')}/webhook/"

    hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    if hostname:
        return f"https://{hostname.rstrip('/')}/webhook/"

    return None


def set_webhook() -> None:
    """Установить вебхук в Telegram Bot API."""
    global _webhook_set
    if _webhook_set:
        return

    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.warning('TELEGRAM_TOKEN не задан — вебхук не установлен.')
        return

    webhook_url = _get_webhook_url()
    if not webhook_url:
        logger.info(
            'RENDER_EXTERNAL_URL / RENDER_EXTERNAL_HOSTNAME не заданы — '
            'вебхук не будет установлен автоматически. '
            'Используйте команду python manage.py setwebhook <url> вручную.'
        )
        return

    api_url = f'https://api.telegram.org/bot{token}/setWebhook'
    payload = {'url': webhook_url}

    try:
        resp = requests.post(api_url, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get('ok'):
            logger.info('Webhook успешно установлен: %s', webhook_url)
            _webhook_set = True
        else:
            logger.error('Telegram вернул ошибку при установке webhook: %s', result)
    except requests.RequestException as exc:
        logger.error('Не удалось установить webhook: %s', exc)


class BotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bot'

    def ready(self):
        # При автоперезагрузке runserver ready() вызывается дважды:
        # один раз в родительском процессе и один раз в дочернем.
        # Проверяем RUN_MAIN, чтобы не дёргать API лишний раз.
        import sys
        run_main = os.environ.get('RUN_MAIN')
        if run_main != 'true':
            set_webhook()
