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

В форме доступны два режима: «Разобрать роль» запускает обычный judge-workflow,
а «Сравнить модели» отправляет тот же запрос через weak, medium и strong
конфигурации и показывает в интерфейсе время, токены, reasoning-токены,
стоимость и валидность JSON. Frontend-режим выполняет по одному измерительному
прогону на конфигурацию; для статистики по нескольким повторам используйте
CLI-бенчмарк ниже. Если thinking-поток не успевает вернуть финальный ответ,
benchmark повторяет этот вызов без thinking; повтор включается в метрики и
стоимость.

## API

- `GET /api/health` — проверка доступности backend и наличия ключа;
- `POST /api/role-analysis` — legacy-анализ описания роли, сохранённый для
  обратной совместимости;
- `POST /api/llm-as-judge` — потоковая проверка одной задачи через первого
  агента, трёх экспертов и финального судью.
- `POST /api/day5-benchmark` — потоковое сравнение трёх конфигураций DeepSeek;
  тело запроса такое же, как у judge-ручки: `{ "task": "..." }`.

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

## Эксперимент моделей (Day 5)

Benchmark запускает один и тот же запрос через весь judge-пайплайн в трёх
конфигурациях DeepSeek:

- `weak`: `deepseek-v4-flash`, thinking отключён;
- `medium`: `deepseek-v4-flash`, thinking включён, `reasoning_effort=high`;
- `strong`: `deepseek-v4-pro`, thinking включён, `reasoning_effort=high`.

Запускать из `backend` после установки зависимостей:

```bash
python -m benchmarks.day5_model_versions --repetitions 3 --warmup 1
```

Команда выполняет 12 полных прогонов по умолчанию: 3 прогрева и 9 измерений.
Один полный прогон содержит пять обращений к API: первый агент, три эксперта
и финальный судья. Результаты сохраняются в `backend/benchmarks/results/`, а
секреты и ключ DeepSeek в файлы не записываются.

Benchmark измеряет wall-clock latency всего workflow и параллельной группы
экспертов, время до первого reasoning/content, успешность, JSON-контракт,
prompt/completion/reasoning/total tokens, cache hit/miss и стоимость. Для
качества используется автоматическая проверка контракта; содержательную оценку
нужно провести отдельно по финальным ответам.

Для минимального платного прогона используйте `--repetitions 1 --warmup 0`.
Текущие цены и правила peak/off-peak необходимо сверять с [официальной
таблицей DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/).

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
