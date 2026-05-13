"""
Management-команда для ручной установки Telegram webhook.

Примеры использования:
    python manage.py setwebhook
    python manage.py setwebhook https://example.com/webhook/
"""

import os
import requests
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Установить webhook для Telegram-бота'

    def add_arguments(self, parser):
        parser.add_argument(
            'url',
            nargs='?',
            type=str,
            help='Полный URL вебхука (опционально, иначе берётся из окружения)',
        )

    def handle(self, *args, **options):
        token = os.environ.get('TELEGRAM_TOKEN')
        if not token:
            self.stderr.write(self.style.ERROR('TELEGRAM_TOKEN не задан!'))
            return

        url = options.get('url')
        if not url:
            render_url = os.environ.get('RENDER_EXTERNAL_URL')
            hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            if render_url:
                url = f"{render_url.rstrip('/')}/webhook/"
            elif hostname:
                url = f"https://{hostname.rstrip('/')}/webhook/"

        if not url:
            self.stderr.write(
                self.style.ERROR(
                    'URL не задан. Передайте аргументом или '
                    'установите RENDER_EXTERNAL_URL / RENDER_EXTERNAL_HOSTNAME.'
                )
            )
            return

        api_url = f'https://api.telegram.org/bot{token}/setWebhook'
        try:
            resp = requests.post(api_url, json={'url': url}, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            if result.get('ok'):
                self.stdout.write(self.style.SUCCESS(f'Webhook установлен: {url}'))
            else:
                self.stderr.write(
                    self.style.ERROR(f'Ошибка от Telegram: {result}')
                )
        except requests.RequestException as exc:
            self.stderr.write(self.style.ERROR(f'Ошибка сети: {exc}'))
