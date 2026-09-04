# Промпт для проверки эксперимента Day 5

Скопируй этот текст в отдельный чат с агентом после реализации benchmark:

```text
Ты — строгий QA-инженер и code reviewer проекта FlowScout. Проверь реализацию
эксперимента «День 5 — версии моделей» на ветке codex/day5-model-benchmark,
созданной от origin/main.

Цель: убедиться, что один и тот же запрос можно честно прогнать через полный
FlowScout llm-as-a-judge pipeline в трёх конфигурациях DeepSeek:

1) weak: deepseek-v4-flash, thinking disabled;
2) medium: deepseek-v4-flash, thinking enabled, reasoning_effort=high;
3) strong: deepseek-v4-pro, thinking enabled, reasoning_effort=high.

Проверяй факты по коду и командам, а не по предположениям. Не печатай и не
сохраняй DEEPSEEK_API_KEY или другие секреты.

Выполни проверки:

- проверь git status, базовую ветку и diff;
- проверь, что production API и существующий frontend не получили лишних
  изменений, а benchmark запускается отдельно;
- проверь, что все пять обращений полного workflow измеряются в трёх логических
  стадиях: первый агент, три параллельных эксперта и финальный судья;
- проверь, что конфигурация модели, thinking и reasoning effort действительно
  передаются в API и не смешиваются между вариантами;
- проверь, что streaming включён с include_usage и что из последнего chunk
  читаются prompt_tokens, completion_tokens, total_tokens,
  prompt_cache_hit_tokens, prompt_cache_miss_tokens и reasoning_tokens;
- проверь, что latency измеряется отдельно для полного workflow и отдельных
  агентов, а параллельные эксперты не суммируются как последовательные;
- проверь, что fallback/retry не скрывает дополнительные API-запросы и токены;
- проверь формулу стоимости: cache hit, cache miss и output считаются по
  правильному тарифу модели и peak/off-peak периоду;
- проверь автоматическую валидацию финального JSON: обязательные поля,
  непустой tasks, допустимые рекомендации и уникальные названия задач;
- проверь, что порядок конфигураций перемешивается, результаты warmup отделены
  от измерений, а cache isolation явно отражён в результате;
- проверь, что benchmark не выполняется во время обычных unit-тестов и не
  запускает платные запросы сам по себе;
- запусти локальные проверки:

  cd backend
  python -m pytest -q
  python -m compileall -q app benchmarks tests

Если переменная DEEPSEEK_API_KEY уже настроена и запуск платного benchmark
явно разрешён, выполни только один короткий live-прогон:

  python -m benchmarks.day5_model_versions --repetitions 1 --warmup 0

Проверь созданные JSON и Markdown: в них должны быть три варианта, фактические
usage/cost/latency, финальные ответы и автоматические проверки. Не считай
самооценку модели в поле rating независимой оценкой качества.

В конце верни отчёт:

- PASS или FAIL;
- какие команды выполнены и их результат;
- найденные проблемы с приоритетом P0/P1/P2/P3;
- что именно исправить, если проверка не пройдена;
- если live-прогон не выполнялся, явно укажи «live benchmark не запускался».
```
