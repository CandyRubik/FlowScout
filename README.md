# FlowScout

FlowScout — сервис на базе LLM, который разбирает описание роли на задачи, определяет, что оставить человеку, автоматизировать или передать подрядчику, и генерирует черновики workflow для n8n/Make с обязательным подтверждением пользователя.

Текущая версия AI Challenge — веб-чат с DeepSeek, разделённый на два независимых компонента:

- `backend` — FastAPI API, которое хранит ключ DeepSeek только на сервере;
- `frontend` — отдельный браузерный клиент на HTML, CSS и JavaScript.

История диалога хранится в состоянии фронтенда и отправляется вместе с каждым запросом.

## Структура

```text
FlowScout/
├── backend/
│   ├── app/
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── README.md
├── LICENSE
└── .gitignore
```

## Запуск backend

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export DEEPSEEK_API_KEY="your_api_key"
uvicorn app.main:app --reload --port 8000
```

При необходимости модель и URL API можно переопределить:

```bash
export DEEPSEEK_MODEL="deepseek-v4-flash"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
```

## Запуск frontend

В отдельном терминале из корня репозитория:

```bash
python3.13 -m http.server 3000 --directory frontend
```

Откройте [http://localhost:3000](http://localhost:3000).

По умолчанию фронтенд обращается к API на `http://localhost:8000`. Для другого адреса
перед подключением `app.js` можно задать `window.API_BASE_URL`.

## API

- `GET /api/health` — проверка доступности backend и наличия ключа;
- `POST /api/chat` — отправка истории сообщений и получение ответа DeepSeek.
