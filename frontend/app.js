const API_BASE_URL = (window.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const taskForm = document.querySelector("#task-form");
const taskInput = document.querySelector("#task-input");
const startButton = document.querySelector("#start-button");
const benchmarkButton = document.querySelector("#benchmark-button");
const judgeSection = document.querySelector("#judge-section");
const judgeGraph = document.querySelector("#judge-graph");
const judgeTask = document.querySelector("#judge-task");
const judgeStageLabel = document.querySelector("#judge-stage-label");
const judgeDecision = document.querySelector("#judge-decision");
const decisionSummary = document.querySelector("#decision-summary");
const decisionRating = document.querySelector("#decision-rating");
const decisionWhy = document.querySelector("#decision-why");
const decisionImprove = document.querySelector("#decision-improve");
const decisionTasks = document.querySelector("#decision-tasks");
const decisionTaskCount = document.querySelector("#decision-task-count");
const decisionSource = document.querySelector("#judge-decision-source");
const resetButton = document.querySelector("#reset-button");
const status = document.querySelector("#status");
const benchmarkSection = document.querySelector("#benchmark-section");
const benchmarkTask = document.querySelector("#benchmark-task");
const benchmarkStageLabel = document.querySelector("#benchmark-stage-label");
const benchmarkResults = document.querySelector("#benchmark-results");
const benchmarkSummary = document.querySelector("#benchmark-summary");
const benchmarkAnswers = document.querySelector("#benchmark-answers");

let isBusy = false;
let judgeAbortController = null;
let requestSequence = 0;
let judgeHasFinished = false;
let benchmarkHasFinished = false;
const judgeStreamStates = new Map();
let judgeStreamFlushFrame = null;

const benchmarkVariants = [
  {
    key: "weak",
    label: "Слабая",
    model: "deepseek-v4-flash",
    thinking_type: "disabled",
    reasoning_effort: null,
  },
  {
    key: "medium",
    label: "Средняя",
    model: "deepseek-v4-flash",
    thinking_type: "enabled",
    reasoning_effort: "high",
  },
  {
    key: "strong",
    label: "Сильная",
    model: "deepseek-v4-pro",
    thinking_type: "enabled",
    reasoning_effort: "high",
  },
];

const benchmarkRows = new Map();

const agentLabels = {
  first: "Генератор гипотезы",
  engineer: "Инженер",
  analyst: "Аналитик",
  process_pm: "Проджект-менеджер",
  judge: "Судья",
};

const recommendationLabels = {
  human: "Оставить человеку",
  automate: "Автоматизировать",
  contractor: "Передать на аутсорс",
};

function setStatus(message, kind = "") {
  status.textContent = message;
  status.dataset.kind = kind;
}

function setBusy(value, mode = "judge") {
  isBusy = value;
  for (const element of taskForm.querySelectorAll("textarea, button")) {
    element.disabled = value;
  }
  resetButton.disabled = false;
  resetButton.textContent = value ? "Остановить" : "Новая проверка";
  startButton.querySelector("span").textContent = value
    ? mode === "benchmark"
      ? "Сравниваем…"
      : "Разбираем роль…"
    : "Разобрать роль";
  benchmarkButton.querySelector("span").textContent = value && mode === "benchmark"
    ? "Сравниваем…"
    : "Сравнить модели";
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
  return "Не удалось выполнить проверку";
}

function getJudgeStreamNode(agent, selector) {
  const card = document.querySelector(`[data-thought-agent="${agent}"]`);
  return card?.querySelector(selector) || null;
}

function setAgentState(agent, state, label) {
  const node = document.querySelector(`[data-agent="${agent}"]`);
  const stateNode = node?.querySelector("[data-agent-state]");
  if (!node || !stateNode) {
    return;
  }

  node.classList.remove("is-active", "is-done", "is-error");
  if (state === "active") {
    node.classList.add("is-active");
  } else if (state === "done") {
    node.classList.add("is-done");
  } else if (state === "error") {
    node.classList.add("is-error");
  }
  stateNode.textContent = label;
}

function setThoughtState(agent, state, label) {
  const card = document.querySelector(`[data-thought-agent="${agent}"]`);
  const stateNode = card?.querySelector("[data-thought-status]");
  if (!card || !stateNode) {
    return;
  }

  card.classList.remove("is-active", "is-done", "is-error");
  if (state === "active") {
    card.classList.add("is-active");
  } else if (state === "done") {
    card.classList.add("is-done");
  } else if (state === "error") {
    card.classList.add("is-error");
  }
  stateNode.textContent = label;
}

function setAgentAndThoughtState(agent, state, label) {
  setAgentState(agent, state, label);
  setThoughtState(agent, state, label);
}

function setJudgeStage(stage, message) {
  judgeGraph.dataset.stage = stage;
  judgeStageLabel.textContent = message;

  if (stage === "first") {
    setAgentAndThoughtState("first", "active", "Думает");
    for (const agent of ["engineer", "analyst", "process_pm", "judge"]) {
      setAgentAndThoughtState(agent, "", "Ждёт");
    }
  } else if (stage === "experts") {
    setAgentAndThoughtState("first", "done", "Готово");
    for (const agent of ["engineer", "analyst", "process_pm"]) {
      setAgentAndThoughtState(agent, "active", "Проверяет");
    }
    setAgentAndThoughtState("judge", "", "Ждёт");
  } else if (stage === "judge") {
    for (const agent of ["first", "engineer", "analyst", "process_pm"]) {
      setAgentAndThoughtState(agent, "done", "Готово");
    }
    setAgentAndThoughtState("judge", "active", "Сопоставляет");
  } else if (stage === "done") {
    for (const agent of ["first", "engineer", "analyst", "process_pm", "judge"]) {
      setAgentAndThoughtState(agent, "done", "Готово");
    }
  }
}

function clearJudgeStreams() {
  if (judgeStreamFlushFrame !== null) {
    cancelAnimationFrame(judgeStreamFlushFrame);
    judgeStreamFlushFrame = null;
  }
  judgeStreamStates.clear();

  for (const card of document.querySelectorAll("[data-thought-agent]")) {
    card.classList.remove("is-active", "is-done", "is-error");
    const statusNode = card.querySelector("[data-thought-status]");
    const reasoningNode = card.querySelector("[data-reasoning]");
    const answerNode = card.querySelector("[data-answer]");
    if (statusNode) {
      statusNode.textContent = "Ожидает";
    }
    if (reasoningNode) {
      reasoningNode.textContent = "";
      reasoningNode.scrollTop = 0;
    }
    if (answerNode) {
      answerNode.textContent = "";
      answerNode.scrollTop = 0;
    }
  }
  for (const node of document.querySelectorAll("[data-agent]")) {
    node.classList.remove("is-active", "is-done", "is-error");
  }
}

function resetJudgeView(task) {
  judgeHasFinished = false;
  benchmarkSection.hidden = true;
  judgeTask.textContent = task;
  judgeDecision.hidden = true;
  decisionSummary.textContent = "";
  decisionRating.textContent = "";
  decisionWhy.textContent = "";
  decisionImprove.textContent = "";
  decisionTasks.replaceChildren();
  decisionTaskCount.textContent = "";
  decisionSource.textContent = "";
  clearJudgeStreams();
  setJudgeStage("first", "Генератор гипотезы собирает первый ответ…");
  judgeSection.hidden = false;
  judgeSection.scrollIntoView({ behavior: "auto", block: "start" });
}

function flushJudgeStreamBuffer() {
  judgeStreamFlushFrame = null;
  const changedNodes = [];

  for (const state of judgeStreamStates.values()) {
    if (!state.parts.length) {
      continue;
    }
    state.node.append(document.createTextNode(state.parts.join("")));
    state.parts.length = 0;
    changedNodes.push(state.node);
  }

  for (const node of changedNodes) {
    node.scrollTop = node.scrollHeight;
  }
}

function scheduleJudgeStreamFlush() {
  if (judgeStreamFlushFrame !== null) {
    return;
  }
  judgeStreamFlushFrame = requestAnimationFrame(flushJudgeStreamBuffer);
}

function flushJudgeStreamBufferNow() {
  if (judgeStreamFlushFrame !== null) {
    cancelAnimationFrame(judgeStreamFlushFrame);
  }
  judgeStreamFlushFrame = null;
  flushJudgeStreamBuffer();
}

function appendJudgeText(agent, selector, text) {
  const node = getJudgeStreamNode(agent, selector);
  if (!node || !text) {
    return;
  }

  const key = `${agent}:${selector}`;
  let state = judgeStreamStates.get(key);
  if (!state || state.node !== node) {
    state = { node, parts: [] };
    judgeStreamStates.set(key, state);
  }
  state.parts.push(text);
  scheduleJudgeStreamFlush();
}

function parseSseBlock(block, onEvent) {
  let eventName = "message";
  const dataLines = [];

  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (!dataLines.length) {
    return;
  }
  onEvent(eventName, JSON.parse(dataLines.join("\n")));
}

async function requestSseStream(path, payload, onEvent, signal) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
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

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      if (block.trim()) {
        parseSseBlock(block, onEvent);
      }
    }
    if (done) {
      break;
    }
  }

  if (buffer.trim()) {
    parseSseBlock(buffer, onEvent);
  }
}

