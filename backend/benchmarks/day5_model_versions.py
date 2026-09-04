from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import statistics
from time import perf_counter
from typing import Callable, Literal

from app.providers.deepseek import (
    DeepSeekProvider,
    LlmUsage,
    ReasoningEffort,
    ThinkingType,
)
from app.schemas import JudgeRequest
from app.services.llm_judge import EXPERT_LABELS, LlmJudgeService
from app.services.llm_metrics import LlmCallMetric, LlmMetricsCollector


PricingPeriod = Literal["off_peak", "peak"]
BenchmarkUpdateCallback = Callable[[dict[str, object]], None]

DEFAULT_TASK = (
    "Операционный менеджер принимает заявки, сверяет данные заказов в CRM, "
    "готовит еженедельный отчёт и передаёт спорные случаи руководителю."
)


@dataclass(frozen=True, slots=True)
class ModelVariant:
    key: str
    label: str
    model: str
    thinking_type: ThinkingType
    reasoning_effort: ReasoningEffort | None


VARIANTS = (
    ModelVariant(
        key="weak",
        label="Слабая",
        model="deepseek-v4-flash",
        thinking_type="disabled",
        reasoning_effort=None,
    ),
    ModelVariant(
        key="medium",
        label="Средняя",
        model="deepseek-v4-flash",
        thinking_type="enabled",
        reasoning_effort="high",
    ),
    ModelVariant(
        key="strong",
        label="Сильная",
        model="deepseek-v4-pro",
        thinking_type="enabled",
        reasoning_effort="high",
    ),
)


@dataclass(frozen=True, slots=True)
class ModelPrice:
    cache_hit: float
    cache_miss: float
    output: float


# USD per one million tokens. Keep this table dated in the generated report:
# DeepSeek may change prices and peak/off-peak rules.
PRICES: dict[PricingPeriod, dict[str, ModelPrice]] = {
    "off_peak": {
        "deepseek-v4-flash": ModelPrice(0.007, 0.22, 0.66),
        "deepseek-v4-pro": ModelPrice(0.022, 0.66, 1.98),
    },
    "peak": {
        "deepseek-v4-flash": ModelPrice(0.014, 0.44, 1.32),
        "deepseek-v4-pro": ModelPrice(0.044, 1.32, 3.96),
    },
}


def is_peak_utc(moment: datetime | None = None) -> bool:
    current = moment or datetime.now(timezone.utc)
    return current.weekday() < 5 and (
        1 <= current.hour < 4 or 6 <= current.hour < 10
    )


def pricing_period(
    override: PricingPeriod | Literal["auto"] = "auto",
    moment: datetime | None = None,
) -> PricingPeriod:
    if override != "auto":
        return override
    return "peak" if is_peak_utc(moment) else "off_peak"


def usage_cost_usd(
    model: str,
    usage: LlmUsage,
    period: PricingPeriod,
) -> float:
    price = PRICES[period][model]
    return (
        usage.prompt_cache_hit_tokens * price.cache_hit
        + usage.prompt_cache_miss_tokens * price.cache_miss
        + usage.completion_tokens * price.output
    ) / 1_000_000


def sum_usage(calls: list[LlmCallMetric]) -> LlmUsage:
    total = LlmUsage()
    for call in calls:
        total += call.usage
    return total


def _normalise_title(value: str) -> str:
    return " ".join(value.casefold().split())


