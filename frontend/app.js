const API_BASE_URL = (window.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const onboardingSection = document.querySelector("#onboarding-section");
const roleForm = document.querySelector("#role-form");
const roleDescription = document.querySelector("#role-description");
const descriptionLabel = document.querySelector("#description-label");
const descriptionHelp = document.querySelector("#description-help");
const submitButton = document.querySelector("#submit-button");
const comparisonOptions = document.querySelector("#comparison-options");
const standardResults = document.querySelector("#standard-results");
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
const comparisonSection = document.querySelector("#comparison-section");
const comparisonTask = document.querySelector("#comparison-task");
const comparisonCount = document.querySelector("#comparison-count");
const comparisonGrid = document.querySelector("#comparison-grid");
const resetButton = document.querySelector("#reset-button");
const status = document.querySelector("#status");

let currentRoleDescription = "";
let currentQuestions = [];
let currentMode = "standard";
let isBusy = false;
let comparisonAbortController = null;

const recommendationLabels = {
  human: "Оставить человеку",
  automate: "Автоматизировать",
  contractor: "Передать подрядчику",
};

const expertLabels = {
  analyst: "Аналитик",
  engineer: "Инженер",
  critic: "Критик",
};

const methodDefinitions = {
  direct: {
    title: "Прямой ответ",
    description: "Существующая ручка и текущий system prompt",
    endpoint: "/api/role-analysis?stream=true",
    payload: (task) => ({ role_description: task }),
  },
  "step-by-step": {
    title: "Пошаговое решение",
    description: "Модель явно проверяет критерии по шагам",
    endpoint: "/api/reasoning/step-by-step",
    payload: (task) => ({ task }),
  },
  "prompt-generated": {
    title: "Сгенерированный промпт",
    description: "Сначала prompt engineer, затем решение задачи",
    endpoint: "/api/reasoning/prompt-generated",
    payload: (task) => ({ task }),
  },
  experts: {
    title: "Группа экспертов",
    description: "Аналитик, инженер и критик дают независимые ответы",
    endpoint: "/api/reasoning/experts",
    payload: (task) => ({ task }),
  },
};

function setStatus(message, kind = "") {
  status.textContent = message;
  status.dataset.kind = kind;
}

function setBusy(value) {
  isBusy = value;
  for (const element of roleForm.querySelectorAll("textarea, button, input")) {
    element.disabled = value;
  }
  for (const element of clarificationForm.querySelectorAll("textarea, button")) {
    element.disabled = value;
  }

  resetButton.disabled = false;
  resetButton.textContent = value && currentMode === "comparison"
    ? "Остановить"
    : "Начать заново";
  submitButton.textContent = value
    ? currentMode === "comparison" ? "Запускаем сравнение…" : "Анализируем…"
    : currentMode === "comparison" ? "Запустить сравнение" : "Проанализировать роль";
  clarificationButton.textContent = value ? "Анализируем…" : "Продолжить анализ";
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

function parseSseBlock(block, onEvent) {
  let eventName = "message";
  const dataLines = [];

  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (!dataLines.length) {
    return;
  }

  let data;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch {
    throw new Error("Сервис вернул некорректное потоковое событие");
  }

  onEvent(eventName, data);
  if (eventName === "error") {
    throw new Error(data.message || "Не удалось выполнить потоковый анализ");
  }
}

async function streamRequest(endpoint, payload, onEvent, signal) {
  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(errorMessage(data));
  }
  if (!response.body) {
    throw new Error("Браузер не поддерживает потоковый ответ");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary).replace(/\r/g, "");
        buffer = buffer.slice(boundary + 2);
        parseSseBlock(block, onEvent);
        boundary = buffer.indexOf("\n\n");
      }

      if (done) {
        break;
      }
    }

    if (buffer.trim()) {
      parseSseBlock(buffer.replace(/\r/g, ""), onEvent);
    }
  } finally {
    reader.releaseLock();
  }
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
  onboardingSection.hidden = true;
  standardResults.hidden = false;
  renderQuestions(questions);
  analysisSection.hidden = true;
  clarificationSection.hidden = false;
  resetButton.hidden = false;
  document.querySelector("#clarification-0")?.focus();
}

