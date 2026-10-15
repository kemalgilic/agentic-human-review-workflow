"""FastAPI application: a thin HTTP layer over the workflow engine.

All business logic lives in app/services and app/human_review. Handlers only
translate HTTP requests into engine calls and domain errors into status codes.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from app.human_review.queue import ReviewAlreadyDecidedError, ReviewNotFoundError
from app.schemas import (
    AuditEvent,
    ExceptionRecord,
    ReviewDecisionRequest,
    ReviewItem,
    ReviewStatus,
    TaskCreate,
    TaskResponse,
)
from app.services.workflow import (
    TaskValidationError,
    WorkflowEngine,
    WorkflowError,
    get_workflow_engine,
)

APP_NAME = "Agentic Human Review Workflow"
APP_VERSION = "0.1.0"

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "Portfolio demo: risk-based routing, human review, "
        "simulated tool execution and audit trail."
    ),
)

EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]


# --------------------------------------------------------------------------
# Error handling: domain errors -> HTTP status codes
# --------------------------------------------------------------------------


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.exception_handler(ReviewNotFoundError)
async def review_not_found_handler(request: Request, exc: ReviewNotFoundError) -> JSONResponse:
    return _error(404, f"review '{exc}' not found")


@app.exception_handler(ReviewAlreadyDecidedError)
async def review_already_decided_handler(
    request: Request, exc: ReviewAlreadyDecidedError
) -> JSONResponse:
    return _error(409, str(exc))


@app.exception_handler(TaskValidationError)
async def task_validation_handler(request: Request, exc: TaskValidationError) -> JSONResponse:
    return _error(422, str(exc))


@app.exception_handler(WorkflowError)
async def workflow_error_handler(request: Request, exc: WorkflowError) -> JSONResponse:
    # Internal details are deliberately not returned to the client. The failure
    # is recorded in the exception queue and the audit trail.
    return _error(
        500,
        "Internal workflow error. The failure was recorded in the exception "
        "queue and the audit trail.",
    )


# --------------------------------------------------------------------------
# Meta endpoints
# --------------------------------------------------------------------------


@app.get("/")
def root() -> dict:
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------


@app.post("/tasks", response_model=TaskResponse, status_code=201)
def create_task(task_in: TaskCreate, engine: EngineDep) -> TaskResponse:
    """Submit a task. The outcome (executed, pending_review, blocked, failed)
    is reported in the `status` field of the response."""
    return engine.submit(task_in)


# --------------------------------------------------------------------------
# Human review
# --------------------------------------------------------------------------


@app.get("/reviews", response_model=list[ReviewItem])
def list_reviews(
    engine: EngineDep,
    status: Annotated[ReviewStatus | None, Query()] = None,
) -> list[ReviewItem]:
    """List review items, optionally filtered by status."""
    return engine.review_queue.list_items(status)


@app.post("/reviews/{review_id}/approve", response_model=TaskResponse)
def approve_review_item(
    review_id: str,
    engine: EngineDep,
    payload: ReviewDecisionRequest | None = None,
) -> TaskResponse:
    """Approve a pending review. The simulated action is executed."""
    if payload is None:
        payload = ReviewDecisionRequest()
    return engine.approve(review_id, reviewer=payload.reviewer, note=payload.note)


@app.post("/reviews/{review_id}/reject", response_model=TaskResponse)
def reject_review_item(
    review_id: str,
    engine: EngineDep,
    payload: ReviewDecisionRequest | None = None,
) -> TaskResponse:
    """Reject a pending review. No action is executed."""
    if payload is None:
        payload = ReviewDecisionRequest()
    return engine.reject(review_id, reviewer=payload.reviewer, note=payload.note)


# --------------------------------------------------------------------------
# Audit trail and exception queue
# --------------------------------------------------------------------------


@app.get("/audit", response_model=list[AuditEvent])
def get_audit_trail(engine: EngineDep, task_id: str | None = None) -> list[AuditEvent]:
    """Return the audit trail, optionally for a single task."""
    return engine.audit.list_events(task_id)


@app.get("/exceptions", response_model=list[ExceptionRecord])
def list_exceptions(engine: EngineDep, task_id: str | None = None) -> list[ExceptionRecord]:
    """Return failures that were recorded in the exception queue."""
    return engine.exceptions.list_records(task_id)