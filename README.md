# FlowScout

FlowScout — сервис, который превращает описание роли в понятную карту задач и
основу автоматизированного n8n-пайплайна. Внутри используется LLM-as-a-judge:
три эксперта проверяют предложенную автоматизацию, а финальный судья собирает
из их наблюдений решение.

Текущий frontend показывает этап проверки и поток рассуждений агентов; генерация
финального n8n-пайплайна развивается как следующий продуктовый слой.

## Структура

```text
FlowScout/
├── backend/
│   ├── app/
│   │   ├── providers/
│   │   ├── services/
│   │   ├── main.py
│   │   ├── prompts.py
│   │   └── schemas.py
│   ├── tests/
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

По умолчанию frontend обращается к API на `http://localhost:8000`. Для другого
адреса до подключения `app.js` можно задать `window.API_BASE_URL`.

## API

- `GET /api/health` — проверка доступности backend и наличия ключа;
- `POST /api/role-analysis` — legacy-анализ описания роли, сохранённый для
  обратной совместимости;
- `POST /api/llm-as-judge` — потоковая проверка одной задачи через первого
  агента, трёх экспертов и финального судью.
- `POST /api/temperature-experiment` — три параллельных ответа на один и тот же
  запрос с `temperature` `0`, `0.7` и `1.2`.

Пример запроса для legacy-ручки:

```json
{
  "role_description": "Операционный менеджер принимает заявки, контролирует заказы и готовит отчёты.",
  "clarification_answers": []
}
```

Ответ имеет один из двух статусов:

- `needs_clarification` — backend возвращает до трёх вопросов;
- `ready` — backend возвращает структурированный анализ задач.

Ответ модели валидируется на backend. Неизвестные поля, неверные рекомендации,
дублирующиеся задачи и невалидный JSON отклоняются.

Для judge-режима frontend отправляет:

```json
{
  "task": "Операционный менеджер принимает заявки, сверяет данные заказов в CRM, готовит еженедельный отчёт и передаёт спорные случаи руководителю."
}
```

`/api/llm-as-judge` возвращает `text/event-stream`. События `stage` обозначают
переход между стадиями, `reasoning` и `content` приходят по мере генерации,
`agent_done` закрывает ответ конкретного агента, а `done` содержит итог судьи.
Три экспертных потока запускаются параллельно и отображаются одновременно.
Финальный `done.answer` — JSON с полями `summary`, `rating`, `why`, `improve` и
`tasks`; каждая задача содержит `title`, `description`, `recommendation`,
`rationale` и `assumptions`, чтобы frontend мог показать решение отдельными
карточками действий.

Для эксперимента температур frontend отправляет:

```json
{
  "task": "Операционный менеджер сверяет заказы и готовит еженедельный отчёт."
}
```

Backend не принимает произвольные значения температуры: он всегда запускает
ровно три варианта параллельно и возвращает `results` с полями `temperature`,
`answer` и, при частичной ошибке, `error`. Кнопка «Сравнить температуры» в
frontend показывает ответы рядом и напоминает, для каких задач подходит каждый
режим.

## Проверки

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

В PR1 не используются база данных, workflow-интеграции и реальные внешние
действия. API-ключ DeepSeek хранится только на backend.

## CI/CD

GitHub Actions запускает backend-тесты, проверку компиляции Python и синтаксиса
frontend для каждого pull request и push в `main`. CD пока не настроен: для него
нужно выбрать среду размещения и способ передачи секретов.