def validate_final_answer(answer: str) -> dict[str, object]:
    """Run cheap, model-independent checks against the judge contract."""

    try:
        payload = json.loads(answer)
    except json.JSONDecodeError:
        return {
            "json_valid": False,
            "contract_valid": False,
            "errors": ["invalid_json"],
        }

    errors: list[str] = []
    if not isinstance(payload, dict):
        return {
            "json_valid": True,
            "contract_valid": False,
            "errors": ["root_is_not_object"],
        }

    for field in ("summary", "rating", "why", "improve", "tasks"):
        if field not in payload:
            errors.append(f"missing_{field}")
    for field in ("summary", "rating", "why", "improve"):
        if field in payload and (
            not isinstance(payload[field], str) or not payload[field].strip()
        ):
            errors.append(f"invalid_{field}")
    if payload.get("rating") not in {
        "Корректен",
        "Частично корректен",
        "Некорректен",
    }:
        errors.append("invalid_rating")

    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("tasks_must_be_non_empty_list")
        tasks = []

    titles: list[str] = []
    allowed_recommendations = {"human", "automate", "contractor"}
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"task_{index}_is_not_object")
            continue
        for field in (
            "title",
            "description",
            "recommendation",
            "rationale",
            "assumptions",
        ):
            if field not in task:
                errors.append(f"task_{index}_missing_{field}")
        for field in ("title", "description", "rationale"):
            if field in task and (
                not isinstance(task[field], str) or not task[field].strip()
            ):
                errors.append(f"task_{index}_invalid_{field}")
        title = task.get("title")
        if isinstance(title, str):
            titles.append(_normalise_title(title))
        recommendation = task.get("recommendation")
        if recommendation not in allowed_recommendations:
            errors.append(f"task_{index}_invalid_recommendation")
        if not isinstance(task.get("assumptions"), list):
            errors.append(f"task_{index}_assumptions_not_list")
        elif not all(
            isinstance(assumption, str) and assumption.strip()
            for assumption in task["assumptions"]
        ):
            errors.append(f"task_{index}_invalid_assumptions")

    if len(titles) != len(set(titles)):
        errors.append("duplicate_task_titles")

    return {
        "json_valid": True,
        "contract_valid": not errors,
        "errors": errors[:20],
    }


def run_once(
    variant: ModelVariant,
    task: str,
    *,
    phase: Literal["warmup", "measurement"],
    repetition: int,
    cache_isolation: bool,
    pricing_override: PricingPeriod | Literal["auto"] = "auto",
    on_update: BenchmarkUpdateCallback | None = None,
) -> dict[str, object]:
    started_at = datetime.now(timezone.utc)
    started_clock = perf_counter()
    user_id = (
        f"day5-{variant.key}-{phase}-{repetition}"
        if cache_isolation
        else None
    )
    provider = DeepSeekProvider(
        model=variant.model,
        thinking_type=variant.thinking_type,
        reasoning_effort=variant.reasoning_effort,
        user_id=user_id,
        # Keep the interactive benchmark usable when a thinking stream exhausts
        # its budget. The retry is still included in usage, cost and request_count.
        retry_without_thinking=True,
    )
    metrics = LlmMetricsCollector()
    service = LlmJudgeService(provider, metrics=metrics)
    final_answer = ""
    error_type: str | None = None
    stage_started_at: dict[str, float] = {}
    stage_durations: dict[str, float] = {}
    completed_experts: set[str] = set()

    def finish_stage(stage: str, moment: float) -> None:
        if stage in stage_started_at and stage not in stage_durations:
            stage_durations[stage] = round(
                (moment - stage_started_at[stage]) * 1_000,
                2,
            )

    try:
        for update in service.stream(JudgeRequest(task=task)):
            update_type = update.get("type")
            if on_update and update_type in {"stage", "agent_done"}:
                on_update(
                    {
                        key: value
                        for key, value in update.items()
                        if key != "answer"
                    },
                )
            now = perf_counter()
            if update_type == "stage":
                stage_started_at[str(update["stage"])] = now
            elif update_type == "agent_done":
                agent = str(update.get("agent"))
                if agent == "first":
                    finish_stage("first", now)
                elif agent in EXPERT_LABELS:
                    completed_experts.add(agent)
                    if completed_experts == set(EXPERT_LABELS):
                        finish_stage("experts", now)
            elif update_type == "done":
                final_answer = str(update.get("answer", ""))
    except Exception as error:  # benchmark must retain partial metrics on failure
        error_type = type(error).__name__

    finished_clock = perf_counter()
    for stage in stage_started_at:
        finish_stage(stage, finished_clock)
    duration_ms = (finished_clock - started_clock) * 1_000
    calls = metrics.calls
    usage = sum_usage(calls)
    period = pricing_period(pricing_override, started_at)
    auto_quality = validate_final_answer(final_answer) if final_answer else {
        "json_valid": False,
        "contract_valid": False,
        "errors": ["no_final_answer"],
    }

    final_answer_received = bool(final_answer)
    all_calls_success = len(calls) == 5 and all(
        call.success for call in calls
    )
    return {
        "phase": phase,
        "repetition": repetition,
        "variant": asdict(variant),
        "started_at": started_at.isoformat(),
        "pricing_period": period,
        "price_per_million_usd": asdict(PRICES[period][variant.model]),
        "cache_isolation": cache_isolation,
        "success": error_type is None and final_answer_received and all_calls_success,
        "final_answer_received": final_answer_received,
        "all_calls_success": all_calls_success,
        "call_failures": [
            call.agent for call in calls if not call.success
        ],
        "error_type": error_type,
        "duration_ms": round(duration_ms, 2),
        "stage_durations_ms": stage_durations,
        "request_count": sum(1 + call.retry_count for call in calls),
        "calls": [call.as_dict() for call in calls],
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "prompt_cache_hit_tokens": usage.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": usage.prompt_cache_miss_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
        },
        "cost_usd": round(usage_cost_usd(variant.model, usage, period), 8),
        "automatic_quality": auto_quality,
        "final_answer": final_answer,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 2)


