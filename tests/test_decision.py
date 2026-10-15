from app.schemas import AIDecision, DecisionType, Task
from app.services.decision import MIN_CONFIDENCE_FOR_AUTO_ALLOW, make_decision
from app.services.risk import assess_risk


def make_task(action: str, user_input: str = "Please handle this request now", **context) -> Task:
    return Task(
        user_input=user_input,
        requested_action=action,
        target_system="demo_system",
        context=context,
    )


def decide(task: Task) -> AIDecision:
    return make_decision(task, assess_risk(task))


def test_low_risk_is_allowed():
    decision = decide(make_task("read_record"))
    assert decision.decision == DecisionType.ALLOW
    assert decision.confidence >= MIN_CONFIDENCE_FOR_AUTO_ALLOW
    assert decision.recommended_action == "execute_automatically"


def test_medium_risk_goes_to_review():
    decision = decide(make_task("send_email", external_recipient=True))
    assert decision.decision == DecisionType.REVIEW
    assert decision.recommended_action == "route_to_human_reviewer"


def test_high_risk_is_denied():
    decision = decide(make_task("delete_record"))
    assert decision.decision == DecisionType.DENY
    assert decision.recommended_action == "block_and_log"


def test_unclear_low_risk_goes_to_review():
    decision = decide(make_task("read_record", user_input="help"))
    assert decision.decision == DecisionType.REVIEW
    assert decision.confidence < MIN_CONFIDENCE_FOR_AUTO_ALLOW


def test_decision_is_complete():
    decision = decide(make_task("update_crm"))
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.explanation
    assert decision.recommended_action


def test_custom_provider_can_be_injected():
    class AlwaysAllow:
        def decide(self, task, risk):
            return AIDecision(
                decision=DecisionType.ALLOW,
                confidence=1.0,
                explanation="stub provider",
                recommended_action="execute_automatically",
            )

    task = make_task("delete_record")
    decision = make_decision(task, assess_risk(task), provider=AlwaysAllow())
    assert decision.explanation == "stub provider"