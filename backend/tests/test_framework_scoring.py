from datetime import datetime, timedelta, timezone

from app.services.framework_scoring import compute_framework_composite_score


def test_recent_high_rated_scores_high():
    score = compute_framework_composite_score(
        avg_overall=7.5,
        user_rating_avg=0.8,
        last_updated=datetime.now(timezone.utc),
        user_weights=None,
        avg_scores=None,
    )
    assert score > 7.0


def test_old_framework_decays():
    recent = compute_framework_composite_score(
        avg_overall=7.5,
        user_rating_avg=0.5,
        last_updated=datetime.now(timezone.utc),
        user_weights=None,
        avg_scores=None,
    )
    old = compute_framework_composite_score(
        avg_overall=7.5,
        user_rating_avg=0.5,
        last_updated=datetime.now(timezone.utc) - timedelta(days=90),
        user_weights=None,
        avg_scores=None,
    )
    assert recent > old


def test_negative_rating_penalizes():
    positive = compute_framework_composite_score(
        avg_overall=7.0,
        user_rating_avg=0.5,
        last_updated=datetime.now(timezone.utc),
        user_weights=None,
        avg_scores=None,
    )
    negative = compute_framework_composite_score(
        avg_overall=7.0,
        user_rating_avg=-0.5,
        last_updated=datetime.now(timezone.utc),
        user_weights=None,
        avg_scores=None,
    )
    assert positive > negative


def test_weighted_scores_used_when_available():
    score = compute_framework_composite_score(
        avg_overall=5.0,
        user_rating_avg=0.0,
        last_updated=datetime.now(timezone.utc),
        user_weights={"clarity_score": 0.5, "structure_score": 0.5},
        avg_scores={"clarity_score": 9.0, "structure_score": 9.0},
    )
    assert score > 7.0  # weighted avg should be 9.0, much higher than avg_overall of 5.0
