import json
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.db import Db, get_db
from app.core.errors import AppError, ErrorCode
from app.core.llm import LLMClient, deadline_in, get_llm
from app.core.ratelimit import KIND_ANALYSIS
from app.services.analysis.result import generate, snapshot_for
from app.services.report import alternative_comparison, evidence_excerpts, evidence_labels, evidence_links, report_result
from app.services.s2.privacy import consent_required

router = APIRouter(prefix="/api/analyses", tags=["analyses"])
_TABLE = "analysis_runs"
# Allow the model deadline and database writes to finish before reclaiming a run.
_STALE_AFTER = timedelta(seconds=150)


class AnalyzeRequest(BaseModel):
    career_input_id: UUID


def _response(row: dict) -> dict:
    return {"analysis_id": row["id"], "status": row["status"], "result": row["result_json"]}


def _stale(row: dict, now: datetime) -> bool:
    return row["status"] == "running" and now - datetime.fromisoformat(row["started_at"]) >= _STALE_AFTER


def _claim(db: Db, run_id: str, initial: dict, model: str) -> tuple[dict, bool]:
    now = datetime.now(timezone.utc)
    row = db.select_one(_TABLE, filters={"id": run_id})
    if row is None:
        try:
            return db.insert(_TABLE, {"id": run_id, "status": "running", "started_at": now.isoformat(), **initial})[0], True
        except AppError as exc:
            if exc.code not in {ErrorCode.VALIDATION_ERROR, ErrorCode.INTERNAL_ERROR}:
                raise
            row = db.select_one(_TABLE, filters={"id": run_id})
            if row is None:
                raise
            # Another request claimed the same snapshot, or the insert result was ambiguous.
            return row, False

    if row["status"] == "failed" or _stale(row, now):
        claimed = db.update(
            _TABLE,
            {"status": "running", "started_at": now.isoformat(), "finished_at": None,
             "result_json": None, "error_code": None, "attempts": 0, "model": model},
            filters={"id": run_id, "status": row["status"], "started_at": row["started_at"]},
        )
        if claimed:
            return claimed[0], True
        row = db.select_one(_TABLE, filters={"id": run_id})
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND)
    return row, False


@router.post("")
def create_analysis(
    payload: AnalyzeRequest,
    request: Request,
    user_id: str = Depends(consent_required),
    db: Db = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
):
    input_id = str(payload.career_input_id)
    career_input = db.select_one("career_inputs", filters={"id": input_id})
    if career_input is None:
        raise AppError(ErrorCode.NOT_FOUND)
    profile = db.select_one("resume_profiles", filters={"id": career_input["resume_profile_id"], "status": "confirmed"})
    if profile is None:
        raise AppError(ErrorCode.NOT_FOUND)

    snapshot = snapshot_for(profile["parsed_json"], career_input)
    settings = request.app.state.settings
    model = settings.gemini_model if settings.llm_provider == "gemini" else settings.claude_model
    # A primary key claims one run atomically across workers, even without a new migration.
    # Changed input or generation rules get a new ID. Existing reports keep their snapshots.
    content = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    run_id = str(uuid5(NAMESPACE_URL, f"career-analysis/v0.1-career-discovery-v2/{user_id}/{input_id}/{content}"))
    initial = {"career_input_id": input_id, "input_snapshot": snapshot, "model": model, "schema_version": "0.1"}
    row, claimed = _claim(db, run_id, initial, model)
    if not claimed:
        return _response(row)

    attempts = 0
    filters = {"id": run_id, "status": "running", "started_at": row["started_at"]}

    def count_attempt():
        nonlocal attempts
        attempts += 1

    try:
        request.app.state.rate_limiter.consume(user_id, KIND_ANALYSIS)
        result = generate(llm, snapshot, deadline_in(90), count_attempt)
    except Exception as exc:
        code = exc.code if isinstance(exc, AppError) else ErrorCode.INTERNAL_ERROR
        db.update(_TABLE, {"status": "failed", "error_code": code.value, "attempts": attempts,
                           "finished_at": datetime.now(timezone.utc).isoformat()}, filters=filters)
        if isinstance(exc, AppError):
            raise
        raise AppError(ErrorCode.INTERNAL_ERROR) from None

    completed = db.update(
        _TABLE,
        {"status": "succeeded", "result_json": result, "attempts": attempts,
         "finished_at": datetime.now(timezone.utc).isoformat()},
        filters=filters,
    )
    if not completed:
        raise AppError(ErrorCode.NOT_FOUND)
    return _response(completed[0])


@router.get("")
def list_analyses(db: Db = Depends(get_db)):
    rows = db.select(
        _TABLE,
        columns="id,started_at,status,career_input_id,result_json",
        order="started_at.desc",
    )
    return [
        {
            "analysis_id": row["id"],
            "created_at": row["started_at"],
            "status": row["status"],
            "career_input_id": row.get("career_input_id"),
            "directions": [
                direction["role_name"]
                for direction in ((row.get("result_json") or {}).get("directions") or [])
            ] if row["status"] == "succeeded" else [],
        }
        for row in rows
    ]


@router.get("/{analysis_id}")
def get_analysis(analysis_id: UUID, db: Db = Depends(get_db)):
    row = db.select_one(_TABLE, filters={"id": str(analysis_id)})
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND)
    result = row["result_json"] or {}
    snapshot = row.get("input_snapshot") or {}
    labels = evidence_labels(result, snapshot)
    presented = report_result(row["result_json"], snapshot)
    return {
        **_response(row),
        "result": presented,
        "alternative_comparison": alternative_comparison(presented or {}, snapshot),
        "created_at": row["started_at"],
        "career_input_id": row.get("career_input_id"),
        "evidence_labels": labels,
        "evidence_excerpts": evidence_excerpts(labels, snapshot),
        "evidence_links": evidence_links(labels, snapshot),
    }
