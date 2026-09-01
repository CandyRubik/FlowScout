const API_BASE_URL = (window.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const form = document.querySelector("#chat-form");
const input = document.querySelector("#prompt");
const messagesContainer = document.querySelector("#messages");
const sendButton = document.querySelector("#send-button");
const clearButton = document.querySelector("#clear-button");
const status = document.querySelector("#status");

let messages = [];
let isBusy = false;

function setStatus(message, kind = "") {
  status.textContent = message;
  status.dataset.kind = kind;
}

function renderMessages() {
  messagesContainer.replaceChildren();

  if (messages.length === 0) {
    const emptyState = document.createElement("div");
    emptyState.className = "empty-state";
    emptyState.innerHTML =
      '<div class="empty-icon" aria-hidden="true">✧</div>' +
      "<h2>Начните диалог</h2>" +
      "<p>Задайте вопрос — история останется в этом окне.</p>";
    messagesContainer.append(emptyState);
    return;
  }

  for (const message of messages) {
    const messageElement = document.createElement("article");
    messageElement.className = `message ${message.role}`;

    const author = document.createElement("div");
    author.className = "message-author";
    author.textContent = message.role === "assistant" ? "DeepSeek" : "Вы";

    const content = document.createElement("p");
    content.className = "message-content";
    content.textContent = message.content;

    messageElement.append(author, content);
    messagesContainer.append(messageElement);
  }

  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function setBusy(value) {
  isBusy = value;
  input.disabled = value;
  sendButton.disabled = value;
  clearButton.disabled = value;
  sendButton.textContent = value ? "Ждём…" : "Отправить";
}

async function checkHealth() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/health`);
    if (!response.ok) {
      throw new Error();
    }

    const data = await response.json();
    setStatus(
      data.deepseek_configured
        ? "Готов к диалогу"
        : "Нужен ключ DeepSeek",
      data.deepseek_configured ? "" : "warning",
    );
  } catch {
    setStatus("Бэкенд недоступен", "error");
  }
}

async function handleSubmit(event) {
  event.preventDefault();
  const content = input.value.trim();

  if (!content || isBusy) {
    return;
  }

  messages.push({ role: "user", content });
  input.value = "";
  renderMessages();
  setBusy(true);
  setStatus("DeepSeek печатает…");

  try {
    const response = await fetch(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(data.detail || "Не удалось получить ответ");
    }
    if (!data.message?.content) {
      throw new Error("DeepSeek вернул пустой ответ");
    }

    messages.push(data.message);
    renderMessages();
    setStatus("Готов к диалогу");
  } catch (error) {
    messages.pop();
    input.value = content;
    renderMessages();
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
    input.focus();
  }
}

form.addEventListener("submit", handleSubmit);

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

clearButton.addEventListener("click", () => {
  messages = [];
  renderMessages();
  setStatus("История очищена");
  input.focus();
});

renderMessages();
checkHealth();
