import pytest
from fastapi.testclient import TestClient

from app.human_review.queue import ReviewQueue
from app.main import app
from app.services.audit import AuditLog
from app.services.exceptions import ExceptionQueue
from app.services.workflow import WorkflowEngine, get_workflow_engine


class CrashingProvider:
    def decide(self, task, risk):
        raise RuntimeError("provider down")


def make_client(provider=None) -> TestClient:
    """TestClient with an isolated engine: no state shared between tests."""
    engine = WorkflowEngine(
        audit=AuditLog(),
        review_queue=ReviewQueue(),
        exceptions=ExceptionQueue(),
        provider=provider,
        base_delay=0.0,
        sleep=lambda seconds: None,
    )
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    return make_client()


def post_task(
    client: TestClient,
    action: str = "read_record",
    user_input: str = "Please handle this demo request",
    **context,
):
    return client.post(
        "/tasks",
        json={
            "user_input": user_input,
            "requested_action": action,
            "target_system": "demo_system",
            "context": context,
        },
    )


def post_medium(client: TestClient, **extra):
    return post_task(client, "send_email", external_recipient=True, **extra)


def event_names(client: TestClient, task_id: str | None = None) -> list[str]:
    params = {"task_id": task_id} if task_id else None
    return [e["event"] for e in client.get("/audit", params=params).json()]


# --------------------------------------------------------------------------
# POST /tasks: the three routes
# --------------------------------------------------------------------------


def test_low_risk_allow_via_api(client):
    response = post_task(client, "read_record")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "executed"
    assert body["decision"]["decision"] == "allow"
    assert body["risk"]["risk_level"] == "low"
    assert body["review_id"] is None
    assert body["execution"]["simulated"] is True
    assert event_names(client, body["task_id"]) == [
        "task_received",
        "risk_assessed",
        "decision_created",
        "action_executed",
    ]


