"""
Вьюха для обработки вебхуков от Telegram.

Реализует:
  - приём POST-запросов от Telegram Bot API;
  - обработку команд /start, /плов, /plov;
  - отправку фото плова через несколько внешних API с fallback-цепочкой.
"""

import json
import logging
import os
import random
from typing import Optional
import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_API_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}'

# ---------------------------------------------------------------------------
# Fallback-картинка (аварийный запас).  Ссылка ведёт на публичное изображение
# плова, размещённое на imgur — хостинг с высокой доступностью.
# ---------------------------------------------------------------------------
FALLBACK_IMAGE_URL = (
    'https://upload.wikimedia.org/wikipedia/commons/e/e3/Plov.jpg'
)


def _send_message(chat_id: int, text: str) -> dict:
    """Отправить текстовое сообщение в Telegram."""
    url = f'{TELEGRAM_API_URL}/sendMessage'
    payload = {'chat_id': chat_id, 'text': text}
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.error('Ошибка при отправке сообщения: %s', exc)
        return {}


def _send_photo(chat_id: int, photo_url: str, caption: str = '') -> dict:
    """Отправить фото в Telegram по URL."""
    url = f'{TELEGRAM_API_URL}/sendPhoto'
    payload = {'chat_id': chat_id, 'photo': photo_url}
    if caption:
        payload['caption'] = caption
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.error('Ошибка при отправке фото: %s', exc)
        return {}


# ---------------------------------------------------------------------------
# Логика получения картинки плова — цепочка fallback'ов
# ---------------------------------------------------------------------------

def _try_unsplash() -> Optional[str]:
    """
    Попытка получить случайное фото плова через Unsplash Source API.
    Сервис не требует API-ключа, но может вернуть 404 или редирект.
    """
    unsplash_url = 'https://source.unsplash.com/random/?plov'
    try:
        # allow_redirects=True — следуем за редиректом к финальной картинке
        resp = requests.get(unsplash_url, allow_redirects=True, timeout=15)
        if resp.status_code == 200:
            final_url = resp.url
            logger.info('Unsplash вернул URL: %s', final_url)
            return final_url
        else:
            logger.warning('Unsplash вернул статус %s', resp.status_code)
    except requests.RequestException as exc:
        logger.warning('Unsplash недоступен: %s', exc)
    return None


def _try_pixabay() -> Optional[str]:
    """
    Попытка получить фото через Pixabay API.
    Требуется переменная окружения PIXABAY_API_KEY.
    """
    api_key = os.environ.get('PIXABAY_API_KEY')
    if not api_key:
        logger.info('PIXABAY_API_KEY не задан — пропускаем Pixabay.')
        return None

    pixabay_url = (
        'https://pixabay.com/api/'
        f'?key={api_key}&q=plov&image_type=photo&per_page=50'
    )
    try:
        resp = requests.get(pixabay_url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get('hits', [])
        if hits:
            image_url = random.choice(hits).get('webformatURL')
            if image_url:
                logger.info('Pixabay вернул URL: %s', image_url)
                return image_url
        logger.warning('Pixabay вернул пустой список результатов.')
    except requests.RequestException as exc:
        logger.warning('Pixabay недоступен: %s', exc)
    except (KeyError, ValueError) as exc:
        logger.warning('Некорректный ответ Pixabay: %s', exc)
    return None


def _try_pexels() -> Optional[str]:
    """
    Попытка получить фото через Pexels API.
    Требуется переменная окружения PEXELS_API_KEY.
    """
    api_key = os.environ.get('PEXELS_API_KEY')
    if not api_key:
        logger.info('PEXELS_API_KEY не задан — пропускаем Pexels.')
        return None

    pexels_url = 'https://api.pexels.com/v1/search?query=plov&per_page=30'
    headers = {'Authorization': api_key}
    try:
        resp = requests.get(pexels_url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        photos = data.get('photos', [])
        if photos:
            photo = random.choice(photos)
            # Предпочитаем оригинал, если нет — берём large2x
            src = photo.get('src', {})
            image_url = src.get('original') or src.get('large2x')
            if image_url:
                logger.info('Pexels вернул URL: %s', image_url)
                return image_url
        logger.warning('Pexels вернул пустой список результатов.')
    except requests.RequestException as exc:
        logger.warning('Pexels недоступен: %s', exc)
    except (KeyError, ValueError) as exc:
        logger.warning('Некорректный ответ Pexels: %s', exc)
    return None


def get_random_plov_image() -> str:
    """
    Последовательно пытаемся получить URL картинки плова:
      1. Unsplash Source (без ключа)
      2. Pixabay (если задан PIXABAY_API_KEY)
      3. Pexels (если задан PEXELS_API_KEY)
      4. Статический fallback URL
    """
    url = _try_unsplash()
    if url:
        return url

    url = _try_pixabay()
    if url:
        return url

    url = _try_pexels()
    if url:
        return url

    logger.warning('Все API недоступны. Используем статический fallback.')
    return FALLBACK_IMAGE_URL


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@csrf_exempt
def webhook(request):
    """
    Принимает POST-запросы от Telegram.
    Извлекает chat_id и текст команды, затем отправляет ответ.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    message = data.get('message') or {}
    chat = message.get('chat') or {}
    chat_id = chat.get('id')
    text = (message.get('text') or '').strip().lower()

    # В группах Telegram добавляет @username бота к команде.
    # Убираем всё после @, чтобы /плов@botname стал просто /плов.
    if '@' in text:
        text = text.split('@')[0]

    if not chat_id:
        return JsonResponse({'ok': False, 'error': 'No chat_id'}, status=400)

    # --- /start --------------------------------------------------------------
    if text in ('/start',):
        welcome = (
            'Привет! Я бот, который знает толк в плове.\n\n'
            'Отправь команду /плов (или /plov), '
            'и я пришлю случайное фото этого божественного блюда! 🍚'
        )
        _send_message(chat_id, welcome)
        return JsonResponse({'ok': True})

    # --- /плов /plov ---------------------------------------------------------
    if text in ('/плов', '/plov'):
        image_url = get_random_plov_image()
        _send_photo(
            chat_id,
            image_url,
            caption='Вот ваш порция плова! Приятного аппетита! 🍛'
        )
        return JsonResponse({'ok': True})

    # --- всё остальное -------------------------------------------------------
    _send_message(chat_id, 'Отправьте команду /плов')
    return JsonResponse({'ok': True})
