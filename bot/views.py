"""
Вьюха для обработки вебхуков от Telegram.

Реализует:
  - приём POST-запросов от Telegram Bot API;
  - обработку команд /start, «плов», «самса»;
  - отправку фото блюд через строго отфильтрованные источники.
"""

import concurrent.futures
import json
import logging
import os
import random
from typing import Optional, Tuple
import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_API_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}'

# ---------------------------------------------------------------------------
# Параметры блюд
# ---------------------------------------------------------------------------

PLOV_CONFIG = {
    'query': 'pilaf food',
    'wiki_query': 'pilaf food',
    'dish_names': ('pilaf', 'plov', 'palov', 'polu', 'osh', 'pulao'),
    'related': ('kazan', 'uzbek', 'tajik', 'azerbaijan', 'bukhara', 'rice'),
    'fallback': 'https://upload.wikimedia.org/wikipedia/commons/e/e3/Plov.jpg',
}

SOMSA_CONFIG = {
    'query': 'samsa',
    'wiki_query': 'samsa pastry',
    'dish_names': ('samsa', 'somsa', 'samosa', 'samoosa', 'sambusa', 'samuchka'),
    'related': ('uzbek', 'tajik'),
    'exclude': (
        'man', 'woman', 'person', 'people', 'microphone', 'concert', 'portrait',
        'singer', 'artist', 'traum', 'festival', 'band', 'echelon', 'gregor',
        'kafka', 'book', 'novel', 'john', 'giovanni', 'dpla', 'amphi',
        'museum', 'archive', 'stage', 'performance', 'metamorphosis',
    ),
    'fallback': 'https://upload.wikimedia.org/wikipedia/commons/f/f1/Uzbek_samsa.jpg',
}


def _is_dish_related(
    text: str,
    dish_names: Tuple[str, ...],
    all_keywords: Tuple[str, ...],
    exclude: Tuple[str, ...] = (),
    strict: bool = False,
) -> bool:
    """
    Проверить, что текст явно относится к блюду.
    strict=True — требует обязательного наличия названия блюда.
    exclude — слова, которые точно не должны встречаться (люди, концерты и т.д.).
    """
    if not text:
        return False
    lowered = text.lower()
    if exclude and any(exc in lowered for exc in exclude):
        return False
    if strict:
        return any(name in lowered for name in dish_names)
    return any(kw in lowered for kw in all_keywords)


def _clean_wikimedia_url(url: str) -> str:
    """Обрезать UTM-метки от URL Wikimedia Commons."""
    if '?' in url:
        return url.split('?')[0]
    return url


# ---------------------------------------------------------------------------
# Отправка сообщений
# ---------------------------------------------------------------------------

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


def _send_photo(chat_id: int, photo_url: str) -> dict:
    """Отправить фото в Telegram по URL."""
    url = f'{TELEGRAM_API_URL}/sendPhoto'
    payload = {'chat_id': chat_id, 'photo': photo_url}
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.error('Ошибка при отправке фото: %s', exc)
        return {}


# ---------------------------------------------------------------------------
# Универсальные поисковые функции
# ---------------------------------------------------------------------------

def _try_wikimedia(
    query: str,
    dish_names: Tuple[str, ...],
    all_keywords: Tuple[str, ...],
    exclude: Tuple[str, ...] = (),
) -> Optional[str]:
    """Wikimedia Commons Search API со случайным offset и фильтрацией."""
    offset = random.randint(1, 100)
    wiki_api = (
        'https://commons.wikimedia.org/w/api.php'
        f'?action=query&generator=search&gsrsearch={requests.utils.quote(query)}'
        '&gsrnamespace=6&prop=imageinfo&iiprop=url|mime'
        f'&format=json&gsrlimit=30&gsroffset={offset}'
    )
    try:
        resp = requests.get(
            wiki_api, timeout=5,
            headers={'User-Agent': 'PlovBot/1.0 (Telegram bot)'},
        )
        resp.raise_for_status()
        data = resp.json()
        pages = data.get('query', {}).get('pages', {})

        candidates = []
        for page in pages.values():
            title = page.get('title', '')
            if not _is_dish_related(title, dish_names, all_keywords, exclude=exclude, strict=True):
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