def test_medium_risk_goes_to_review_via_api(client):
    response = post_medium(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_review"
    assert body["decision"]["decision"] == "review"
    assert body["risk"]["risk_level"] == "medium"
    assert body["review_id"].startswith("rev_")
    assert body["execution"] is None
    assert event_names(client, body["task_id"])[-1] == "review_required"


def test_high_risk_is_blocked_via_api(client):
    response = post_task(client, "delete_record")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "blocked"
    assert body["decision"]["decision"] == "deny"
    assert body["risk"]["risk_level"] == "high"
    assert body["review_id"] is None
    assert body["execution"] is None
    assert event_names(client, body["task_id"])[-1] == "action_blocked"
    assert client.get("/reviews").json() == []


# --------------------------------------------------------------------------
# GET /reviews
# --------------------------------------------------------------------------


def test_reviews_list_and_status_filter(client):
    first = post_medium(client).json()
    second = post_medium(client).json()
    client.post(f"/reviews/{first['review_id']}/approve")

    assert len(client.get("/reviews").json()) == 2
    pending = client.get("/reviews", params={"status": "pending"}).json()
    assert [i["review_id"] for i in pending] == [second["review_id"]]
    approved = client.get("/reviews", params={"status": "approved"}).json()
    assert [i["review_id"] for i in approved] == [first["review_id"]]
    assert client.get("/reviews", params={"status": "rejected"}).json() == []


def test_review_item_shape(client):
    body = post_medium(client).json()
    item = client.get("/reviews").json()[0]
    assert set(item) == {
        "review_id",
        "task",
        "decision",
        "risk",
        "status",
        "created_at",
        "reviewer",
        "reviewer_note",
        "reviewed_at",
    }
    assert item["status"] == "pending"
    assert item["task"]["task_id"] == body["task_id"]
    assert item["reviewer"] is None


def test_invalid_status_filter_returns_422(client):
    assert client.get("/reviews", params={"status": "bogus"}).status_code == 422


# --------------------------------------------------------------------------
# Approve and reject
# --------------------------------------------------------------------------


def test_approve_executes_action(client):
    pending = post_medium(client).json()
    response = client.post(f"/reviews/{pending['review_id']}/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["task_id"] == pending["task_id"]
    assert body["execution"]["simulated"] is True


def test_approve_records_reviewer_and_note(client):
    pending = post_medium(client).json()
    response = client.post(
        f"/reviews/{pending['review_id']}/approve",
        json={"reviewer": "alice", "note": "looks fine"},
    )
    assert response.status_code == 200

    item = client.get("/reviews", params={"status": "approved"}).json()[0]
    assert item["reviewer"] == "alice"
    assert item["reviewer_note"] == "looks fine"
    assert item["reviewed_at"] is not None

    approved_event = [
        e
        for e in client.get("/audit", params={"task_id": pending["task_id"]}).json()
        if e["event"] == "review_approved"
    ][0]
    assert approved_event["actor"] == "alice"


def test_approve_without_body_uses_default_reviewer(client):
    pending = post_medium(client).json()
    assert client.post(f"/reviews/{pending['review_id']}/approve").status_code == 200
    item = client.get("/reviews", params={"status": "approved"}).json()[0]
    assert item["reviewer"] == "human_reviewer"
    assert item["reviewer_note"] is None


def test_reject_blocks_action(client):
    pending = post_medium(client).json()
    response = client.post(
        f"/reviews/{pending['review_id']}/reject",
        json={"reviewer": "bob", "note": "not allowed"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["execution"] is None
    assert body["review_id"] == pending["review_id"]

    names = event_names(client, pending["task_id"])
    assert names[-2:] == ["review_rejected", "action_blocked"]
    assert "action_executed" not in names


def test_double_approve_returns_409(client):
    pending = post_medium(client).json()
    assert client.post(f"/reviews/{pending['review_id']}/approve").status_code == 200

    second = client.post(f"/reviews/{pending['review_id']}/approve")
    assert second.status_code == 409
    assert "already approved" in second.json()["detail"]
    assert event_names(client, pending["task_id"]).count("action_executed") == 1


def test_reject_after_approve_returns_409(client):
    pending = post_medium(client).json()
    client.post(f"/reviews/{pending['review_id']}/approve")
    assert client.post(f"/reviews/{pending['review_id']}/reject").status_code == 409


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_unknown_review_returns_404(client, action):
    response = client.post(f"/reviews/rev_missing/{action}")
    assert response.status_code == 404
    assert "rev_missing" in response.json()["detail"]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {
            "user_input": "hi there friend",
            "requested_action": "launch_rocket",
            "target_system": "mail",
        },
        {"user_input": "hi there friend", "requested_action": "read_record"},
        {"user_input": "", "requested_action": "read_record", "target_system": "mail"},
    ],
)
def test_invalid_task_body_returns_422(client, payload):
    response = client.post("/tasks", json=payload)
    assert response.status_code == 422
    assert client.get("/audit").json() == []


def test_blank_input_returns_422_and_is_audited(client):
    response = post_task(client, "read_record", user_input="   ")
    assert response.status_code == 422
    assert "blank" in response.json()["detail"]

    events = client.get("/audit").json()
    assert [e["event"] for e in events] == ["task_received", "exception"]
    assert events[1]["details"]["stage"] == "context_validation"


# --------------------------------------------------------------------------
# GET /audit
# --------------------------------------------------------------------------


def test_audit_trail_in_order(client):
    pending = post_medium(client).json()
    client.post(f"/reviews/{pending['review_id']}/approve")
    assert event_names(client) == [
        "task_received",
        "risk_assessed",
        "decision_created",
        "review_required",
        "review_approved",
        "action_executed",
    ]


def test_audit_filter_by_task(client):
    executed = post_task(client, "read_record").json()
    blocked = post_task(client, "delete_record").json()

    assert len(client.get("/audit").json()) == 8
    assert "action_executed" in event_names(client, executed["task_id"])
    assert "action_blocked" not in event_names(client, executed["task_id"])
    assert "action_blocked" in event_names(client, blocked["task_id"])
    assert client.get("/audit", params={"task_id": "task_unknown"}).json() == []


def test_audit_event_shape(client):
    post_task(client, "read_record")
    event = client.get("/audit").json()[0]
    assert set(event) == {"timestamp", "task_id", "event", "decision", "actor", "details"}
    assert event["event"] == "task_received"
    assert event["actor"] == "api"


# --------------------------------------------------------------------------
# Failures are never silent
# --------------------------------------------------------------------------


def test_failed_execution_is_visible(client):
    response = post_task(client, "read_record", simulate_permanent_failure=True)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["execution"] is None

    records = client.get("/exceptions").json()
    assert len(records) == 1
    assert records[0]["task_id"] == body["task_id"]
    assert records[0]["exception_id"] in body["message"]
    assert event_names(client, body["task_id"])[-1] == "exception"
    assert client.get("/exceptions", params={"task_id": "task_unknown"}).json() == []


def test_retry_is_visible_in_audit(client):
    response = post_task(client, "read_record", simulate_transient_failures=1)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "executed"
    assert "retry_attempted" in event_names(client, body["task_id"])
    assert client.get("/exceptions").json() == []


def test_workflow_crash_returns_500_without_leaking_details():
    client = make_client(provider=CrashingProvider())
    response = post_task(client, "read_record")

    assert response.status_code == 500
    assert "provider down" not in response.text

    records = client.get("/exceptions").json()
    assert len(records) == 1
    assert records[0]["stage"] == "risk_and_decision"
    assert event_names(client)[-1] == "exception"


def test_exceptions_endpoint_empty_by_default(client):
    assert client.get("/exceptions").json() == []