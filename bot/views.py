"""
Вьюха для обработки вебхуков от Telegram.

Реализует:
  - приём POST-запросов от Telegram Bot API;
  - обработку команд /start, /плов, /plov;
  - отправку фото плова через строго отфильтрованные источники.
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
# Ключевые слова для проверки релевантности изображения
# ---------------------------------------------------------------------------
# Названия блюда — обязательно должно присутствовать хотя бы одно
DISH_NAMES = ('pilaf', 'plov', 'palov', 'polu', 'osh', 'pulao')
# Дополнительные/региональные — только в комбинации с названием блюда
RELATED_WORDS = ('kazan', 'uzbek', 'tajik', 'azerbaijan', 'bukhara', 'rice')
ALL_KEYWORDS = DISH_NAMES + RELATED_WORDS


def _is_plov_related(text: str, strict: bool = False) -> bool:
    """
    Проверить, что текст явно относится к плову.
    strict=True — требует обязательного наличия названия блюда (plov/pilaf).
    """
    if not text:
        return False
    lowered = text.lower()
    if strict:
        return any(name in lowered for name in DISH_NAMES)
    return any(kw in lowered for kw in ALL_KEYWORDS)


# ---------------------------------------------------------------------------
# Fallback-картинка (аварийный запас)
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
# Логика получения картинки плова — жёсткая фильтрация
# ---------------------------------------------------------------------------

def _clean_wikimedia_url(url: str) -> str:
    """Обрезать UTM-метки от URL Wikimedia Commons."""
    if '?' in url:
        return url.split('?')[0]
    return url


def _try_wikimedia() -> Optional[str]:
    """
    Основной метод: Wikimedia Commons Search API.
    Ищет по 'plov food', фильтрует результаты по title — только явно про плов.
    Не требует API-ключа.
    """
    wiki_api = (
        'https://commons.wikimedia.org/w/api.php'
        '?action=query&generator=search&gsrsearch=plov+food'
        '&gsrnamespace=6&prop=imageinfo&iiprop=url|mime'
        '&format=json&gsrlimit=30'
    )
    try:
        resp = requests.get(wiki_api, timeout=15, headers={'User-Agent': 'PlovBot/1.0 (Telegram bot)'})
        resp.raise_for_status()
        data = resp.json()
        pages = data.get('query', {}).get('pages', {})

        candidates = []
        for page in pages.values():
            title = page.get('title', '')
            # Фильтр по названию файла: обязательно должно быть слово plov/pilaf
            if not _is_plov_related(title, strict=True):
                continue
            imageinfo = page.get('imageinfo', [])
            if imageinfo:
                img_url = imageinfo[0].get('url', '')
                if img_url:
                    candidates.append(_clean_wikimedia_url(img_url))

        if candidates:
            chosen = random.choice(candidates)
            logger.info('Wikimedia выбрал URL: %s', chosen)
            return chosen
        logger.warning('Wikimedia не нашёл подходящих изображений после фильтрации.')
    except requests.RequestException as exc:
        logger.warning('Wikimedia недоступен: %s', exc)
    except (KeyError, ValueError) as exc:
        logger.warning('Некорректный ответ Wikimedia: %s', exc)
    return None


def _try_pixabay() -> Optional[str]:
    """
    Резерв: Pixabay API.
    Ищет 'pilaf food', фильтрует по tags — только если есть ключевые слова о плове.
    """
    api_key = os.environ.get('PIXABAY_API_KEY')
    if not api_key:
        logger.info('PIXABAY_API_KEY не задан — пропускаем Pixabay.')
        return None

    pixabay_url = (
        'https://pixabay.com/api/'
        f'?key={api_key}&q=pilaf+food&image_type=photo&per_page=50'
    )
    try:
        resp = requests.get(pixabay_url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get('hits', [])

        candidates = []
        for hit in hits:
            tags = hit.get('tags', '')
            # Жёсткий фильтр: tags должны явно упоминать плов
            if not _is_plov_related(tags):
                continue
            image_url = hit.get('webformatURL')
            if image_url:
                candidates.append(image_url)

        if candidates:
            chosen = random.choice(candidates)
            logger.info('Pixabay выбрал URL: %s', chosen)
            return chosen
        logger.warning('Pixabay не нашёл подходящих изображений после фильтрации.')
    except requests.RequestException as exc:
        logger.warning('Pixabay недоступен: %s', exc)
    except (KeyError, ValueError) as exc:
        logger.warning('Некорректный ответ Pixabay: %s', exc)
    return None


def _try_pexels() -> Optional[str]:
    """
    Второй резерв: Pexels API.
    Ищет 'pilaf food', фильтрует по alt-тексту — только явно про плов.
    """
    api_key = os.environ.get('PEXELS_API_KEY')
    if not api_key:
        logger.info('PEXELS_API_KEY не задан — пропускаем Pexels.')
        return None

    pexels_url = 'https://api.pexels.com/v1/search?query=pilaf+food&per_page=30'
    headers = {'Authorization': api_key}
    try:
        resp = requests.get(pexels_url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        photos = data.get('photos', [])

        candidates = []
        for photo in photos:
            alt = photo.get('alt', '')
            # Жёсткий фильтр: alt должен явно упоминать плов
            if not _is_plov_related(alt):
                continue
            src = photo.get('src', {})
            image_url = src.get('original') or src.get('large2x')
            if image_url:
                candidates.append(image_url)

        if candidates:
            chosen = random.choice(candidates)
            logger.info('Pexels выбрал URL: %s', chosen)
            return chosen
        logger.warning('Pexels не нашёл подходящих изображений после фильтрации.')
    except requests.RequestException as exc:
        logger.warning('Pexels недоступен: %s', exc)
    except (KeyError, ValueError) as exc:
        logger.warning('Некорректный ответ Pexels: %s', exc)
    return None


def get_random_plov_image() -> str:
    """
    Последовательно пытаемся получить URL картинки плова:
      1. Wikimedia Commons (строгая фильтрация по title)
      2. Pixabay (фильтрация по tags)
      3. Pexels (фильтрация по alt)
      4. Статический fallback URL
    """
    url = _try_wikimedia()
    if url:
        return url

    url = _try_pixabay()
    if url:
        return url

    url = _try_pexels()
    if url:
        return url

    logger.warning('Все API не дали плов. Используем статический fallback.')
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
            caption='Вот ваша порция плова! Приятного аппетита! 🍛'
        )
        return JsonResponse({'ok': True})

    # --- всё остальное -------------------------------------------------------
    _send_message(chat_id, 'Отправьте команду /плов')
    return JsonResponse({'ok': True})
