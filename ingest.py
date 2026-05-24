import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.registry.registry import (
    load_registry,
    create_employer,
    find_employer_by_name,
    create_plan,
    list_plans,
    get_plan,
    compute_file_hash,
    document_exists,
    generic_doc_exists,
    add_document,
    add_generic_document,
    get_registry_summary,
    DOC_TYPES
)
from app.ingestion.pipeline import ingest_file


# ── Display Helpers ───────────────────────────────────────────────────────────

def print_header():
    print("\n" + "═" * 52)
    print("   Retirement Plan RAG — Document Ingestion")
    print("═" * 52)


def print_section(title: str):
    print(f"\n── {title} {'─' * (46 - len(title))}")


def print_success(msg: str):
    print(f"  ✓ {msg}")


def print_warning(msg: str):
    print(f"  ⚠ {msg}")


def print_error(msg: str):
    print(f"  ✗ {msg}")


# ── Input Helpers ─────────────────────────────────────────────────────────────

def ask(prompt: str, required: bool = True) -> str:
    """Ask user for input. Loops until non-empty if required."""
    while True:
        value = input(f"\n  {prompt}: ").strip()
        if value:
            return value
        if not required:
            return ""
        print("  This field is required.")


def ask_optional(prompt: str) -> str | None:
    """Ask for optional input. Returns None if skipped."""
    value = input(f"\n  {prompt} (press Enter to skip): ").strip()
    return value if value else None