async function requestJudgeStream(payload, onEvent, signal) {
  return requestSseStream("/api/llm-as-judge", payload, onEvent, signal);
}

async function requestBenchmarkStream(payload, onEvent, signal) {
  return requestSseStream("/api/day5-benchmark", payload, onEvent, signal);
}

function asText(value) {
  if (Array.isArray(value)) {
    return value.map(asText).filter(Boolean).join("\n").trim();
  }
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).trim();
}

function asList(value) {
  if (Array.isArray(value)) {
    return value.map(asText).filter(Boolean);
  }
  if (typeof value === "string") {
    return value
      .split(/\r?\n/)
      .map((item) => item.replace(/^\s*[-*•]\s*/, "").trim())
      .filter(Boolean);
  }
  return [];
}

function normalizeRecommendation(value) {
  const normalized = asText(value).toLowerCase();
  if (["human", "automate", "contractor"].includes(normalized)) {
    return normalized;
  }
  if (normalized.includes("автомат") || normalized.includes("autom")) {
    return "automate";
  }
  if (normalized.includes("подряд") || normalized.includes("contract")) {
    return "contractor";
  }
  return "human";
}

function normalizeRating(value) {
  const rating = asText(value);
  const normalized = rating.toLowerCase();
  if (normalized.includes("partially correct") || normalized.includes("частично")) {
    return "Частично корректен";
  }
  if (
    normalized.includes("incorrect") ||
    normalized.includes("невер") ||
    normalized.includes("неправ")
  ) {
    return "Некорректен";
  }
  if (normalized === "correct" || normalized === "правильный" || normalized === "правилен") {
    return "Корректен";
  }
  return rating;
}

