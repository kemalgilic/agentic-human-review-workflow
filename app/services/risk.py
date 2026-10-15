"""Demo risk engine: simple additive scoring with generic, illustrative weights.

This is a portfolio implementation. It intentionally contains no production
policy logic or proprietary scoring models.
"""

from app.schemas import RequestedAction, RiskAssessment, RiskLevel, Task

# Base risk per action type (illustrative values).
ACTION_BASE_SCORE: dict[RequestedAction, int] = {
    RequestedAction.READ_RECORD: 5,
    RequestedAction.CREATE_TICKET: 15,
    RequestedAction.UPDATE_CRM: 20,
    RequestedAction.SEND_EMAIL: 25,
    RequestedAction.DELETE_RECORD: 25,
}

# Signal weights (illustrative values).
WEIGHT_SENSITIVE_DATA = 30
WEIGHT_DESTRUCTIVE = 35
WEIGHT_EXTERNAL_RECIPIENT = 25
WEIGHT_PRIVILEGED = 30
WEIGHT_IMPACT = {"low": 0, "medium": 10, "high": 20}
WEIGHT_UNCLEAR_CONTEXT = 15

MIN_WORDS_FOR_CLEAR_CONTEXT = 3
MEDIUM_THRESHOLD = 30
HIGH_THRESHOLD = 60


def level_for_score(score: int) -> RiskLevel:
    if score >= HIGH_THRESHOLD:
        return RiskLevel.HIGH
    if score >= MEDIUM_THRESHOLD:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def assess_risk(task: Task) -> RiskAssessment:
    """Score a task and explain why. Pure function, no side effects."""
    ctx = task.context
    score = ACTION_BASE_SCORE[task.requested_action]
    reasons: list[str] = [f"action '{task.requested_action.value}' base risk"]

    if ctx.data_classification == "sensitive":
        score += WEIGHT_SENSITIVE_DATA
        reasons.append("sensitive data involved")

    if ctx.destructive or task.requested_action == RequestedAction.DELETE_RECORD:
        score += WEIGHT_DESTRUCTIVE
        reasons.append("destructive action")

    if ctx.external_recipient:
        score += WEIGHT_EXTERNAL_RECIPIENT
        reasons.append("external recipient")

    if ctx.privileged_operation:
        score += WEIGHT_PRIVILEGED
        reasons.append("privileged operation")

    impact_points = WEIGHT_IMPACT[ctx.impact]
    if impact_points:
        score += impact_points
        reasons.append(f"{ctx.impact} impact")

    if len(task.user_input.split()) < MIN_WORDS_FOR_CLEAR_CONTEXT:
        score += WEIGHT_UNCLEAR_CONTEXT
        reasons.append("unclear context (very short request)")

    score = min(score, 100)
    return RiskAssessment(
        risk_level=level_for_score(score),
        risk_score=score,
        reasons=reasons,
    )