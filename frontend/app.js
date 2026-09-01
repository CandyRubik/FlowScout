const API_BASE_URL = (window.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const roleForm = document.querySelector("#role-form");
const roleDescription = document.querySelector("#role-description");
const analyzeButton = document.querySelector("#analyze-button");
const clarificationSection = document.querySelector("#clarification-section");
const clarificationForm = document.querySelector("#clarification-form");
const clarificationButton = document.querySelector("#clarification-button");
const questionsContainer = document.querySelector("#questions");
const analysisSection = document.querySelector("#analysis-section");
const analysisTitle = document.querySelector("#analysis-title");
const roleSummary = document.querySelector("#role-summary");
const taskCount = document.querySelector("#task-count");
const tasksContainer = document.querySelector("#tasks");
const assumptionsSection = document.querySelector("#assumptions-section");
const assumptionsContainer = document.querySelector("#global-assumptions");
const resetButton = document.querySelector("#reset-button");
const status = document.querySelector("#status");

let currentRoleDescription = "";
let currentQuestions = [];
let isBusy = false;

const recommendationLabels = {
  human: "Оставить человеку",
  automate: "Автоматизировать",
  contractor: "Передать подрядчику",
};

function setStatus(message, kind = "") {
  status.textContent = message;
  status.dataset.kind = kind;
}

function setBusy(value) {
  isBusy = value;
  for (const element of roleForm.querySelectorAll("textarea, button")) {
    element.disabled = value;
  }
  for (const element of clarificationForm.querySelectorAll("textarea, button")) {
    element.disabled = value;
  }
  resetButton.disabled = value;
  analyzeButton.textContent = value ? "Анализируем…" : "Проанализировать роль";
  clarificationButton.textContent = value
    ? "Анализируем…"
    : "Продолжить анализ";
}

function errorMessage(data) {
  if (typeof data.detail === "string") {
    return data.detail;
  }
  if (Array.isArray(data.detail)) {
    return data.detail
      .map((item) => item.msg || "Некорректное значение")
      .join("; ");
  }
  return "Не удалось выполнить анализ";
}

async function requestAnalysis(payload) {
  const response = await fetch(`${API_BASE_URL}/api/role-analysis`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(errorMessage(data));
  }
  return data;
}

function renderQuestions(questions) {
  currentQuestions = questions;
  questionsContainer.replaceChildren();

  questions.forEach((question, index) => {
    const field = document.createElement("label");
    field.className = "form-field";
    field.htmlFor = `clarification-${index}`;

    const caption = document.createElement("span");
    caption.textContent = question;

    const answer = document.createElement("textarea");
    answer.id = `clarification-${index}`;
    answer.rows = 3;
    answer.required = true;
    answer.dataset.questionIndex = String(index);
    answer.placeholder = "Ваш ответ…";

    field.append(caption, answer);
    questionsContainer.append(field);
  });
}

function renderAssumptions(assumptions) {
  assumptionsContainer.replaceChildren();
  if (!assumptions.length) {
    assumptionsSection.hidden = true;
    return;
  }

  for (const assumption of assumptions) {
    const item = document.createElement("li");
    item.textContent = assumption;
    assumptionsContainer.append(item);
  }
  assumptionsSection.hidden = false;
}

function renderTask(task) {
  const card = document.createElement("article");
  card.className = "task-card";

  const header = document.createElement("div");
  header.className = "task-header";

  const title = document.createElement("h3");
  title.textContent = task.title;

  const recommendation = document.createElement("span");
  recommendation.className = `recommendation ${task.recommendation}`;
  recommendation.textContent =
    recommendationLabels[task.recommendation] || task.recommendation;
  header.append(title, recommendation);

  const description = document.createElement("p");
  description.className = "task-description";
  description.textContent = task.description;

  const rationaleLabel = document.createElement("strong");
  rationaleLabel.textContent = "Почему:";

  const rationale = document.createElement("p");
  rationale.className = "task-rationale";
  rationale.append(rationaleLabel, document.createTextNode(` ${task.rationale}`));

  card.append(header, description, rationale);

  if (task.assumptions.length) {
    const assumptions = document.createElement("p");
    assumptions.className = "task-assumptions";
    assumptions.textContent = `Предположение: ${task.assumptions.join("; ")}`;
    card.append(assumptions);
  }

  return card;
}

function renderAnalysis(analysis) {
  analysisTitle.textContent = analysis.role_title;
  roleSummary.textContent = analysis.role_summary;
  taskCount.textContent = `${analysis.tasks.length} задач`;
  tasksContainer.replaceChildren(...analysis.tasks.map(renderTask));
  renderAssumptions(analysis.global_assumptions);
  analysisSection.hidden = false;
}

function showClarifications(questions) {
  renderQuestions(questions);
  analysisSection.hidden = true;
  clarificationSection.hidden = false;
  document.querySelector("#clarification-0")?.focus();
}

function showReady(analysis) {
  clarificationSection.hidden = true;
  renderAnalysis(analysis);
  resetButton.hidden = false;
  setStatus("Анализ готов");
}

async function submitAnalysis(payload) {
  if (isBusy) {
    return;
  }

  setBusy(true);
  setStatus("Проверяем описание роли…");

  try {
    const data = await requestAnalysis(payload);
    if (data.status === "needs_clarification") {
      showClarifications(data.questions);
      setStatus("Нужно уточнить несколько деталей");
    } else if (data.status === "ready" && data.analysis) {
      showReady(data.analysis);
    } else {
      throw new Error("Сервис вернул неизвестный формат ответа");
    }
  } catch (error) {
    setStatus(error.message || "Не удалось выполнить анализ", "error");
  } finally {
    setBusy(false);
  }
}

roleForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const description = roleDescription.value.trim();
  if (!description || isBusy) {
    return;
  }

  currentRoleDescription = description;
  currentQuestions = [];
  clarificationSection.hidden = true;
  analysisSection.hidden = true;
  resetButton.hidden = true;
  submitAnalysis({ role_description: description });
});

clarificationForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (isBusy || !currentRoleDescription) {
    return;
  }

  const answers = [...questionsContainer.querySelectorAll("textarea")].map(
    (input, index) => ({
      question: currentQuestions[index],
      answer: input.value.trim(),
    }),
  );
  if (answers.some((item) => !item.answer)) {
    return;
  }

  submitAnalysis({
    role_description: currentRoleDescription,
    clarification_answers: answers,
  });
});

resetButton.addEventListener("click", () => {
  currentRoleDescription = "";
  currentQuestions = [];
  roleForm.reset();
  questionsContainer.replaceChildren();
  tasksContainer.replaceChildren();
  assumptionsContainer.replaceChildren();
  clarificationSection.hidden = true;
  analysisSection.hidden = true;
  assumptionsSection.hidden = true;
  resetButton.hidden = true;
  setStatus("Готов к новому анализу");
  roleDescription.focus();
});

async function checkHealth() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/health`);
    if (!response.ok) {
      throw new Error();
    }
    const data = await response.json();
    setStatus(
      data.deepseek_configured
        ? "Готов к анализу"
        : "Нужен ключ DeepSeek",
      data.deepseek_configured ? "" : "warning",
    );
  } catch {
    setStatus("Backend недоступен", "error");
  }
}

checkHealth();
