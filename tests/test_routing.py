import pytest

from app.schemas import AIDecision, DecisionType, RiskAssessment, RiskLevel
from app.services.routing import Route, route_task


def make_decision(kind: DecisionType) -> AIDecision:
    return AIDecision(
        decision=kind,
        confidence=0.8,
        explanation="test",
        recommended_action="test",
    )


def make_risk(level: RiskLevel) -> RiskAssessment:
    scores = {RiskLevel.LOW: 10, RiskLevel.MEDIUM: 40, RiskLevel.HIGH: 80}
    return RiskAssessment(risk_level=level, risk_score=scores[level], reasons=["test"])


@pytest.mark.parametrize(
    ("decision", "level", "expected"),
    [
        (DecisionType.ALLOW, RiskLevel.LOW, Route.AUTO_EXECUTE),
        (DecisionType.REVIEW, RiskLevel.MEDIUM, Route.HUMAN_REVIEW),
        (DecisionType.DENY, RiskLevel.HIGH, Route.BLOCK),
        (DecisionType.REVIEW, RiskLevel.LOW, Route.HUMAN_REVIEW),
        (DecisionType.DENY, RiskLevel.LOW, Route.BLOCK),
    ],
)
def test_routing_table(decision, level, expected):
    assert route_task(make_decision(decision), make_risk(level)) == expected


def test_ai_allow_cannot_override_high_risk():
    route = route_task(make_decision(DecisionType.ALLOW), make_risk(RiskLevel.HIGH))
    assert route == Route.BLOCK


def test_ai_allow_cannot_skip_review_for_medium_risk():
    route = route_task(make_decision(DecisionType.ALLOW), make_risk(RiskLevel.MEDIUM))
    assert route == Route.HUMAN_REVIEW