def _try_pixabay(
    query: str,
    dish_names: Tuple[str, ...],
    all_keywords: Tuple[str, ...],
    exclude: Tuple[str, ...] = (),
) -> Optional[str]:
    """Pixabay API с динамической пагинацией и строгой фильтрацией по tags."""
    api_key = os.environ.get('PIXABAY_API_KEY')
    if not api_key:
        logger.info('PIXABAY_API_KEY не задан — пропускаем Pixabay.')
        return None

    base_url = (
        'https://pixabay.com/api/'
        f'?key={api_key}&q={requests.utils.quote(query)}'
        '&image_type=photo&per_page=50'
    )
    try:
        # Сначала узнаем сколько всего результатов, чтобы не лезть на пустые страницы
        resp_first = requests.get(f'{base_url}&page=1', timeout=5)
        resp_first.raise_for_status()
        data_first = resp_first.json()
        total_hits = data_first.get('totalHits', 0)
        if total_hits == 0:
            logger.warning('Pixabay: 0 результатов по запросу "%s"', query)
            return None

        max_page = min(10, (total_hits + 49) // 50)
        page = random.randint(1, max_page)

        # Если случайная страница != 1, делаем второй запрос
        if page == 1:
            data = data_first
        else:
            resp = requests.get(f'{base_url}&page={page}', timeout=5)
            resp.raise_for_status()
            data = resp.json()

        hits = data.get('hits', [])
        candidates = []
        for hit in hits:
            tags = hit.get('tags', '')
            # Строгая фильтрация: tags должны явно содержать название блюда
            if not _is_dish_related(tags, dish_names, all_keywords, exclude=exclude, strict=True):
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


def _try_pexels(
    query: str,
    dish_names: Tuple[str, ...],
    all_keywords: Tuple[str, ...],
    exclude: Tuple[str, ...] = (),
) -> Optional[str]:
    """Pexels API со случайной страницей и строгой фильтрацией по alt."""
    api_key = os.environ.get('PEXELS_API_KEY')
    if not api_key:
        logger.info('PEXELS_API_KEY не задан — пропускаем Pexels.')
        return None

    page = random.randint(1, 5)
    pexels_url = (
        f'https://api.pexels.com/v1/search'
        f'?query={requests.utils.quote(query)}&per_page=80&page={page}'
    )
    headers = {'Authorization': api_key}
    try:
        resp = requests.get(pexels_url, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        photos = data.get('photos', [])

        candidates = []
        for photo in photos:
            alt = photo.get('alt', '')
            # Строгая фильтрация: alt должен явно содержать название блюда
            if not _is_dish_related(alt, dish_names, all_keywords, exclude=exclude, strict=True):
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


def get_random_dish_image(config: dict) -> str:
    """
    Универсальная функция получения URL картинки блюда.
    config должен содержать: query, dish_names, related, fallback.
    Опционально: exclude.
    """
    dish_names = config['dish_names']
    all_keywords = dish_names + config['related']
    exclude = config.get('exclude', ())
    query = config['query']

    wiki_query = config.get('wiki_query', query)

    url = _try_wikimedia(wiki_query, dish_names, all_keywords, exclude)
    if url:
        return url

    url = _try_pixabay(query, dish_names, all_keywords, exclude)
    if url:
        return url

    url = _try_pexels(query, dish_names, all_keywords, exclude)
    if url:
        return url

    logger.warning('Все API не дали результат. Используем fallback для %s.', query)
    return config['fallback']


def get_random_plov_image() -> str:
    """Получить случайное фото плова."""
    return get_random_dish_image(PLOV_CONFIG)


def get_random_somsa_image() -> str:
    """Получить случайное фото самсы."""
    return get_random_dish_image(SOMSA_CONFIG)


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
            'Привет! Я бот, который знает толк в еде.\n\n'
            'Напиши «плов» — пришлю фото плова.\n'
            'Напиши «самса» — пришлю фото самсы. 🍽️'
        )
        _send_message(chat_id, welcome)
        return JsonResponse({'ok': True})

    # --- плов ----------------------------------------------------------------
    if text == 'плов':
        image_url = get_random_plov_image()
        _send_photo(chat_id, image_url)
        return JsonResponse({'ok': True})

    # --- самса ---------------------------------------------------------------
    if text in ('самса', 'somsa', 'сомса'):
        image_url = get_random_somsa_image()
        _send_photo(chat_id, image_url)
        return JsonResponse({'ok': True})

    # --- всё остальное — молчим, чтобы не засорять чат -----------------------
    return JsonResponse({'ok': True})