def ask_choice(prompt: str, options: list[str]) -> int:
    """
    Show numbered options and get user choice.
    Returns 0-based index.
    """
    print(f"\n  {prompt}")
    for i, option in enumerate(options, start=1):
        print(f"    [{i}] {option}")

    while True:
        try:
            choice = input("\n  Enter number: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return idx
            print(f"  Please enter a number between 1 and {len(options)}")
        except ValueError:
            print("  Please enter a valid number.")


def ask_confirm(prompt: str) -> bool:
    """Ask yes/no confirmation."""
    while True:
        value = input(f"\n  {prompt} [y/n]: ").strip().lower()
        if value in ['y', 'yes']:
            return True
        if value in ['n', 'no']:
            return False
        print("  Please enter y or n.")


# ── Step 1: Get File ──────────────────────────────────────────────────────────

def step_get_file() -> tuple[str, str, str]:
    """
    Ask user for file path.
    Returns (file_path, filename, extension)
    """
    print_section("Document File")

    while True:
        file_path = ask("Enter path to document file")

        if not os.path.exists(file_path):
            print_error(f"File not found: {file_path}")
            continue

        _, ext = os.path.splitext(file_path)
        ext = ext.lower()

        supported = [".pdf", ".docx", ".xlsx", ".xls"]
        if ext not in supported:
            print_error(
                f"Unsupported file type: {ext}. "
                f"Supported: {', '.join(supported)}"
            )
            continue

        filename = os.path.basename(file_path)
        print_success(f"File found: {filename} ({ext})")
        return file_path, filename, ext


# ── Step 2: Tier Selection ────────────────────────────────────────────────────

def step_get_tier() -> str:
    """
    Ask if document is general/regulatory or plan-specific.
    Returns 'generic' or 'plan_doc'
    """
    print_section("Document Tier")

    options = [
        "General / Regulatory (ERISA, IRS Publications, regulations)",
        "Plan-specific (SPD, Amendment, Form 5500, etc.)"
    ]
    idx = ask_choice("What type of document is this?", options)

    if idx == 0:
        print_success("Tier: General / Regulatory")
        return "generic"
    else:
        print_success("Tier: Plan-specific")
        return "plan_doc"


# ── Step 3: Plan Selection ────────────────────────────────────────────────────

def step_get_plan() -> dict:
    """
    Show existing plans or create new one.
    Returns plan dict.
    """
    print_section("Plan Selection")

    plans = list_plans()

    if plans:
        # Build display options
        options = []
        for p in plans:
            options.append(
                f"{p['plan_name']} — {p['employer_name']} ({p['plan_id']})"
            )
        options.append("Create new plan")

        idx = ask_choice("Select a plan:", options)

        if idx < len(plans):
            selected = plans[idx]
            print_success(
                f"Selected: {selected['plan_name']} ({selected['plan_id']})"
            )
            return selected
        # else fall through to create new plan

    else:
        print("  No plans found. Let's create one.")

    return step_create_plan()


def step_create_plan() -> dict:
    """
    Collect employer + plan details and create both.
    Returns newly created plan dict with employer_name attached.
    """
    print_section("Create New Plan")

    # ── Employer ──
    print("\n  Employer details:")
    employer_name = ask("Employer name")

    # Check if employer already exists
    existing_emp = find_employer_by_name(employer_name)
    if existing_emp:
        employer = existing_emp
        print_success(
            f"Existing employer found: {employer['employer_id']}"
        )
    else:
        employer = create_employer(employer_name)
        print_success(
            f"Employer created: {employer['employer_id']} — "
            f"{employer['employer_name']}"
        )

    # ── Plan ──
    print("\n  Plan details:")
    plan_name = ask("Plan name")
    plan_effective_date = ask_optional("Plan effective date (YYYY-MM-DD)")

    plan = create_plan(
        plan_name=plan_name,
        employer_id=employer["employer_id"],
        effective_date=plan_effective_date
    )

    print_success(
        f"Plan created: {plan['plan_id']} — {plan['plan_name']}"
    )

    # Attach employer name for display
    plan["employer_name"] = employer["employer_name"]
    return plan


# ── Step 4: Document Type ─────────────────────────────────────────────────────

def step_get_doc_type() -> str:
    """Ask user to select document type."""
    print_section("Document Type")
    idx = ask_choice("Select document type:", DOC_TYPES)
    doc_type = DOC_TYPES[idx]
    print_success(f"Document type: {doc_type}")
    return doc_type


# ── Step 5: Effective Date ────────────────────────────────────────────────────

def step_get_effective_date() -> str | None:
    """Ask for optional effective date."""
    print_section("Effective Date")
    print("  The date from which this document's rules are valid.")
    print("  Important for amendments that override older rules.")
    return ask_optional("Document effective date (YYYY-MM-DD)")


# ── Step 6: Duplicate Check ───────────────────────────────────────────────────

def step_check_duplicate(
    file_path: str,
    tier: str,
    plan_id: str | None
) -> tuple[bool, str]:
    """
    Compute file hash and check for duplicates.
    Returns (is_duplicate, file_hash)
    """
    print_section("Duplicate Check")
    print("  Computing file hash...")

    file_hash = compute_file_hash(file_path)
    print_success(f"Hash: {file_hash[:16]}...")

    if tier == "plan_doc":
        is_dup = document_exists(plan_id, file_hash)
    else:
        is_dup = generic_doc_exists(file_hash)

    if is_dup:
        print_warning("This exact file has already been ingested.")
    else:
        print_success("No duplicate found.")

    return is_dup, file_hash


# ── Step 7: Confirm and Ingest ────────────────────────────────────────────────

def step_confirm_and_ingest(
    file_path: str,
    filename: str,
    ext: str,
    tier: str,
    plan: dict | None,
    doc_type: str,
    effective_date: str | None,
    file_hash: str
) -> bool:
    """
    Show summary and ask confirmation.
    Run ingestion if confirmed.
    Returns True if ingested successfully.
    """
    print_section("Summary")
    print(f"  File:           {filename}")
    print(f"  Tier:           {tier}")
    if plan:
        print(f"  Plan:           {plan['plan_name']} ({plan['plan_id']})")
        print(f"  Employer:       {plan.get('employer_name', '')}")
    print(f"  Document type:  {doc_type}")
    print(f"  Effective date: {effective_date or 'Not specified'}")

    if not ask_confirm("Proceed with ingestion?"):
        print("\n  Ingestion cancelled.")
        return False

    # Build context for pipeline
    from app.registry.registry import _next_id, load_registry
    registry = load_registry()

    if tier == "plan_doc":
        # Check if document already exists — reuse its doc_id
        existing_doc_id = None
        plan_data = registry["plans"].get(plan["plan_id"], {})
        for doc in plan_data.get("documents", []):
            if doc["file_hash"] == file_hash:
                existing_doc_id = doc["doc_id"]
                break

        if existing_doc_id:
            doc_id = existing_doc_id
            print(f"\n  Reusing existing doc_id: {doc_id}")
        else:
            all_doc_ids = [
                doc["doc_id"]
                for p in registry["plans"].values()
                for doc in p["documents"]
            ]
            doc_id = _next_id(all_doc_ids, "DOC")

        context = {
            "tier":          "plan_doc",
            "doc_id":        doc_id,
            "doc_type":      doc_type,
            "plan_id":       plan["plan_id"],
            "plan_name":     plan["plan_name"],
            "employer_id":   plan["employer_id"],
            "employer_name": plan.get("employer_name", ""),
            "effective_date": effective_date
        }
    else:
        # Check if generic document already exists — reuse its doc_id
        existing_doc_id = None
        for doc in registry["generic_documents"]:
            if doc["file_hash"] == file_hash:
                existing_doc_id = doc["doc_id"]
                break

        if existing_doc_id:
            doc_id = existing_doc_id
            print(f"\n  Reusing existing doc_id: {doc_id}")
        else:
            existing_ids = [
                d["doc_id"] for d in registry["generic_documents"]
            ]
            doc_id = _next_id(existing_ids, "GDOC")

        context = {
            "tier":          "generic",
            "doc_id":        doc_id,
            "doc_type":      doc_type,
            "plan_id":       None,
            "plan_name":     None,
            "employer_id":   None,
            "employer_name": None,
            "effective_date": effective_date
        }

        
    # Run pipeline
    try:
        result = ingest_file(file_path, context)
    except Exception as e:
        print_error(f"Ingestion failed: {e}")
        return False

    # Update registry
    source_format = ext.lstrip(".")
    if tier == "plan_doc":
        add_document(
            plan_id=plan["plan_id"],
            filename=filename,
            doc_type=doc_type,
            source_format=source_format,
            file_hash=file_hash,
            chunk_count=result["chunk_count"],
            effective_date=effective_date
        )
    else:
        add_generic_document(
            filename=filename,
            doc_type=doc_type,
            source_format=source_format,
            file_hash=file_hash,
            chunk_count=result["chunk_count"],
            effective_date=effective_date
        )

    # Show registry summary
    print_section("Registry Updated")
    summary = get_registry_summary()
    print(f"  Employers:         {summary['employers']}")
    print(f"  Plans:             {summary['plans']}")
    print(f"  Plan documents:    {summary['plan_documents']}")
    print(f"  Generic documents: {summary['generic_documents']}")
    print(f"  Total chunks:      {summary['total_chunks']}")

    return True


# ── Main Flow ─────────────────────────────────────────────────────────────────

def main():
    print_header()

    # Show current registry state
    summary = get_registry_summary()
    print(f"\n  Current registry: "
          f"{summary['plans']} plans, "
          f"{summary['plan_documents'] + summary['generic_documents']} documents, "
          f"{summary['total_chunks']} chunks")

    # Step 1 — Get file
    file_path, filename, ext = step_get_file()

    # Step 2 — Get tier
    tier = step_get_tier()

    # Step 3 — Get plan (only for plan-specific docs)
    plan = None
    if tier == "plan_doc":
        plan = step_get_plan()

    # Step 4 — Get doc type
    doc_type = step_get_doc_type()

    # Step 5 — Get effective date
    effective_date = step_get_effective_date()

    # Step 6 — Duplicate check
    plan_id = plan["plan_id"] if plan else None
    is_dup, file_hash = step_check_duplicate(file_path, tier, plan_id)

    if is_dup:
        if not ask_confirm(
            "This file was already ingested. Ingest again anyway?"
        ):
            print("\n  Skipped. Exiting.")
            return

    # Step 7 — Confirm and ingest
    step_confirm_and_ingest(
        file_path=file_path,
        filename=filename,
        ext=ext,
        tier=tier,
        plan=plan,
        doc_type=doc_type,
        effective_date=effective_date,
        file_hash=file_hash
    )

    print("\n" + "═" * 52)
    print("   Done!")
    print("═" * 52 + "\n")


if __name__ == "__main__":
    main()