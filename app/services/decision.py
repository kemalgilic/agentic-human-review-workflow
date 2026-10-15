"""Demo AI decision layer.

A rule-based stand-in for an LLM call, hidden behind a small provider
interface. It contains no real prompts, models or API calls. A real provider
could implement DecisionProvider and be injected without changing the rest of
the workflow.
"""

from typing import Protocol

from app.schemas import AIDecision, DecisionType, RiskAssessment, RiskLevel, Task
from app.services.risk import MIN_WORDS_FOR_CLEAR_CONTEXT

# Illustrative confidence values.
CONFIDENCE_BY_LEVEL: dict[RiskLevel, float] = {
    RiskLevel.LOW: 0.92,
    RiskLevel.MEDIUM: 0.70,
    RiskLevel.HIGH: 0.90,
}
UNCLEAR_CONTEXT_PENALTY = 0.25
MIN_CONFIDENCE_FOR_AUTO_ALLOW = 0.75


class DecisionProvider(Protocol):
    def decide(self, task: Task, risk: RiskAssessment) -> AIDecision: ...


class RuleBasedDecisionProvider:
    """Deterministic mock decision provider used by the demo."""

    def decide(self, task: Task, risk: RiskAssessment) -> AIDecision:
        confidence = CONFIDENCE_BY_LEVEL[risk.risk_level]
        unclear = len(task.user_input.split()) < MIN_WORDS_FOR_CLEAR_CONTEXT
        if unclear:
            confidence -= UNCLEAR_CONTEXT_PENALTY
        confidence = round(max(0.05, min(confidence, 1.0)), 2)

        summary = (
            f"Risk {risk.risk_level.value} (score {risk.risk_score}): "
            + ", ".join(risk.reasons)
        )

        if risk.risk_level == RiskLevel.HIGH:
            return AIDecision(
                decision=DecisionType.DENY,
                confidence=confidence,
                explanation=f"{summary}. High-risk request is not executed automatically.",
                recommended_action="block_and_log",
            )

        if risk.risk_level == RiskLevel.MEDIUM:
            return AIDecision(
                decision=DecisionType.REVIEW,
                confidence=confidence,
                explanation=f"{summary}. Moderate risk requires a human reviewer.",
                recommended_action="route_to_human_reviewer",
            )

        if confidence < MIN_CONFIDENCE_FOR_AUTO_ALLOW:
            return AIDecision(
                decision=DecisionType.REVIEW,
                confidence=confidence,
                explanation=f"{summary}. Confidence is too low for automatic approval.",
                recommended_action="route_to_human_reviewer",
            )

        return AIDecision(
            decision=DecisionType.ALLOW,
            confidence=confidence,
            explanation=f"{summary}. Low-risk request can be executed automatically.",
            recommended_action="execute_automatically",
        )


_default_provider = RuleBasedDecisionProvider()


def make_decision(
    task: Task,
    risk: RiskAssessment,
    provider: DecisionProvider | None = None,
) -> AIDecision:
    return (provider or _default_provider).decide(task, risk)