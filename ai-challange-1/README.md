# AI Challenge 0

Минимальный интерактивный CLI-чат на Python, который отправляет сообщения в DeepSeek API и сохраняет историю диалога во время работы программы.

## Запуск

```bash
cd ai-challange-1
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export DEEPSEEK_API_KEY="your_api_key"
python main.py
```

После запуска можно вводить новые сообщения одно за другим — перезапускать программу не нужно.
Пока DeepSeek формирует ответ, ввод временно блокируется, чтобы текст не выглядел сообщением от модели.

## Команды

- `/clear` — очистить историю диалога
- `/help` — показать подсказку
- `/quit` или `Ctrl+C` — выйти

При необходимости модель можно переопределить:

```bash
export DEEPSEEK_MODEL="deepseek-v4-flash"
```
