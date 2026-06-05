"""
FastAPI backend for Retirement RAG.
Run: uvicorn api:app --reload --port 8001
"""

import os
import json
import glob
import shutil
import tempfile
import asyncio
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv
from jose import JWTError, jwt

load_dotenv()

# ── JWT Config ────────────────────────────────────────────────────────────────

JWT_SECRET    = os.environ.get("JWT_SECRET", "retirement-plan-secret-key-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 8

def _load_users() -> dict:
    """
    Load users from USERS_CONFIG env var (JSON) or fall back to defaults.
    Format: {"username": {"password": "...", "role": "plan_admin|plan_consultant"}}
    Set USERS_CONFIG env var with a JSON string to add multiple users.
    """
    config = os.environ.get("USERS_CONFIG")
    if config:
        try:
            return json.loads(config)
        except Exception:
            pass
    return {
        "plan_admin": {
            "password": os.environ.get("PLAN_ADMIN_PASSWORD", "admin123"),
            "role":     "plan_admin",
        },
        "plan_consultant": {
            "password": os.environ.get("PLAN_PARTICIPANT_PASSWORD", "participant123"),
            "role":     "plan_consultant",
        },
    }

USERS = _load_users()

bearer_scheme = HTTPBearer(auto_error=False)

def create_token(role: str) -> str:
    payload = {
        "role": role,
        "exp":  datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> str:
    if not credentials:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload["role"]
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")

def require_admin(role: str = Depends(verify_token)) -> str:
    if role != "plan_admin":
        raise HTTPException(403, "Plan Admin access required")
    return role

from app.registry.registry import (
    delete_plan,
    load_registry,
    create_employer, list_employers, get_employer,
    create_plan, list_plans, get_plan,
    add_document, add_generic_document,
    document_exists, generic_doc_exists,
    compute_file_hash,
    remove_document_from_plan, remove_generic_document,
    find_document, get_registry_summary,
    get_plan_rules,
    DOC_TYPES,
)
from app.ingestion.pipeline import ingest_file
from app.ingestion.classifier import classify_document
from app.llm.agent import run_agent
from app.storage.vector_store import get_collection_stats, delete_doc_chunks, delete_plan_chunks


app = FastAPI(title="Retirement RAG API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://127.0.0.1:3001"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    user = USERS.get(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(401, "Invalid username or password")
    token = create_token(user["role"])
    return {"token": token, "role": user["role"]}

@app.get("/api/auth/me")
async def me(role: str = Depends(verify_token)):
    return {"role": role}


# ── In-memory job tracker ──────────────────────────────────────────────────────
_jobs: dict = {}


# ── ID helpers ─────────────────────────────────────────────────────────────────

def _next_gdoc_id(registry: dict) -> str:
    nums = []
    for d in registry["generic_documents"]:
        try:
            nums.append(int(d["doc_id"].replace("GDOC_", "")))
        except ValueError:
            pass
    return f"GDOC_{str(max(nums, default=0) + 1).zfill(3)}"


def _next_doc_id(registry: dict) -> str:
    nums = []
    for plan in registry["plans"].values():
        for d in plan["documents"]:
            try:
                nums.append(int(d["doc_id"].replace("DOC_", "")))
            except ValueError:
                pass
    return f"DOC_{str(max(nums, default=0) + 1).zfill(3)}"


# ── Background ingestion ───────────────────────────────────────────────────────

def _run_job(job_id: str, tmp_path: str, context: dict,
             filename: str, source_format: str,
             file_hash: str, effective_date: Optional[str]):
    """Background thread: run ingestion, update job status, clean up temp file."""

    def log(msg: str):
        _jobs[job_id]["logs"].append({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "msg": msg,
        })

    _jobs[job_id]["logs"] = []
    log(f"Starting ingestion for {filename}")

    try:
        result = ingest_file(tmp_path, context, log_fn=log)

        # Update chunk_count now that ingestion is complete
        # (doc was pre-registered with chunk_count=0 before this thread started)
        if context["tier"] == "generic":
            add_generic_document(
                doc_id=context["doc_id"],
                filename=filename,
                doc_type=context["doc_type"],
                source_format=source_format,
                file_hash=file_hash,
                chunk_count=result["chunk_count"],
                effective_date=effective_date,
            )
        else:
            add_document(
                plan_id=context["plan_id"],
                doc_id=context["doc_id"],
                filename=filename,
                doc_type=context["doc_type"],
                source_format=source_format,
                file_hash=file_hash,
                chunk_count=result["chunk_count"],
                effective_date=effective_date,
            )

        log(f"✓ Chunk count updated in registry")
        _jobs[job_id].update({
            "status": "done",
            "doc_id": context["doc_id"],
            "chunk_count": result["chunk_count"],
            "stats": result["stats"],
            "filename": filename,
        })
    except Exception as e:
        log(f"✗ Error: {e}")
        _jobs[job_id].update({"status": "error", "message": str(e)})
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Registry endpoints ─────────────────────────────────────────────────────────

@app.get("/api/summary")
async def get_summary(role: str = Depends(verify_token)):
    s = get_registry_summary()
    try:
        stats = await asyncio.to_thread(get_collection_stats)
        s["vector_chunks"] = stats.get("total_chunks", 0)
    except Exception:
        s["vector_chunks"] = 0
    return s


@app.get("/api/employers")
async def get_employers(role: str = Depends(verify_token)):
    return await asyncio.to_thread(list_employers)


@app.get("/api/plans")
async def get_plans(role: str = Depends(verify_token)):
    return await asyncio.to_thread(list_plans)


@app.get("/api/plans/{plan_id}/rules")
async def get_plan_rules_endpoint(plan_id: str, role: str = Depends(verify_token)):
    rules = await asyncio.to_thread(get_plan_rules, plan_id)
    if rules is None:
        raise HTTPException(404, f"No extracted facts found for plan '{plan_id}'")
    return rules


@app.get("/api/registry")
async def get_registry(role: str = Depends(verify_token)):
    registry = load_registry()
    plans_with_employer = []
    for plan in registry["plans"].values():
        employer = registry["employers"].get(plan["employer_id"], {})
        plans_with_employer.append({
            **plan,
            "employer_name": employer.get("employer_name", ""),
        })
    return {
        "employers": list(registry["employers"].values()),
        "plans": plans_with_employer,
        "generic_documents": registry["generic_documents"],
    }


@app.get("/api/doc-types")
async def get_doc_types(role: str = Depends(verify_token)):
    return DOC_TYPES


# ── Create employers / plans ───────────────────────────────────────────────────

class CreateEmployerBody(BaseModel):
    name: str


@app.post("/api/employers")
async def create_new_employer(body: CreateEmployerBody, role: str = Depends(require_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Employer name required")
    return await asyncio.to_thread(create_employer, name)


class CreatePlanBody(BaseModel):
    name: str
    employer_id: str
    effective_date: Optional[str] = None


@app.post("/api/plans")
async def create_new_plan(body: CreatePlanBody, role: str = Depends(require_admin)):
    if not body.name.strip() or not body.employer_id.strip():
        raise HTTPException(400, "name and employer_id required")
    return await asyncio.to_thread(create_plan, body.name, body.employer_id, body.effective_date)


# ── Delete plans / documents ───────────────────────────────────────────────────

@app.delete("/api/plans/{plan_id}")
async def delete_plan_endpoint(plan_id: str, role: str = Depends(require_admin)):
    plan = get_plan(plan_id)
    if not plan:
        raise HTTPException(404, f"Plan '{plan_id}' not found")

    # Delete all doc chunks from vector DB
    await asyncio.to_thread(delete_plan_chunks, plan_id)

    # Delete ALL processed files for this plan (glob catches orphans from still-running threads)
    for f in glob.glob(os.path.join("data", "processed", f"{plan_id}_*.json")):
        os.remove(f)

    # Delete plan facts file
    facts_path = os.path.join("data", "facts", f"{plan_id}_rules.json")
    if os.path.exists(facts_path):
        os.remove(facts_path)

    delete_plan(plan_id)
    return {"status": "deleted", "plan_id": plan_id}


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str, role: str = Depends(require_admin)):
    doc, plan_id = find_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document '{doc_id}' not found")

    await asyncio.to_thread(delete_doc_chunks, doc_id)

    # Delete processed file — glob catches any doc_type suffix variation or orphans
    pattern = f"{plan_id}_{doc_id}_*.json" if plan_id else f"GENERIC_{doc_id}_*.json"
    for f in glob.glob(os.path.join("data", "processed", pattern)):
        os.remove(f)

    if plan_id:
        remove_document_from_plan(plan_id, doc_id)
    else:
        remove_generic_document(doc_id)

    return {"status": "deleted", "doc_id": doc_id}


# ── Doc-type auto-detection ────────────────────────────────────────────────────

@app.post("/api/detect-doc-type")
async def detect_doc_type(file: UploadFile = File(...), role: str = Depends(require_admin)):
    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in (".pdf", ".docx", ".xlsx", ".xls"):
        raise HTTPException(400, "Unsupported file format")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        doc_type, input_tokens, output_tokens = await asyncio.to_thread(
            classify_document, tmp_path, suffix
        )
        return {"doc_type": doc_type, "input_tokens": input_tokens, "output_tokens": output_tokens}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Ingestion ──────────────────────────────────────────────────────────────────

@app.post("/api/ingest")
async def ingest(
    file: UploadFile = File(...),
    tier: str = Form(...),
    doc_type: Optional[str] = Form(None),
    plan_id: Optional[str] = Form(None),
    effective_date: Optional[str] = Form(None),
    role: str = Depends(require_admin),
):
    if tier not in ("plan_doc", "generic"):
        raise HTTPException(400, "tier must be 'plan_doc' or 'generic'")
    if tier == "plan_doc" and not plan_id:
        raise HTTPException(400, "plan_id required for plan_doc tier")

    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in (".pdf", ".docx", ".xlsx", ".xls"):
        raise HTTPException(400, "Unsupported file format. Use PDF, DOCX, or XLSX.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    # Auto-detect doc_type if not supplied
    if not doc_type or doc_type not in DOC_TYPES:
        detected, _, _ = await asyncio.to_thread(classify_document, tmp_path, suffix)
        doc_type = detected

    try:
        file_hash = compute_file_hash(tmp_path)
        registry = load_registry()

        if tier == "generic":
            if generic_doc_exists(file_hash):
                return {"status": "duplicate", "message": "Already ingested as a generic document."}
            doc_id = _next_gdoc_id(registry)
            context = {
                "tier": "generic",
                "doc_id": doc_id,
                "doc_type": doc_type,
                "plan_id": None,
                "plan_name": None,
                "employer_id": None,
                "employer_name": None,
                "effective_date": effective_date or None,
            }
        else:
            plan = get_plan(plan_id)
            if not plan:
                raise HTTPException(404, f"Plan '{plan_id}' not found")
            if document_exists(plan_id, file_hash):
                return {"status": "duplicate", "message": "Already ingested for this plan."}
            doc_id = _next_doc_id(registry)
            employer = get_employer(plan["employer_id"]) or {}
            context = {
                "tier": "plan_doc",
                "doc_id": doc_id,
                "doc_type": doc_type,
                "plan_id": plan_id,
                "plan_name": plan["plan_name"],
                "employer_id": plan["employer_id"],
                "employer_name": employer.get("employer_name", ""),
                "effective_date": effective_date or None,
            }

        # Register the document in the registry BEFORE starting the background
        # thread — so it's visible immediately and can't be lost if threads race.
        if tier == "generic":
            add_generic_document(
                doc_id=doc_id,
                filename=file.filename,
                doc_type=doc_type,
                source_format=suffix.lstrip("."),
                file_hash=file_hash,
                chunk_count=0,
                effective_date=effective_date or None,
            )
        else:
            add_document(
                plan_id=plan_id,
                doc_id=doc_id,
                filename=file.filename,
                doc_type=doc_type,
                source_format=suffix.lstrip("."),
                file_hash=file_hash,
                chunk_count=0,
                effective_date=effective_date or None,
            )

        job_id = uuid.uuid4().hex[:8]
        _jobs[job_id] = {"status": "processing", "filename": file.filename, "logs": []}

        threading.Thread(
            target=_run_job,
            args=(job_id, tmp_path, context, file.filename,
                  suffix.lstrip("."), file_hash, effective_date or None),
            daemon=True,
        ).start()
        tmp_path = None  # thread now owns the temp file

        return {
            "status": "processing",
            "job_id": job_id,
            "filename": file.filename,
            "doc_id": doc_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@app.get("/api/ingest/status/{job_id}")
async def ingest_status(job_id: str, role: str = Depends(verify_token)):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' not found")
    return job


# ── Query ──────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    scope: Optional[str] = None


@app.post("/api/query")
async def query(req: QueryRequest, role: str = Depends(verify_token)):
    if not req.question.strip():
        raise HTTPException(400, "Question cannot be empty")
    try:
        result = await asyncio.to_thread(
            run_agent,
            req.question,
            req.plan_id or None,
            req.plan_name or None,
        )
        result.setdefault("input_tokens", 0)
        result.setdefault("output_tokens", 0)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Serve built frontend (production) ─────────────────────────────────────────

_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")

if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        return FileResponse(os.path.join(_DIST, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8001, reload=True)