def summarise_variant(
    variant: ModelVariant,
    runs: list[dict[str, object]],
) -> dict[str, object]:
    successful_runs = [run for run in runs if run["success"]]
    return {
        "variant": asdict(variant),
        "runs": len(runs),
        "success_rate": round(len(successful_runs) / len(runs), 3)
        if runs
        else 0,
        "contract_valid_rate": round(
            sum(
                bool(
                    run["automatic_quality"].get("contract_valid", False),
                )
                for run in runs
            )
            / len(runs),
            3,
        )
        if runs
        else 0,
        "median_duration_ms": _median(
            [float(run["duration_ms"]) for run in runs],
        ),
        "median_experts_wall_ms": _median(
            [
                float(run["stage_durations_ms"]["experts"])
                for run in runs
                if "experts" in run["stage_durations_ms"]
            ],
        ),
        "median_total_tokens": _median(
            [float(run["usage"]["total_tokens"]) for run in runs],
        ),
        "median_completion_tokens": _median(
            [float(run["usage"]["completion_tokens"]) for run in runs],
        ),
        "median_reasoning_tokens": _median(
            [float(run["usage"]["reasoning_tokens"]) for run in runs],
        ),
        "median_cost_usd": _median(
            [float(run["cost_usd"]) for run in runs],
        ),
        "total_measured_cost_usd": round(
            sum(float(run["cost_usd"]) for run in runs),
            8,
        ),
    }


def run_benchmark(
    task: str,
    *,
    repetitions: int,
    warmup: int,
    seed: int,
    cache_isolation: bool,
    pricing_override: PricingPeriod | Literal["auto"] = "auto",
) -> dict[str, object]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    if warmup < 0:
        raise ValueError("warmup cannot be negative")

    started_at = datetime.now(timezone.utc)
    order_rng = random.Random(seed)
    warmup_runs: list[dict[str, object]] = []
    measurement_runs: list[dict[str, object]] = []

    for repetition in range(1, warmup + 1):
        order = list(VARIANTS)
        order_rng.shuffle(order)
        for variant in order:
            warmup_runs.append(
                run_once(
                    variant,
                    task,
                    phase="warmup",
                    repetition=repetition,
                    cache_isolation=cache_isolation,
                    pricing_override=pricing_override,
                ),
            )

    for repetition in range(1, repetitions + 1):
        order = list(VARIANTS)
        order_rng.shuffle(order)
        for variant in order:
            measurement_runs.append(
                run_once(
                    variant,
                    task,
                    phase="measurement",
                    repetition=repetition,
                    cache_isolation=cache_isolation,
                    pricing_override=pricing_override,
                ),
            )

    summary = [
        summarise_variant(
            variant,
            [
                run
                for run in measurement_runs
                if run["variant"]["key"] == variant.key
            ],
        )
        for variant in VARIANTS
    ]
    return {
        "experiment": "day5-model-versions",
        "started_at": started_at.isoformat(),
        "task": task,
        "seed": seed,
        "repetitions": repetitions,
        "warmup": warmup,
        "execution_order": "randomised per repetition; variants are sequential",
        "cache_isolation": cache_isolation,
        "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "variants": [asdict(variant) for variant in VARIANTS],
        "warmup_runs": warmup_runs,
        "runs": measurement_runs,
        "summary": summary,
        "measured_cost_usd": round(
            sum(float(run["cost_usd"]) for run in measurement_runs),
            8,
        ),
        "warmup_cost_usd": round(
            sum(float(run["cost_usd"]) for run in warmup_runs),
            8,
        ),
    }