function normalizeTask(task, index) {
  if (!task || typeof task !== "object") {
    return null;
  }

  return {
    title: asText(task.title ?? task.action ?? task.name) || `Действие ${index + 1}`,
    description:
      asText(task.description ?? task.action_description ?? task.what) ||
      "Действие выделено из описания роли.",
    recommendation: normalizeRecommendation(
      task.recommendation ??
        task.decision ??
        task.automation_decision ??
        task.automation,
    ),
    rationale:
      asText(task.rationale ?? task.reason ?? task.why) ||
      "Рекомендация вынесена по итогам проверки.",
    assumptions: asList(task.assumptions ?? task.conditions ?? task.assumption),
  };
}

function normalizeDecision(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return null;
  }

  const analysis =
    data.analysis && typeof data.analysis === "object" ? data.analysis : {};
  const rawTasks = data.tasks ?? data.actions ?? analysis.tasks ?? [];
  const tasks = Array.isArray(rawTasks)
    ? rawTasks.map(normalizeTask).filter(Boolean)
    : [];

  return {
    summary: asText(data.summary ?? data.result ?? data.verdict ?? analysis.role_summary),
    rating: normalizeRating(data.rating ?? data.assessment),
    why: asText(data.why ?? data.reasons),
    improve: asText(data.improve ?? data.improvements),
    tasks,
  };
}

function parseJsonDecision(text) {
  const normalized = text
    .trim()
    .replace(/^\uFEFF/, "")
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "")
    .trim();
  const candidates = [normalized];
  const objectStart = normalized.indexOf("{");
  const objectEnd = normalized.lastIndexOf("}");
  if (objectStart >= 0 && objectEnd > objectStart) {
    candidates.push(normalized.slice(objectStart, objectEnd + 1));
  }

  for (const candidate of candidates) {
    try {
      const decoded = JSON.parse(candidate);
      const parsed = normalizeDecision(
        typeof decoded === "string" ? JSON.parse(decoded) : decoded,
      );
      if (parsed && (parsed.tasks.length || parsed.summary || parsed.rating)) {
        return parsed;
      }
    } catch {
      // Fall through to the text parser for an incomplete or legacy response.
    }
  }
  return null;
}

