# Plov Bot 🤖🍚

Telegram-бот на Django, который в ответ на команду `/плов` (или `/plov`) отправляет случайное фото плова, полученное через бесплатные API поиска изображений.

## Как это работает

Бот принимает вебхуки от Telegram, парсит команды и отправляет ответы через HTTP-запросы к Bot API.

### Цепочка получения изображений (fallback)

1. **Unsplash Source API** — основной метод, не требует API-ключа.
   - URL: `https://source.unsplash.com/random/?plov`
   - Возвращает редирект на случайное фото по ключевому слову.

2. **Pixabay API** — резервный метод.
   - Требует ключ `PIXABAY_API_KEY`.
   - 50 результатов по запросу "plov", выбирается случайный.

3. **Pexels API** — второй резерв.
   - Требует ключ `PEXELS_API_KEY`.
   - 30 результатов, выбирается случайный.

4. **Статический fallback** — если все API недоступны.
   - Зашитый в код URL надёжного изображения.

## Локальный запуск

```bash
# 1. Клонируйте репозиторий
cd plov

# 2. Установите зависимости (uv)
uv sync

# 3. Установите переменные окружения
export TELEGRAM_TOKEN=8560460156:AAF_L2VcN70Nomy2BgrzTxbVkPDQ5AK5gNU
export DJANGO_SECRET_KEY=any-local-secret-key

# 4. Запустите сервер
python manage.py runserver
```

Для локальной отладки вебхуков удобно использовать [ngrok](https://ngrok.com/):
```bash
ngrok http 8000
# получите URL вида https://abc123.ngrok.io
# затем установите вебхук вручную:
python manage.py setwebhook https://abc123.ngrok.io/webhook/
```

## Деплой на Render.com (бесплатно)

### 1. Подготовка

У вас уже есть токен бота от BotFather:
```
TELEGRAM_TOKEN=8560460156:AAF_L2VcN70Nomy2BgrzTxbVkPDQ5AK5gNU
```

### 2. (Опционально) Получить API-ключи

**Pixabay:**
- Зарегистрируйтесь на [pixabay.com](https://pixabay.com/)
- Перейдите в [настройки API](https://pixabay.com/api/docs/)
- Скопируйте ключ

**Pexels:**
- Зарегистрируйтесь на [pexels.com](https://www.pexels.com/)
- Перейдите в [Pexels API](https://www.pexels.com/api/)
- Создайте приложение и скопируйте ключ

> Unsplash Source **не требует** регистрации и API-ключа — это публичный эндпоинт.

### 3. Создать Web Service на Render

1. Зарегистрируйтесь/войдите на [render.com](https://render.com)
2. Нажмите **New +** → **Web Service**
3. Подключите GitHub-репозиторий с проектом
4. Заполните поля:
   - **Name**: `plov-bot` (или любое другое)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn plov.wsgi:application`
5. Нажмите **Advanced** и добавьте переменные окружения:
   - `TELEGRAM_TOKEN` = `8560460156:AAF_L2VcN70Nomy2BgrzTxbVkPDQ5AK5gNU`
   - `PIXABAY_API_KEY` (опционально)
   - `PEXELS_API_KEY` (опционально)
6. Нажмите **Create Web Service**

Render автоматически выдаст URL вида `https://plov-bot.onrender.com`.

### 4. Автоматическая установка вебхука

При первом запуске Django выполнит `bot.apps.ready()`, который:
- Сформирует URL вебхука из `RENDER_EXTERNAL_URL` или `RENDER_EXTERNAL_HOSTNAME`
- Отправит запрос к Telegram API: `setWebhook`

После деплоя откройте бота в Telegram и отправьте `/start`.

### 5. Ручная установка вебхука (если нужно)

Если автоматическая установка не сработала:

```bash
# Через консоль Render (Shell)
python manage.py setwebhook https://ваш-сервис.onrender.com/webhook/
```

Или удалённо через curl:
```bash
curl -X POST "https://api.telegram.org/bot8560460156:AAF_L2VcN70Nomy2BgrzTxbVkPDQ5AK5gNU/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://ваш-сервис.onrender.com/webhook/"}'
```

## Особенности бесплатного тарифа Render

- Сервер "засыпает" после 15 минут бездействия.
- При входящем запросе от Telegram сервер просыпается за 1–2 секунды — это нормально.
- Первое сообщение после простоя может иметь небольшую задержку.

## Структура проекта

```
plov/
├── bot/
│   ├── __init__.py
│   ├── apps.py          # Автоустановка webhook при старте
│   ├── urls.py          # Маршрут /webhook/
│   ├── views.py         # Логика команд и fallback API
│   └── management/
│       └── commands/
│           └── setwebhook.py   # Команда ручной установки
├── plov/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── manage.py
├── pyproject.toml
├── uv.lock
├── Procfile
└── README.md
```