def _format_number(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_report(result: dict[str, object]) -> str:
    summaries = result["summary"]
    runs = result["runs"]
    lines = [
        "# День 5 — сравнение конфигураций DeepSeek",
        "",
        "> Это сравнение трёх конфигураций DeepSeek, а не трёх независимых моделей HuggingFace.",
        "",
        f"**Запрос:** {result['task']}",
        "",
        "## Методика",
        "",
        "Каждый измеряемый прогон запускает весь FlowScout pipeline: первый агент, "
        "три параллельных эксперта и финальный судья. Порядок конфигураций "
        "перемешивается, результаты warmup в итоговую статистику не входят.",
        "",
        "## Метрики",
        "",
        "| Уровень | Успешность | JSON contract | Median latency, ms | Median experts wall, ms | Median tokens | Median reasoning | Median cost, $ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for summary in summaries:
        variant = summary["variant"]
        lines.append(
            "| {label} ({model}, {thinking_type}/{reasoning_effort}) | {success} | "
            "{contract} | {duration} | {experts} | {tokens} | {reasoning} | {cost} |".format(
                label=variant["label"],
                model=variant["model"],
                thinking_type=variant["thinking_type"],
                reasoning_effort=variant["reasoning_effort"] or "—",
                success=_format_number(summary["success_rate"]),
                contract=_format_number(summary["contract_valid_rate"]),
                duration=_format_number(summary["median_duration_ms"]),
                experts=_format_number(summary["median_experts_wall_ms"]),
                tokens=_format_number(summary["median_total_tokens"]),
                reasoning=_format_number(summary["median_reasoning_tokens"]),
                cost=_format_number(summary["median_cost_usd"]),
            ),
        )

    lines.extend(
        [
            "",
            f"Измеренная стоимость: **${result['measured_cost_usd']:.8f}**; "
            f"warmup: **${result['warmup_cost_usd']:.8f}**.",
            "",
            "## Автоматические проверки",
            "",
            "Проверяются JSON, обязательные поля, enum рекомендаций и уникальность "
            "названий задач. Это не заменяет ручную оценку качества.",
            "",
            "## Ответы для ручной оценки",
            "",
            "Оценивать ответы рекомендуется вслепую по критериям: полнота, "
            "корректность рекомендаций, применимость, отсутствие выдуманных "
            "предположений и соблюдение формата.",
        ],
    )
    for run in runs:
        if not run["final_answer"]:
            continue
        variant = run["variant"]
        answer = str(run["final_answer"]).replace("```", "``\\`")
        lines.extend(
            [
                "",
                f"### {variant['label']} — повтор {run['repetition']}",
                "",
                "```json",
                answer,
                "```",
            ],
        )
    return "\n".join(lines) + "\n"


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).parent / "results" / f"day5-{stamp}.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the FlowScout Day 5 DeepSeek configuration benchmark.",
    )
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--pricing-period",
        choices=("auto", "off_peak", "peak"),
        default="auto",
        help="Use auto to derive peak/off-peak from the UTC start time.",
    )
    parser.add_argument(
        "--no-cache-isolation",
        action="store_true",
        help="Do not set a synthetic user_id; cache hits may affect comparisons.",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output_path = args.output or _default_output_path()
    report_path = args.report or output_path.with_suffix(".md")
    result = run_benchmark(
        args.task,
        repetitions=args.repetitions,
        warmup=args.warmup,
        seed=args.seed,
        cache_isolation=not args.no_cache_isolation,
        pricing_override=args.pricing_period,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(render_report(result), encoding="utf-8")

    print(f"JSON: {output_path}")
    print(f"Report: {report_path}")
    for summary in result["summary"]:
        variant = summary["variant"]
        print(
            f"{variant['label']}: "
            f"success={summary['success_rate']}, "
            f"latency_ms={summary['median_duration_ms']}, "
            f"tokens={summary['median_total_tokens']}, "
            f"cost_usd={summary['median_cost_usd']}",
        )


if __name__ == "__main__":
    main()