function splitDecision(text) {
  const sections = {
    summary: [],
    rating: [],
    why: [],
    improve: [],
  };
  const sectionNames = {
    Итог: "summary",
    Оценка: "rating",
    Почему: "why",
    "Что улучшить": "improve",
  };
  const tasks = [];
  let currentSection = null;
  let inTasks = false;
  let currentTask = null;

  const saveTask = () => {
    const normalizedTask = normalizeTask(currentTask, tasks.length);
    if (normalizedTask) {
      tasks.push(normalizedTask);
    }
    currentTask = null;
  };

  for (const line of text.split(/\r?\n/)) {
    const taskSection = line.match(
      /^\s*(?:Действия|Задачи|План действий|Рекомендации)\s*:\s*(.*)$/i,
    );
    if (taskSection) {
      saveTask();
      currentSection = null;
      inTasks = true;
      continue;
    }

    if (inTasks) {
      const actionHeader =
        line.match(
          /^\s*(?:#{1,3}\s*)?(?:Действие|Задача)\s*(?:№\s*)?\d*\s*[:.)-]\s*(.+)$/i,
        ) || line.match(/^\s*#{1,3}\s*\d+[.)]\s+(.+)$/i) || line.match(/^\s*\d+[.)]\s+(.+)$/i);
      if (actionHeader) {
        saveTask();
        currentTask = { title: actionHeader[1] || actionHeader[2] };
        continue;
      }

      const taskField = line.match(
        /^\s*(Название|Заголовок|Решение|Рекомендация|Описание|Обоснование|Условия|Предположение|Предположения)\s*:\s*(.*)$/i,
      );
      if (taskField && currentTask) {
        const fieldName = taskField[1].toLowerCase();
        const fieldValue = taskField[2];
        if (fieldName === "название" || fieldName === "заголовок") {
          currentTask.title = fieldValue;
        } else if (fieldName === "решение" || fieldName === "рекомендация") {
          currentTask.recommendation = fieldValue;
        } else if (fieldName === "описание") {
          currentTask.description = fieldValue;
        } else if (fieldName === "обоснование") {
          currentTask.rationale = fieldValue;
        } else if (fieldName === "условия" || fieldName.startsWith("предполож")) {
          currentTask.assumptions = fieldValue;
        }
        continue;
      }
    }

    const match = line.match(/^\s*(Итог|Оценка|Почему|Что улучшить)\s*:\s*(.*)$/i);
    if (match) {
      saveTask();
      inTasks = false;
      currentSection = sectionNames[match[1]];
      if (match[2]) {
        sections[currentSection].push(match[2]);
      }
    } else if (currentSection) {
      sections[currentSection].push(line);
    }
  }
  saveTask();

  return {
    ...Object.fromEntries(
      Object.entries(sections).map(([key, lines]) => [key, lines.join("\n").trim()]),
    ),
    tasks,
  };
}

function parseDecision(text) {
  return parseJsonDecision(text) || splitDecision(text);
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
  recommendation.textContent = recommendationLabels[task.recommendation];
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
    const assumptionsLabel = document.createElement("strong");
    assumptionsLabel.textContent = "Условия:";
    const assumptions = document.createElement("p");
    assumptions.className = "task-assumptions";
    assumptions.append(
      assumptionsLabel,
      document.createTextNode(` ${task.assumptions.join("; ")}`),
    );
    card.append(assumptions);
  }

  return card;
}

function taskCountLabel(count) {
  const remainder = count % 10;
  const remainderHundred = count % 100;
  if (remainder === 1 && remainderHundred !== 11) {
    return `${count} действие`;
  }
  if (
    remainder >= 2 &&
    remainder <= 4 &&
    (remainderHundred < 12 || remainderHundred > 14)
  ) {
    return `${count} действия`;
  }
  return `${count} действий`;
}

function renderDecision(finalAnswer) {
  const parsed = parseDecision(finalAnswer);
  const tasks = parsed.tasks.length
    ? parsed.tasks
    : [
        normalizeTask(
          {
            title: "Итог проверки",
            description: parsed.summary || finalAnswer,
            recommendation: "human",
            rationale: parsed.why || "Судья не выделил отдельные действия.",
          },
          0,
        ),
      ];

  decisionSummary.textContent = parsed.summary || "План действий сформирован по результатам проверки.";
  decisionRating.textContent = parsed.rating || "Оценка не выделена отдельно";
  decisionWhy.textContent = parsed.why || "Судья не выделил отдельные причины";
  decisionImprove.textContent = parsed.improve || "Не требуется";
  decisionTasks.replaceChildren(...tasks.map(renderTask));
  decisionTaskCount.textContent = taskCountLabel(tasks.length);
  decisionSource.textContent = finalAnswer;
  return { ...parsed, tasks };
}

function formatBenchmarkDuration(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return value >= 1000
    ? `${(value / 1000).toFixed(2)} с`
    : `${Math.round(value)} мс`;
}

function formatBenchmarkTokens(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return new Intl.NumberFormat("ru-RU").format(Math.round(value));
}

function formatBenchmarkCost(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return `$${value.toFixed(6)}`;
}

function benchmarkVariantConfig(variant) {
  if (variant.thinking_type === "disabled") {
    return "thinking off";
  }
  return `thinking on · effort ${variant.reasoning_effort || "default"}`;
}

function createBenchmarkRow(variant) {
  const row = document.createElement("tr");
  row.dataset.variant = variant.key;

  const level = document.createElement("td");
  level.className = "benchmark-level";
  level.textContent = variant.label;

  const config = document.createElement("td");
  config.className = "benchmark-config";
  const model = document.createElement("span");
  model.textContent = variant.model;
  const settings = document.createElement("small");
  settings.textContent = benchmarkVariantConfig(variant);
  config.append(model, settings);

  const cells = { level, config };
  for (const key of ["status", "duration", "tokens", "reasoning", "cost", "quality"]) {
    const cell = document.createElement("td");
    cell.className = `benchmark-cell-${key}`;
    cells[key] = cell;
  }
  cells.status.dataset.state = "pending";
  cells.status.textContent = "Ожидает";
  cells.duration.textContent = "—";
  cells.tokens.textContent = "—";
  cells.reasoning.textContent = "—";
  cells.cost.textContent = "—";
  cells.quality.dataset.state = "pending";
  cells.quality.textContent = "—";

  row.append(
    level,
    config,
    cells.status,
    cells.duration,
    cells.tokens,
    cells.reasoning,
    cells.cost,
    cells.quality,
  );
  benchmarkRows.set(variant.key, { cells });
  return row;
}

function renderBenchmarkRows(variants) {
  benchmarkRows.clear();
  benchmarkResults.replaceChildren(
    ...variants.map((variant) => createBenchmarkRow(variant)),
  );
}

function setBenchmarkVariantStatus(key, message, state = "") {
  const row = benchmarkRows.get(key);
  if (!row) {
    return;
  }
  row.cells.status.textContent = message;
  row.cells.status.dataset.state = state;
}

function renderBenchmarkAnswer(result) {
  const variant = result.variant || {};
  const details = document.createElement("details");
  details.className = "benchmark-answer";
  const summary = document.createElement("summary");
  summary.textContent = `${variant.label || variant.key || "Модель"} — ответ судьи`;
  const answer = document.createElement("pre");
  answer.textContent = result.final_answer || (
    result.error_type ? `Ошибка: ${result.error_type}` : "Ответ не получен."
  );
  details.append(summary, answer);
  benchmarkAnswers.append(details);
}

function renderBenchmarkResult(result) {
  const key = result.variant?.key;
  const row = benchmarkRows.get(key);
  if (!row) {
    return;
  }

  const quality = result.automatic_quality || {};
  const success = Boolean(result.success);
  const requestCount = Number(result.request_count);
  row.cells.status.textContent = success
    ? requestCount > 5
      ? `Готово · ${requestCount} вызовов`
      : "Готово"
    : `Ошибка${result.error_type ? ` · ${result.error_type}` : ""}`;
  row.cells.status.dataset.state = success ? "success" : "error";
  row.cells.duration.textContent = formatBenchmarkDuration(result.duration_ms);
  row.cells.tokens.textContent = formatBenchmarkTokens(result.usage?.total_tokens);
  row.cells.reasoning.textContent = formatBenchmarkTokens(
    result.usage?.reasoning_tokens,
  );
  row.cells.cost.textContent = formatBenchmarkCost(result.cost_usd);
  row.cells.quality.textContent = quality.contract_valid
    ? "Валиден"
    : quality.json_valid
      ? "Неполный"
      : "Ошибка";
  row.cells.quality.dataset.state = quality.contract_valid
    ? "success"
    : quality.json_valid
      ? "partial"
      : "error";
  renderBenchmarkAnswer(result);
}

function resetBenchmarkView(task) {
  benchmarkHasFinished = false;
  judgeSection.hidden = true;
  benchmarkSection.hidden = false;
  benchmarkSection.querySelector(".benchmark-live-status").dataset.state = "";
  benchmarkTask.textContent = task;
  benchmarkStageLabel.textContent = "Запускаем сравнение…";
  benchmarkSummary.hidden = true;
  benchmarkSummary.textContent = "";
  benchmarkAnswers.replaceChildren();
  renderBenchmarkRows(benchmarkVariants);
  benchmarkSection.scrollIntoView({ behavior: "auto", block: "start" });
}

function formatJudgeAnswer(decision) {
  const lines = [];
  if (decision.summary) {
    lines.push(`Итог: ${decision.summary}`);
  }
  if (decision.tasks.length) {
    lines.push("", "Задачи:");
    decision.tasks.forEach((task, index) => {
      lines.push(`${index + 1}. ${task.title} — ${recommendationLabels[task.recommendation]}`);
    });
  }
  return lines.join("\n") || "Итоговое решение сформировано.";
}

function handleBenchmarkEvent(eventName, payload) {
  if (eventName === "benchmark_start") {
    renderBenchmarkRows(payload.variants || benchmarkVariants);
    benchmarkStageLabel.textContent = "Ожидаем первый результат…";
    return;
  }

  if (eventName === "variant_start") {
    const variant = payload.variant || {};
    const key = variant.key;
    setBenchmarkVariantStatus(key, "Выполняется", "running");
    benchmarkStageLabel.textContent = `${variant.label || key}: запускаем workflow…`;
    setStatus(`Сравнение: ${variant.label || key}`);
    return;
  }

  if (eventName === "variant_stage") {
    const key = payload.variant;
    const row = benchmarkRows.get(key);
    const label = row?.cells.level.textContent || key;
    setBenchmarkVariantStatus(key, payload.message || "Выполняется", "running");
    benchmarkStageLabel.textContent = `${label}: ${payload.message || "следующий этап"}`;
    setStatus(benchmarkStageLabel.textContent);
    return;
  }

  if (eventName === "variant_agent_done") {
    const key = payload.variant;
    const row = benchmarkRows.get(key);
    if (row && payload.agent) {
      setBenchmarkVariantStatus(
        key,
        `${row.cells.status.textContent} · ${payload.agent}`,
        "running",
      );
    }
    return;
  }

  if (eventName === "variant_result") {
    renderBenchmarkResult(payload);
    return;
  }

  if (eventName === "benchmark_done") {
    benchmarkHasFinished = true;
    benchmarkSummary.hidden = false;
    benchmarkSummary.textContent = `Сравнение завершено · ${payload.summary?.length || 0} конфигурации · общая стоимость ${formatBenchmarkCost(payload.measured_cost_usd)}`;
    benchmarkStageLabel.textContent = "Сравнение завершено";
    benchmarkSection.querySelector(".benchmark-live-status").dataset.state = "done";
    setStatus("Сравнение моделей завершено");
    return;
  }

  if (eventName === "error") {
    throw new Error(payload.message || "Не удалось завершить сравнение");
  }
}

function handleJudgeEvent(eventName, payload) {
  const agent = payload.agent;

  if (eventName === "stage") {
    setJudgeStage(payload.stage, payload.message);
    setStatus(payload.message);
    return;
  }

  if (eventName === "status") {
    if (agent) {
      setThoughtState(agent, "active", payload.message || "Продолжает думать");
    }
    if (payload.message) {
      setStatus(`${agentLabels[agent] || "Агент"}: ${payload.message}`);
    }
    return;
  }

  if (eventName === "reasoning") {
    appendJudgeText(agent, "[data-reasoning]", payload.text);
    return;
  }

  if (eventName === "content") {
    appendJudgeText(agent, "[data-answer]", payload.text);
    return;
  }

  if (eventName === "agent_done") {
    flushJudgeStreamBufferNow();
    const answerNode = getJudgeStreamNode(agent, "[data-answer]");
    if (answerNode && !answerNode.textContent && payload.answer) {
      answerNode.textContent = payload.answer;
    }
    setAgentAndThoughtState(agent, "done", "Готово");
    return;
  }

  if (eventName === "agent_error") {
    flushJudgeStreamBufferNow();
    setAgentAndThoughtState(agent, "error", "Ошибка");
    setStatus(`${agentLabels[agent] || "Агент"}: ${payload.message}`, "warning");
    return;
  }

  if (eventName === "done") {
    flushJudgeStreamBufferNow();
    const finalAnswer = payload.answer || "Итоговый ответ не получен.";
    const answerNode = getJudgeStreamNode("judge", "[data-answer]");
    setJudgeStage("done", "Судья вынес итоговое решение");
    const decision = renderDecision(finalAnswer);
    if (answerNode) {
      answerNode.textContent = formatJudgeAnswer(decision);
    }
    judgeDecision.hidden = false;
    judgeHasFinished = true;
    setStatus("Судья вынес итог");
    return;
  }

  if (eventName === "error") {
    flushJudgeStreamBufferNow();
    throw new Error(payload.message || "Не удалось завершить проверку");
  }
}

async function runJudge(task) {
  if (isBusy) {
    return;
  }

  const runId = ++requestSequence;
  judgeAbortController = new AbortController();
  resetJudgeView(task);
  resetButton.hidden = false;
  setBusy(true);
  setStatus("FlowScout разбирает роль и ищет точки автоматизации…");

  try {
    await requestJudgeStream(
      { task },
      (eventName, payload) => handleJudgeEvent(eventName, payload),
      judgeAbortController.signal,
    );
    if (!judgeHasFinished) {
      throw new Error("Поток завершился до финального решения");
    }
  } catch (error) {
    if (error.name === "AbortError") {
      setStatus("Проверка остановлена", "warning");
    } else {
      judgeGraph.dataset.stage = "error";
      setStatus(error.message || "Не удалось завершить проверку", "error");
    }
  } finally {
    if (runId === requestSequence) {
      judgeAbortController = null;
      setBusy(false);
      resetButton.hidden = false;
    }
  }
}

async function runBenchmark(task) {
  if (isBusy) {
    return;
  }

  const runId = ++requestSequence;
  judgeAbortController = new AbortController();
  resetBenchmarkView(task);
  resetButton.hidden = false;
  setBusy(true, "benchmark");
  setStatus("Сравниваем три конфигурации DeepSeek…");

  try {
    await requestBenchmarkStream(
      { task },
      (eventName, payload) => handleBenchmarkEvent(eventName, payload),
      judgeAbortController.signal,
    );
    if (!benchmarkHasFinished) {
      throw new Error("Поток завершился до окончания сравнения");
    }
  } catch (error) {
    if (error.name === "AbortError") {
      benchmarkStageLabel.textContent = "Сравнение остановлено";
      benchmarkSection.querySelector(".benchmark-live-status").dataset.state = "error";
      setStatus("Сравнение остановлено", "warning");
    } else {
      benchmarkStageLabel.textContent = "Сравнение не завершено";
      benchmarkSection.querySelector(".benchmark-live-status").dataset.state = "error";
      setStatus(error.message || "Не удалось завершить сравнение", "error");
    }
  } finally {
    if (runId === requestSequence) {
      judgeAbortController = null;
      setBusy(false);
      resetButton.hidden = false;
    }
  }
}

function resetApp() {
  requestSequence += 1;
  if (judgeAbortController) {
    judgeAbortController.abort();
    judgeAbortController = null;
  }
  taskForm.reset();
  judgeSection.hidden = true;
  benchmarkSection.hidden = true;
  judgeDecision.hidden = true;
  clearJudgeStreams();
  resetButton.hidden = true;
  setBusy(false);
  setStatus("Готов разобрать новую роль");
  taskInput.focus();
}

taskForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const task = taskInput.value.trim();
  if (!task || isBusy) {
    return;
  }
  runJudge(task);
});

resetButton.addEventListener("click", resetApp);

benchmarkButton.addEventListener("click", () => {
  const task = taskInput.value.trim();
  if (isBusy) {
    return;
  }
  if (!task || task.length < 10) {
    taskInput.reportValidity();
    return;
  }
  runBenchmark(task);
});

async function checkHealth() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/health`);
    if (!response.ok) {
      throw new Error();
    }
    const data = await response.json();
    setStatus(
      data.deepseek_configured ? "Готов разобрать новую роль" : "Нужен ключ DeepSeek",
      data.deepseek_configured ? "" : "warning",
    );
  } catch {
    setStatus("Backend недоступен", "error");
  }
}

checkHealth();
