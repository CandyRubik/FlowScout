from __future__ import annotations

import os
import select
import sys
import termios

from openai import OpenAI


SYSTEM_PROMPT = "You are a helpful assistant. Answer clearly and concisely."


def _discard_pending_input(file_descriptor: int) -> None:
    while select.select([file_descriptor], [], [], 0)[0]:
        try:
            os.read(file_descriptor, 4096)
        except OSError:
            return


def _request_with_input_blocked(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
):
    """Run a request without echoing or accepting terminal input."""
    file_descriptor: int | None = None
    terminal_state = None

    if sys.stdin.isatty():
        file_descriptor = sys.stdin.fileno()
        terminal_state = termios.tcgetattr(file_descriptor)
        blocked_state = terminal_state.copy()
        blocked_state[6] = terminal_state[6][:]
        blocked_state[3] &= ~(
            termios.ECHO
            | termios.ECHONL
            | termios.ECHOE
            | termios.ECHOK
            | termios.ECHOCTL
            | termios.ICANON
        )
        blocked_state[6][termios.VMIN] = 0
        blocked_state[6][termios.VTIME] = 0
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, blocked_state)

    try:
        return client.chat.completions.create(
            model=model,
            messages=messages,
        )
    finally:
        if file_descriptor is not None and terminal_state is not None:
            _discard_pending_input(file_descriptor)
            termios.tcsetattr(file_descriptor, termios.TCSADRAIN, terminal_state)


def main() -> None:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("Set DEEPSEEK_API_KEY before running the program.")

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    print("DeepSeek chat")
    print("Введите сообщение. Команды: /clear, /help, /quit")

    while True:
        try:
            prompt = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nДо встречи!")
            return

        if not prompt:
            continue
        if prompt in {"/quit", "/exit"}:
            print("До встречи!")
            return
        if prompt == "/clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("История диалога очищена.")
            continue
        if prompt == "/help":
            print("/clear — очистить историю\n/quit — выйти из чата")
            continue

        messages.append({"role": "user", "content": prompt})
        print("\nDeepSeek печатает…", flush=True)

        try:
            response = _request_with_input_blocked(client, model, messages)
            answer = response.choices[0].message.content or ""
        except KeyboardInterrupt:
            messages.pop()
            print("\nЗапрос прерван.")
            return
        except Exception as error:
            messages.pop()
            print(f"\nОшибка запроса: {error}")
            continue

        print(f"DeepSeek:\n{answer}")
        messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
