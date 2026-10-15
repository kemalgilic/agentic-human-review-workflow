"""Pydantic schemas for the Agentic Human Review Workflow (portfolio demo)."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class RequestedAction(StrEnum):
    SEND_EMAIL = "send_email"
    UPDATE_CRM = "update_crm"
    CREATE_TICKET = "create_ticket"
    READ_RECORD = "read_record"
    DELETE_RECORD = "delete_record"


class DecisionType(StrEnum):
    ALLOW = "allow"
    REVIEW = "review"
    DENY = "deny"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class WorkflowStatus(StrEnum):
    EXECUTED = "executed"
    PENDING_REVIEW = "pending_review"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    FAILED = "failed"


class AuditEventType(StrEnum):
    TASK_RECEIVED = "task_received"
    DECISION_CREATED = "decision_created"
    RISK_ASSESSED = "risk_assessed"
    REVIEW_REQUIRED = "review_required"
    REVIEW_APPROVED = "review_approved"
    REVIEW_REJECTED = "review_rejected"
    ACTION_EXECUTED = "action_executed"
    ACTION_BLOCKED = "action_blocked"
    RETRY_ATTEMPTED = "retry_attempted"
    EXCEPTION = "exception"


# --------------------------------------------------------------------------
# Task
# --------------------------------------------------------------------------


class TaskContext(BaseModel):
    """Generic demo context signals. Extra keys are allowed."""

    model_config = ConfigDict(extra="allow")

    data_classification: Literal["internal", "sensitive"] = "internal"
    external_recipient: bool = False
    destructive: bool = False
    privileged_operation: bool = False
    impact: Literal["low", "medium", "high"] = "low"
    notes: str | None = None


class TaskCreate(BaseModel):
    """Request body for POST /tasks."""

    user_input: str = Field(min_length=1, max_length=2000)
    requested_action: RequestedAction
    target_system: str = Field(min_length=1, max_length=100)
    context: TaskContext = Field(default_factory=TaskContext)


class Task(TaskCreate):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    created_at: datetime = Field(default_factory=utc_now)


# --------------------------------------------------------------------------
# Decision and risk
# --------------------------------------------------------------------------


class AIDecision(BaseModel):
    decision: DecisionType
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    recommended_action: str


class RiskAssessment(BaseModel):
    risk_level: RiskLevel
    risk_score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Human review
# --------------------------------------------------------------------------


class ReviewItem(BaseModel):
    review_id: str = Field(default_factory=lambda: new_id("rev"))
    task: Task
    decision: AIDecision
    risk: RiskAssessment
    status: ReviewStatus = ReviewStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    reviewer: str | None = None
    reviewer_note: str | None = None
    reviewed_at: datetime | None = None


class ReviewDecisionRequest(BaseModel):
    """Request body for approve / reject."""

    reviewer: str = Field(default="human_reviewer", min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


# --------------------------------------------------------------------------
# Execution, audit, exceptions
# --------------------------------------------------------------------------


class ExecutionResult(BaseModel):
    success: bool
    action: RequestedAction
    target_system: str
    simulated: bool = True
    message: str
    output: dict[str, Any] = Field(default_factory=dict)
    executed_at: datetime = Field(default_factory=utc_now)


class AuditEvent(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    task_id: str
    event: AuditEventType
    decision: DecisionType | None = None
    actor: str
    details: dict[str, Any] = Field(default_factory=dict)


class ExceptionRecord(BaseModel):
    exception_id: str = Field(default_factory=lambda: new_id("exc"))
    task_id: str
    stage: str
    error: str
    attempts: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)


# --------------------------------------------------------------------------
# API response for POST /tasks
# --------------------------------------------------------------------------


class TaskResponse(BaseModel):
    task_id: str
    status: WorkflowStatus
    decision: AIDecision
    risk: RiskAssessment
    review_id: str | None = None
    execution: ExecutionResult | None = None
    message: str