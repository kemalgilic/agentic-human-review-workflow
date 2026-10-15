"""Risk-based routing.

Fail-safe rule: the risk engine can tighten, but never loosen, the AI decision.
"""

from enum import StrEnum

from app.schemas import AIDecision, DecisionType, RiskAssessment, RiskLevel


class Route(StrEnum):
    AUTO_EXECUTE = "auto_execute"
    HUMAN_REVIEW = "human_review"
    BLOCK = "block"


def route_task(decision: AIDecision, risk: RiskAssessment) -> Route:
    if risk.risk_level == RiskLevel.HIGH or decision.decision == DecisionType.DENY:
        return Route.BLOCK

    if risk.risk_level == RiskLevel.MEDIUM or decision.decision == DecisionType.REVIEW:
        return Route.HUMAN_REVIEW

    return Route.AUTO_EXECUTE