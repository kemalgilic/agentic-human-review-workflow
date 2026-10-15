import pytest

from app.schemas import RiskLevel, Task
from app.services.risk import assess_risk, level_for_score


def make_task(action: str, user_input: str = "Please handle this request now", **context) -> Task:
    return Task(
        user_input=user_input,
        requested_action=action,
        target_system="demo_system",
        context=context,
    )


def test_low_risk_read_internal():
    result = assess_risk(make_task("read_record"))
    assert result.risk_level == RiskLevel.LOW
    assert result.risk_score < 30


def test_external_email_is_medium():
    result = assess_risk(make_task("send_email", external_recipient=True))
    assert result.risk_level == RiskLevel.MEDIUM
    assert "external recipient" in result.reasons


def test_sensitive_external_email_is_high():
    result = assess_risk(
        make_task("send_email", external_recipient=True, data_classification="sensitive")
    )
    assert result.risk_level == RiskLevel.HIGH
    assert "sensitive data involved" in result.reasons


def test_delete_is_high():
    result = assess_risk(make_task("delete_record"))
    assert result.risk_level == RiskLevel.HIGH
    assert "destructive action" in result.reasons


def test_unclear_context_adds_risk():
    clear = assess_risk(make_task("create_ticket"))
    vague = assess_risk(make_task("create_ticket", user_input="help"))
    assert vague.risk_score > clear.risk_score
    assert vague.risk_level == RiskLevel.MEDIUM
    assert any("unclear context" in r for r in vague.reasons)


def test_score_is_capped_at_100():
    result = assess_risk(
        make_task(
            "delete_record",
            user_input="go",
            data_classification="sensitive",
            destructive=True,
            external_recipient=True,
            privileged_operation=True,
            impact="high",
        )
    )
    assert result.risk_score == 100
    assert result.risk_level == RiskLevel.HIGH


def test_every_assessment_has_reasons():
    result = assess_risk(make_task("read_record"))
    assert len(result.reasons) >= 1


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, RiskLevel.LOW),
        (29, RiskLevel.LOW),
        (30, RiskLevel.MEDIUM),
        (59, RiskLevel.MEDIUM),
        (60, RiskLevel.HIGH),
        (100, RiskLevel.HIGH),
    ],
)
def test_level_thresholds(score, expected):
    assert level_for_score(score) == expected