import pytest

from academy_features import (
    ACADEMY_CURRICULUM,
    LESSON_KEYS,
    _journal_review,
    _num_from_text,
    _safe_float,
    ensure_growth_schema,
)


def test_curriculum_has_six_unique_phases_and_eighteen_unique_lessons():
    assert len(ACADEMY_CURRICULUM) == 6
    assert len(LESSON_KEYS) == 18
    assert len({phase["key"] for phase in ACADEMY_CURRICULUM}) == 6
    assert all(len(phase["lessons"]) == 3 for phase in ACADEMY_CURRICULUM)


def test_safe_float_accepts_french_decimals_and_enforces_limits():
    assert _safe_float("1,25", 0.1, 5, True) == 1.25
    with pytest.raises(ValueError):
        _safe_float("6", 0.1, 5, True)
    with pytest.raises(ValueError):
        _safe_float("", 0.1, 5, True)


def test_price_extraction_handles_french_chart_labels():
    assert _num_from_text("Entrée : 4 380,50 après confirmation") == 4380.50
    assert _num_from_text("niveau non lisible") is None


def test_journal_review_rewards_process_not_only_result():
    review = _journal_review({
        "risk_pct": 1,
        "result_r": -1,
        "plan_followed": True,
        "context": "Rejet confirmé de la résistance.",
        "emotion_before": "neutre",
    })
    assert "perte conforme au plan" in review
    assert "risque reste" in review


class RecordingConnection:
    def __init__(self):
        self.queries = []

    def run(self, query, **_params):
        self.queries.append(query)
        return []


def test_growth_schema_is_additive_and_does_not_mutate_copy_trading_tables():
    conn = RecordingConnection()
    ensure_growth_schema(conn)
    sql = "\n".join(conn.queries).lower()
    assert "create table if not exists member_learning_profiles" in sql
    assert "create table if not exists trading_journal_entries" in sql
    assert "create table if not exists simulator_sessions" in sql
    assert "update members" not in sql
    assert "copy_actif" not in sql