function showReady(analysis) {
  onboardingSection.hidden = true;
  standardResults.hidden = false;
  clarificationSection.hidden = true;
  renderAnalysis(analysis);
  resetButton.hidden = false;
  setStatus("Анализ готов");
}

async function submitAnalysis(payload) {
  if (isBusy) {
    return;
  }

  currentMode = "standard";
  setBusy(true);
  onboardingSection.hidden = true;
  comparisonSection.hidden = true;
  standardResults.hidden = false;
  clarificationSection.hidden = true;
  analysisSection.hidden = true;
  resetButton.hidden = false;
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

function appendStreamText(element, text) {
  if (!text) {
    return;
  }
  if (element.dataset.empty === "true") {
    element.textContent = "";
    element.dataset.empty = "false";
  }
  element.textContent += text;
  element.scrollTop = element.scrollHeight;
}

function createStreamBlock(title, className = "") {
  const block = document.createElement("section");
  block.className = `stream-block ${className}`.trim();

  const heading = document.createElement("h3");
  heading.textContent = title;

  const output = document.createElement("pre");
  output.className = "stream-output";
  output.textContent = "Ожидаем поток…";
  output.dataset.empty = "true";

  block.append(heading, output);
  return { block, output };
}

function createComparisonCard(methodId) {
  const method = methodDefinitions[methodId];
  const card = document.createElement("article");
  card.className = `comparison-card comparison-card-${methodId}`;

  const header = document.createElement("div");
  header.className = "comparison-card-header";

  const headingGroup = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = method.title;
  const description = document.createElement("p");
  description.textContent = method.description;
  headingGroup.append(title, description);

  const state = document.createElement("span");
  state.className = "comparison-card-state pending";
  state.textContent = "Ожидает запуска";
  header.append(headingGroup, state);
  card.append(header);

  const reasoning = createStreamBlock("Рассуждение модели", "reasoning-block");
  const answer = createStreamBlock("Финальный ответ", "answer-block");
  card.append(reasoning.block, answer.block);

  const result = {
    id: methodId,
    card,
    state,
    reasoning: reasoning.output,
    answer: answer.output,
    prompt: null,
    promptReasoning: null,
    experts: new Map(),
    answerBuffer: "",
  };

  if (methodId === "prompt-generated") {
    const promptReasoning = createStreamBlock(
      "Рассуждение при создании промпта",
      "prompt-reasoning-block",
    );
    const prompt = createStreamBlock("Сгенерированный промпт", "prompt-block");
    card.insertBefore(promptReasoning.block, reasoning.block);
    card.insertBefore(prompt.block, reasoning.block);
    result.promptReasoning = promptReasoning.output;
    result.prompt = prompt.output;
  }

  if (methodId === "experts") {
    reasoning.block.remove();
    answer.block.remove();
    const expertsHeading = document.createElement("h3");
    expertsHeading.className = "experts-heading";
    expertsHeading.textContent = "Рассуждения экспертов";
    card.append(expertsHeading);

    const expertsGrid = document.createElement("div");
    expertsGrid.className = "experts-grid";
    for (const expertId of Object.keys(expertLabels)) {
      const expertCard = document.createElement("section");
      expertCard.className = "expert-card";
      const expertHeader = document.createElement("div");
      expertHeader.className = "expert-header";
      const expertTitle = document.createElement("h4");
      expertTitle.textContent = expertLabels[expertId];
      const expertState = document.createElement("span");
      expertState.className = "expert-state";
      expertState.textContent = "Ожидает";
      expertHeader.append(expertTitle, expertState);
      const expertReasoning = createStreamBlock("Рассуждение");
      const expertAnswer = createStreamBlock("Ответ");
      expertCard.append(expertHeader, expertReasoning.block, expertAnswer.block);
      expertsGrid.append(expertCard);
      result.experts.set(expertId, {
        card: expertCard,
        state: expertState,
        reasoning: expertReasoning.output,
        answer: expertAnswer.output,
      });
    }
    card.append(expertsGrid);

    const consensus = document.createElement("div");
    consensus.className = "expert-consensus";
    consensus.hidden = true;
    card.append(consensus);
    result.consensus = consensus;
  }

  return result;
}

function formatRoleAnalysis(result) {
  if (!result || typeof result !== "object") {
    return String(result || "Пустой ответ");
  }
  if (result.status === "needs_clarification") {
    return `Нужно уточнение:\n${(result.questions || []).map((item) => `• ${item}`).join("\n")}`;
  }
  if (result.status !== "ready" || !result.analysis) {
    return JSON.stringify(result, null, 2);
  }

  const analysis = result.analysis;
  const lines = [analysis.role_title, analysis.role_summary, ""];
  for (const task of analysis.tasks || []) {
    lines.push(`${task.title} — ${recommendationLabels[task.recommendation] || task.recommendation}`);
    lines.push(task.rationale);
    if (task.assumptions?.length) {
      lines.push(`Допущения: ${task.assumptions.join("; ")}`);
    }
    lines.push("");
  }
  return lines.join("\n").trim();
}

function expertResultText(result) {
  return formatRoleAnalysis(result);
}

function handleComparisonEvent(comparison, eventName, data) {
  if (eventName === "phase" || eventName === "status") {
    if (data.expert && comparison.experts.has(data.expert)) {
      comparison.experts.get(data.expert).state.textContent = data.message || "Анализирует…";
    } else if (data.message) {
      comparison.state.textContent = data.message;
    }
    return;
  }

  if (eventName === "reasoning") {
    if (comparison.id === "experts" && data.expert && comparison.experts.has(data.expert)) {
      const expert = comparison.experts.get(data.expert);
      appendStreamText(expert.reasoning, data.text);
    } else if (data.phase === "prompt_generation" && comparison.promptReasoning) {
      appendStreamText(comparison.promptReasoning, data.text);
    } else {
      appendStreamText(comparison.reasoning, data.text);
    }
    return;
  }

  if (eventName === "content") {
    if (comparison.id === "experts" && data.expert && comparison.experts.has(data.expert)) {
      appendStreamText(comparison.experts.get(data.expert).answer, data.text);
    } else {
      comparison.answerBuffer += data.text || "";
      appendStreamText(comparison.answer, data.text);
    }
    return;
  }

  if (eventName === "prompt") {
    appendStreamText(comparison.prompt, data.text);
    return;
  }

  if (eventName === "prompt_ready") {
    if (comparison.prompt) {
      comparison.prompt.textContent = data.prompt || "";
      comparison.prompt.dataset.empty = "false";
    }
    return;
  }

  if (eventName === "expert_done") {
    const expert = comparison.experts.get(data.expert);
    if (!expert) {
      return;
    }
    expert.answer.textContent = expertResultText(data.result);
    expert.answer.dataset.empty = "false";
    expert.state.textContent = "Готово";
    return;
  }

  if (eventName === "done") {
    if (data.result) {
      comparison.answer.textContent = formatRoleAnalysis(data.result);
      comparison.answer.dataset.empty = "false";
    }
    if (Array.isArray(data.experts)) {
      const recommendations = data.experts
        .map((item) => item.result?.analysis?.tasks?.[0]?.recommendation)
        .filter(Boolean);
      const counts = recommendations.reduce((accumulator, recommendation) => {
        accumulator[recommendation] = (accumulator[recommendation] || 0) + 1;
        return accumulator;
      }, {});
      const consensus = Object.entries(counts).sort((left, right) => right[1] - left[1])[0];
      if (consensus && comparison.consensus) {
        comparison.consensus.hidden = false;
        comparison.consensus.textContent = `Мнение большинства: ${recommendationLabels[consensus[0]] || consensus[0]} (${consensus[1]} из ${recommendations.length})`;
      }
    }
    comparison.state.className = "comparison-card-state ready";
    comparison.state.textContent = "Готово";
  }
}

function selectedComparisonMethods() {
  return [...document.querySelectorAll('input[name="comparison-method"]:checked')]
    .map((input) => input.value)
    .filter((methodId) => methodDefinitions[methodId]);
}

async function runComparison(task) {
  if (isBusy) {
    return;
  }

  const methodIds = selectedComparisonMethods();
  if (!methodIds.length) {
    setStatus("Выберите хотя бы один способ сравнения", "warning");
    return;
  }

  currentMode = "comparison";
  comparisonAbortController?.abort();
  comparisonAbortController = new AbortController();
  const signal = comparisonAbortController.signal;
  const comparisons = methodIds.map(createComparisonCard);

  onboardingSection.hidden = true;
  standardResults.hidden = true;
  comparisonSection.hidden = false;
  comparisonTask.textContent = `Исходная задача: ${task}`;
  comparisonCount.textContent = `${comparisons.length} вариантов`;
  comparisonGrid.replaceChildren(...comparisons.map((item) => item.card));
  resetButton.hidden = false;
  setBusy(true);
  setStatus("Запускаем независимые варианты рассуждения…");

  const runs = comparisons.map(async (comparison) => {
    const method = methodDefinitions[comparison.id];
    comparison.state.className = "comparison-card-state running";
    comparison.state.textContent = "Запущено";
    try {
      await streamRequest(
        method.endpoint,
        method.payload(task),
        (eventName, data) => handleComparisonEvent(comparison, eventName, data),
        signal,
      );
      if (comparison.state.classList.contains("running")) {
        comparison.state.className = "comparison-card-state ready";
        comparison.state.textContent = "Готово";
      }
    } catch (error) {
      if (signal.aborted) {
        comparison.state.className = "comparison-card-state stopped";
        comparison.state.textContent = "Остановлено";
        return;
      }
      comparison.state.className = "comparison-card-state error";
      comparison.state.textContent = error.message || "Ошибка";
      const errorOutput = comparison.id === "experts"
        ? comparison.consensus
        : comparison.answer;
      if (errorOutput) {
        errorOutput.hidden = false;
        errorOutput.textContent = error.message || "Не удалось получить ответ";
        errorOutput.dataset.empty = "false";
      }
    }
  });

  await Promise.allSettled(runs);
  if (!signal.aborted) {
    setStatus("Сравнение завершено");
  }
  setBusy(false);
  comparisonAbortController = null;
}

function updateModeUI() {
  currentMode = document.querySelector('input[name="mode"]:checked')?.value || "standard";
  const comparison = currentMode === "comparison";
  comparisonOptions.hidden = !comparison;
  descriptionLabel.textContent = comparison ? "Одна задача для сравнения" : "Описание роли";
  roleDescription.placeholder = comparison
    ? "Например: каждую пятницу формировать сводный отчёт по заказам из таблицы по фиксированному шаблону и отправлять его руководителю…"
    : "Например: операционный менеджер интернет-магазина принимает заявки, контролирует заказы, общается с поставщиками и готовит еженедельные отчёты…";
  descriptionHelp.textContent = comparison
    ? "Для чистого эксперимента опишите одну конкретную рабочую задачу."
    : "Не добавляйте API-ключи и пароли. Чем конкретнее обязанности, тем точнее получится анализ.";
  submitButton.textContent = comparison ? "Запустить сравнение" : "Проанализировать роль";
}

roleForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const description = roleDescription.value.trim();
  if (!description || isBusy) {
    return;
  }

  currentRoleDescription = description;
  currentQuestions = [];
  if (currentMode === "comparison") {
    runComparison(description);
    return;
  }

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

for (const input of document.querySelectorAll('input[name="mode"]')) {
  input.addEventListener("change", updateModeUI);
}

resetButton.addEventListener("click", () => {
  comparisonAbortController?.abort();
  comparisonAbortController = null;
  currentRoleDescription = "";
  currentQuestions = [];
  currentMode = "standard";
  isBusy = false;
  roleForm.reset();
  questionsContainer.replaceChildren();
  tasksContainer.replaceChildren();
  assumptionsContainer.replaceChildren();
  comparisonGrid.replaceChildren();
  clarificationSection.hidden = true;
  analysisSection.hidden = true;
  standardResults.hidden = true;
  comparisonSection.hidden = true;
  onboardingSection.hidden = false;
  resetButton.hidden = true;
  resetButton.textContent = "Начать заново";
  updateModeUI();
  setBusy(false);
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

updateModeUI();
checkHealth();
