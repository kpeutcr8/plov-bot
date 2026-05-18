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


def _is_valid_image_url(url: str) -> bool:
    """Проверить, что URL отдаёт изображение по Content-Type."""
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True, headers=headers)
        if resp.status_code == 200:
            content_type = resp.headers.get('Content-Type', '')
            content_length = resp.headers.get('Content-Length')
            if content_type == 'image/svg+xml':
                return False
            if content_length is not None and int(content_length) <= 0:
                return False
            return content_type.startswith('image/')
    except requests.RequestException:
        pass

    # Если HEAD не сработал (405 и т.д.), пробуем GET с stream=True
    try:
        resp = requests.get(url, stream=True, timeout=5, headers=headers)
        content_type = resp.headers.get('Content-Type', '')
        content_length = resp.headers.get('Content-Length')
        resp.close()
        if content_type == 'image/svg+xml':
            return False
        if content_length is not None and int(content_length) <= 0:
            return False
        return content_type.startswith('image/')
    except requests.RequestException:
        return False


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
    except requests.HTTPError as exc:
        resp_text = ''
        if exc.response is not None:
            resp_text = exc.response.text
        logger.error('Ошибка при отправке фото: %s — response: %s', exc, resp_text)
        return {}
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
            if not _is_dish_related(title, dish_names, all_keywords, exclude=exclude, strict=strict):
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
            # Доверяем поисковому запросу Pixabay, фильтруем только по exclude
            if exclude and any(exc in tags.lower() for exc in exclude):
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
            # Доверяем поисковому запросу Pexels, фильтруем только по exclude
            if exclude and any(exc in alt.lower() for exc in exclude):
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


def _try_duckduckgo_images(
    query: str,
    dish_names: Tuple[str, ...],
    all_keywords: Tuple[str, ...],
    exclude: Tuple[str, ...] = (),
    strict: bool = True,
) -> Optional[str]:
    """
    Поиск картинок через DuckDuckGo (библиотека duckduckgo-search).
    Бесплатно, без API-ключей.
    """
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = ddgs.images(
                query,
                max_results=100,
                region=random.choice(("wt-wt", "us-en", "ru-ru", "en-gb", "de-de", "fr-fr")),
            )

        candidates = []
        for result in results:
            title = result.get('title', '')
            if not _is_dish_related(title, dish_names, all_keywords, exclude=exclude, strict=True):
                continue
            image_url = result.get('image')
            if image_url:
                candidates.append(image_url)

        if candidates:
            chosen = random.choice(candidates)
            logger.info('DuckDuckGo Images выбрал URL: %s', chosen)
            return chosen

        logger.warning('DuckDuckGo Images не нашёл подходящих изображений после фильтрации.')
    except Exception as exc:
        logger.warning('DuckDuckGo Images ошибка: %s', exc)
    return None


def _try_source(func, *args):
    """Вспомогательная обёртка: получить URL и сразу провалидировать."""
    url = func(*args)
    if url and _is_valid_image_url(url):
        return url
    return None


def get_random_dish_image(config: dict, max_attempts: int = 3) -> str:
    """
    Универсальная функция получения URL картинки блюда.
    config должен содержать: query, dish_names, related, fallback.
    Опционально: exclude.
    Делает до max_attempts параллельных попыток через разные API.
    """
    dish_names = config['dish_names']
    all_keywords = dish_names + config['related']
    exclude = config.get('exclude', ())
    query = config['query']

    wiki_query = config.get('wiki_query', query)

    for attempt in range(1, max_attempts + 1):
        extras = ('recipe', 'cooking', 'delicious', 'food', 'traditional', 'homemade')
        ddg_query = f"{query} {random.choice(extras)}"
        url = _try_source(_try_duckduckgo_images, ddg_query, dish_names, all_keywords, exclude)
        if url:
            logger.info('Валидная картинка найдена DuckDuckGo (попытка %d): %s', attempt, url)
            return url

        # Старые сервисы отключены из цепочки, но код функций сохранён:
        # with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        #     futures = {
        #         executor.submit(
        #             _try_source, _try_wikimedia, wiki_query, dish_names, all_keywords, exclude
        #         ): 'Wikimedia',
        #         executor.submit(
        #             _try_source, _try_pixabay, query, dish_names, all_keywords, exclude
        #         ): 'Pixabay',
        #         executor.submit(
        #             _try_source, _try_pexels, query, dish_names, all_keywords, exclude
        #         ): 'Pexels',
        #     }
        #     for future in concurrent.futures.as_completed(futures):
        #         source_name = futures[future]
        #         try:
        #             url = future.result()
        #             if url:
        #                 logger.info(
        #                     'Валидная картинка найдена %s (попытка %d): %s',
        #                     source_name, attempt, url,
        #                 )
        #                 return url
        #         except Exception as exc:
        #             logger.warning('Ошибка при запросе к %s: %s', source_name, exc)

        logger.info('Попытка %d не дала валидной картинки, пробуем ещё...', attempt)

    logger.warning('Все %d попыток исчерпаны. Используем fallback для %s.', max_attempts, query)
    return config['fallback']


def get_random_plov_image() -> str:
    """Получить случайное фото плова."""
    return get_random_dish_image(PLOV_CONFIG)


def get_random_somsa_image() -> str:
    """Получить случайное фото самсы."""
    return get_random_dish_image(SOMSA_CONFIG)


def get_cobalt_image() -> str:
    """Получить случайное фото Chevrolet Cobalt."""
    query = 'Chevrolet Cobalt white'
    dish_names = ('cobalt', 'chevrolet', 'chevy')
    all_keywords = dish_names + ('car', 'auto', 'sedan', 'white')
    extras = ('car', 'sedan', 'auto', 'vehicle', 'road', 'drive')
    ddg_query = f"{query} {random.choice(extras)}"
    url = _try_duckduckgo_images(ddg_query, dish_names, all_keywords, (), strict=False)
    if url:
        return url
    logger.warning('DuckDuckGo не дал результат для кобальта. Используем fallback.')
    return 'https://upload.wikimedia.org/wikipedia/commons/2/2a/Chevrolet_Cobalt_Sedan_LT.jpg'


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
        result = _send_photo(chat_id, image_url)
        if not result or not result.get('ok'):
            _send_message(chat_id, 'Не удалось загрузить фото, попробуй ещё раз 🍽️')
        return JsonResponse({'ok': True})

    # --- самса ---------------------------------------------------------------
    if text in ('самса', 'somsa', 'сомса'):
        image_url = get_random_somsa_image()
        result = _send_photo(chat_id, image_url)
        if not result or not result.get('ok'):
            _send_message(chat_id, 'Не удалось загрузить фото, попробуй ещё раз 🍽️')
        return JsonResponse({'ok': True})

    # --- кобальт -------------------------------------------------------------
    if text in ('кобальт', 'cobalt'):
        image_url = get_cobalt_image()
        result = _send_photo(chat_id, image_url)
        if not result or not result.get('ok'):
            _send_message(chat_id, 'Не удалось загрузить фото, попробуй ещё раз 🍽️')
        return JsonResponse({'ok': True})

    # --- всё остальное — молчим, чтобы не засорять чат -----------------------
    return JsonResponse({'ok': True})
