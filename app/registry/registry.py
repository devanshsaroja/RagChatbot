import json
import os
import hashlib
from datetime import date


# ── Constants ─────────────────────────────────────────────────────────────────

REGISTRY_PATH = os.path.join("data", "registry.json")

DOC_TYPES = [
    "SPD",
    "Adoption Agreement",
    "Plan Amendment",
    "IRS Determination Letter",
    "Form 5500",
    "IRS Publication",
    "ERISA Regulation",
    "Other"
]


# ── Registry Load / Save ──────────────────────────────────────────────────────

def load_registry() -> dict:
    """
    Load registry from disk.
    Creates a fresh empty registry if file doesn't exist.
    """
    if not os.path.exists(REGISTRY_PATH):
        return _empty_registry()

    with open(REGISTRY_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_registry(registry: dict):
    """Save registry to disk."""
    registry["meta"]["last_updated"] = str(date.today())
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    with open(REGISTRY_PATH, 'w', encoding='utf-8') as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


def _empty_registry() -> dict:
    """Create a fresh empty registry structure."""
    return {
        "meta": {
            "version": "1.0",
            "created_at": str(date.today()),
            "last_updated": str(date.today())
        },
        "employers": {},
        "plans": {},
        "generic_documents": []
    }


# ── ID Generation ─────────────────────────────────────────────────────────────

def _next_id(existing_ids: list[str], prefix: str) -> str:
    """
    Generate next sequential ID.
    Example: ["EMP_001", "EMP_002"] → "EMP_003"
    """
    max_num = 0
    for id_str in existing_ids:
        try:
            num = int(id_str.replace(prefix + "_", ""))
            if num > max_num:
                max_num = num
        except ValueError:
            continue
    return f"{prefix}_{str(max_num + 1).zfill(3)}"


# ── Employer Operations ───────────────────────────────────────────────────────

def create_employer(employer_name: str) -> dict:
    """
    Create a new employer and save to registry.
    Returns the created employer dict.
    """
    registry = load_registry()

    employer_id = _next_id(
        list(registry["employers"].keys()), "EMP"
    )

    employer = {
        "employer_id": employer_id,
        "employer_name": employer_name,
        "created_at": str(date.today())
    }

    registry["employers"][employer_id] = employer
    save_registry(registry)
    return employer


def find_employer_by_name(employer_name: str) -> dict | None:
    """
    Find employer by name (case-insensitive).
    Returns employer dict or None if not found.
    """
    registry = load_registry()
    name_lower = employer_name.strip().lower()

    for employer in registry["employers"].values():
        if employer["employer_name"].lower() == name_lower:
            return employer

    return None


def get_employer(employer_id: str) -> dict | None:
    """Get employer by ID."""
    registry = load_registry()
    return registry["employers"].get(employer_id)


def list_employers() -> list[dict]:
    """Return list of all employers."""
    registry = load_registry()
    return list(registry["employers"].values())


# ── Plan Operations ───────────────────────────────────────────────────────────

def create_plan(
    plan_name: str,
    employer_id: str,
    effective_date: str | None = None
) -> dict:
    """
    Create a new plan and save to registry.
    Returns the created plan dict.
    """
    registry = load_registry()

    plan_id = _next_id(list(registry["plans"].keys()), "PLAN")

    plan = {
        "plan_id": plan_id,
        "plan_name": plan_name,
        "employer_id": employer_id,
        "effective_date": effective_date,
        "created_at": str(date.today()),
        "documents": []
    }

    registry["plans"][plan_id] = plan
    save_registry(registry)
    return plan


def get_plan(plan_id: str) -> dict | None:
    """Get plan by ID."""
    registry = load_registry()
    return registry["plans"].get(plan_id)


def find_plans_by_employer(employer_id: str) -> list[dict]:
    """Get all plans for a given employer."""
    registry = load_registry()
    return [
        p for p in registry["plans"].values()
        if p["employer_id"] == employer_id
    ]


def list_plans() -> list[dict]:
    """
    Return list of all plans with employer name attached.
    Used by ingest.py for display.
    """
    registry = load_registry()
    result = []

    for plan in registry["plans"].values():
        employer = registry["employers"].get(plan["employer_id"], {})
        result.append({
            **plan,
            "employer_name": employer.get("employer_name", "Unknown")
        })

    return result


# ── Document Operations ───────────────────────────────────────────────────────

def compute_file_hash(file_path: str) -> str:
    """
    Compute SHA256 hash of a file.
    Used to detect duplicate uploads.
    """
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def document_exists(plan_id: str, file_hash: str) -> bool:
    """
    Check if a document with this hash already exists in this plan.
    Scenario 3 duplicate detection.
    """
    registry = load_registry()
    plan = registry["plans"].get(plan_id)
    if not plan:
        return False

    for doc in plan["documents"]:
        if doc["file_hash"] == file_hash:
            return True

    return False


def add_document(
    plan_id: str,
    filename: str,
    doc_type: str,
    source_format: str,
    file_hash: str,
    chunk_count: int,
    effective_date: str | None = None
) -> dict:
    """
    Add or update a document in a plan's document list.
    If doc with same hash exists — updates chunk_count and ingested_at.
    Returns the created/updated document dict.
    """
    registry = load_registry()
    plan = registry["plans"].get(plan_id)

    if not plan:
        raise ValueError(f"Plan not found: {plan_id}")

    # Check if document already exists by hash — update if so
    for existing_doc in plan["documents"]:
        if existing_doc["file_hash"] == file_hash:
            existing_doc["chunk_count"] = chunk_count
            existing_doc["ingested_at"] = str(date.today())
            save_registry(registry)
            return existing_doc

    # New document — generate new doc_id
    all_doc_ids = [
        doc["doc_id"]
        for p in registry["plans"].values()
        for doc in p["documents"]
    ]
    doc_id = _next_id(all_doc_ids, "DOC")

    document = {
        "doc_id": doc_id,
        "filename": filename,
        "doc_type": doc_type,
        "tier": "plan_doc",
        "source_format": source_format,
        "file_hash": file_hash,
        "effective_date": effective_date,
        "ingested_at": str(date.today()),
        "chunk_count": chunk_count
    }

    registry["plans"][plan_id]["documents"].append(document)
    save_registry(registry)
    return document

# ── Generic Document Operations ───────────────────────────────────────────────

def generic_doc_exists(file_hash: str) -> bool:
    """
    Check if a generic document with this hash already exists.
    """
    registry = load_registry()
    for doc in registry["generic_documents"]:
        if doc["file_hash"] == file_hash:
            return True
    return False


def add_generic_document(
    filename: str,
    doc_type: str,
    source_format: str,
    file_hash: str,
    chunk_count: int,
    effective_date: str | None = None
) -> dict:
    """
    Add or update a generic document in the registry.
    If doc with same hash exists — updates chunk_count and ingested_at.
    Returns the created/updated document dict.
    """
    registry = load_registry()

    # Check if document already exists by hash — update if so
    for existing_doc in registry["generic_documents"]:
        if existing_doc["file_hash"] == file_hash:
            existing_doc["chunk_count"] = chunk_count
            existing_doc["ingested_at"] = str(date.today())
            save_registry(registry)
            return existing_doc

    # New document — generate new doc_id
    existing_ids = [d["doc_id"] for d in registry["generic_documents"]]
    doc_id = _next_id(existing_ids, "GDOC")

    document = {
        "doc_id": doc_id,
        "filename": filename,
        "doc_type": doc_type,
        "tier": "generic",
        "source_format": source_format,
        "file_hash": file_hash,
        "effective_date": effective_date,
        "ingested_at": str(date.today()),
        "chunk_count": chunk_count
    }

    registry["generic_documents"].append(document)
    save_registry(registry)
    return document

def list_generic_documents() -> list[dict]:
    """Return list of all generic documents."""
    registry = load_registry()
    return registry["generic_documents"]


# ── Summary ───────────────────────────────────────────────────────────────────

def get_registry_summary() -> dict:
    """
    Return high level summary of registry contents.
    Used by ingest.py for display.
    """
    registry = load_registry()
    total_docs = sum(
        len(p["documents"])
        for p in registry["plans"].values()
    )
    total_chunks = sum(
        doc["chunk_count"]
        for p in registry["plans"].values()
        for doc in p["documents"]
    ) + sum(
        doc["chunk_count"]
        for doc in registry["generic_documents"]
    )

    return {
        "employers": len(registry["employers"]),
        "plans": len(registry["plans"]),
        "plan_documents": total_docs,
        "generic_documents": len(registry["generic_documents"]),
        "total_chunks": total_chunks
    }


# ── Remove Operations ─────────────────────────────────────────────────────────

def remove_document_from_plan(plan_id: str, doc_id: str) -> bool:
    """
    Remove a document from a plan in the registry.
    Also removes the plan if it has no documents left.
    Also removes the employer if it has no plans left.
    Returns True if removed, False if not found.
    """
    registry = load_registry()

    plan = registry["plans"].get(plan_id)
    if not plan:
        return False

    # Find and remove document
    original_count = len(plan["documents"])
    plan["documents"] = [
        d for d in plan["documents"]
        if d["doc_id"] != doc_id
    ]

    if len(plan["documents"]) == original_count:
        return False  # Document not found

    # If plan has no documents left — remove plan
    if not plan["documents"]:
        employer_id = plan["employer_id"]
        del registry["plans"][plan_id]

        # If employer has no plans left — remove employer
        employer_has_plans = any(
            p["employer_id"] == employer_id
            for p in registry["plans"].values()
        )
        if not employer_has_plans:
            registry["employers"].pop(employer_id, None)

    save_registry(registry)
    return True


def remove_generic_document(doc_id: str) -> bool:
    """
    Remove a generic document from the registry.
    Returns True if removed, False if not found.
    """
    registry = load_registry()
    original_count = len(registry["generic_documents"])
    registry["generic_documents"] = [
        d for d in registry["generic_documents"]
        if d["doc_id"] != doc_id
    ]

    if len(registry["generic_documents"]) == original_count:
        return False

    save_registry(registry)
    return True


def find_document(doc_id: str) -> tuple[dict | None, str]:
    """
    Find a document by doc_id across all plans and generic docs.
    Returns (document_dict, plan_id) or (None, None) if not found.
    plan_id is None for generic documents.
    """
    registry = load_registry()

    # Search in plans
    for plan_id, plan in registry["plans"].items():
        for doc in plan["documents"]:
            if doc["doc_id"] == doc_id:
                return doc, plan_id

    # Search in generic docs
    for doc in registry["generic_documents"]:
        if doc["doc_id"] == doc_id:
            return doc, None

    return None, None

# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing registry...\n")

    # Clean start — remove existing registry for test
    if os.path.exists(REGISTRY_PATH):
        os.remove(REGISTRY_PATH)
        print("Removed existing registry for clean test\n")

    # Create employer
    emp = create_employer("Microsoft Corporation")
    print(f"Created employer: {emp['employer_id']} — {emp['employer_name']}")

    # Find employer by name
    found = find_employer_by_name("microsoft corporation")
    print(f"Found employer by name: {found['employer_id']}")

    # Create plan
    plan = create_plan(
        plan_name="Microsoft Savings Plus 401(k) Plan",
        employer_id=emp["employer_id"],
        effective_date="2025-01-01"
    )
    print(f"\nCreated plan: {plan['plan_id']} — {plan['plan_name']}")

    # Add document
    file_hash = compute_file_hash("data/raw/Microsoft_401k_SPD.pdf")
    doc = add_document(
        plan_id=plan["plan_id"],
        filename="Microsoft_401k_SPD.pdf",
        doc_type="SPD",
        source_format="pdf",
        file_hash=file_hash,
        chunk_count=183,
        effective_date="2025-01-01"
    )
    print(f"\nAdded document: {doc['doc_id']} — {doc['filename']}")

    # Test duplicate detection
    is_dup = document_exists(plan["plan_id"], file_hash)
    print(f"Duplicate detection: {is_dup}")

    # Add generic document
    gdoc = add_generic_document(
        filename="IRS_Publication_560.pdf",
        doc_type="IRS Publication",
        source_format="pdf",
        file_hash="fakehash123",
        chunk_count=89
    )
    print(f"\nAdded generic doc: {gdoc['doc_id']} — {gdoc['filename']}")

    # Summary
    summary = get_registry_summary()
    print(f"\nRegistry summary: {summary}")

    # List plans
    plans = list_plans()
    print(f"\nAll plans:")
    for p in plans:
        print(f"  {p['plan_id']} | {p['plan_name']} | {p['employer_name']}")

    print("\nRegistry test complete.")
    print(f"Check: {REGISTRY_PATH}")