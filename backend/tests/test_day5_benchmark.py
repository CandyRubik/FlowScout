from __future__ import annotations

from app.providers.deepseek import LlmUsage
from benchmarks.day5_model_versions import (
    DEFAULT_TASK,
    PRICES,
    pricing_period,
    usage_cost_usd,
    validate_final_answer,
)


def test_day5_default_task_is_the_project_example() -> None:
    assert "Операционный менеджер" in DEFAULT_TASK
    assert len(DEFAULT_TASK) >= 10


def test_day5_cost_uses_cache_hit_miss_and_output_tokens() -> None:
    usage = LlmUsage(
        prompt_cache_hit_tokens=100,
        prompt_cache_miss_tokens=200,
        completion_tokens=300,
    )

    expected = (100 * 0.007 + 200 * 0.22 + 300 * 0.66) / 1_000_000

    assert usage_cost_usd("deepseek-v4-flash", usage, "off_peak") == expected
    assert PRICES["off_peak"]["deepseek-v4-flash"].output == 0.66


def test_day5_contract_validation_accepts_a_valid_judge_answer() -> None:
    answer = (
        '{"summary":"Итог","rating":"Корректен","why":"Причина",'
        '"improve":"Не требуется","tasks":[{"title":"Проверить данные",'
        '"description":"Сверить заказ","recommendation":"automate",'
        '"rationale":"Повторяемая операция с понятными данными",'
        '"assumptions":[]}]}'
    )

    result = validate_final_answer(answer)

    assert result == {
        "json_valid": True,
        "contract_valid": True,
        "errors": [],
    }


def test_day5_contract_validation_rejects_duplicate_titles() -> None:
    answer = (
        '{"summary":"Итог","rating":"Корректен","why":"Причина",'
        '"improve":"Не требуется","tasks":['
        '{"title":"Проверить данные","description":"A",'
        '"recommendation":"human","rationale":"Причина", "assumptions":[]},'
        '{"title":"проверить   данные","description":"B",'
        '"recommendation":"human","rationale":"Причина", "assumptions":[]}'
        ']}'
    )

    result = validate_final_answer(answer)

    assert result["json_valid"] is True
    assert result["contract_valid"] is False
    assert "duplicate_task_titles" in result["errors"]


def test_pricing_period_override_is_deterministic() -> None:
    assert pricing_period("off_peak") == "off_peak"
    assert pricing_period("peak") == "peak"
