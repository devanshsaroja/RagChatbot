"""
extract_facts.py — One-time utility to extract plan facts from already-ingested SPDs.

Reads processed chunk files from data/processed/ and runs Claude extraction
for each plan that has an SPD document, then stores the results in registry.json
under plan_rules.

Usage:
    python extract_facts.py           # skip plans that already have plan_rules
    python extract_facts.py --force   # re-extract even if plan_rules already set
"""

import json
import os
import sys

from app.registry.registry import load_registry, update_plan_rules
from app.ingestion.fact_extractor import extract_plan_facts


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_spd_chunks(plan_id: str, doc_id: str) -> list[dict] | None:
    """
    Load processed chunks for a given plan's SPD document.
    Returns list of chunk dicts, or None if file not found.
    """
    path = os.path.join("data", "processed", f"{plan_id}_{doc_id}_SPD.json")

    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(force: bool = False):
    print("=" * 60)
    print("Plan Fact Extraction Utility")
    print("=" * 60)

    registry = load_registry()
    plans = registry["plans"]

    if not plans:
        print("No plans found in registry.")
        return

    print(f"Found {len(plans)} plan(s) in registry.\n")

    extracted = 0
    skipped   = 0
    failed    = 0

    for plan_id, plan in plans.items():
        plan_name = plan.get("plan_name", plan_id)
        print(f"Plan: {plan_id} — {plan_name}")

        # Find SPD document
        spd_doc = next(
            (d for d in plan.get("documents", []) if d["doc_type"] == "SPD"),
            None
        )

        if not spd_doc:
            print(f"  No SPD document found — skipping\n")
            skipped += 1
            continue

        doc_id = spd_doc["doc_id"]
        print(f"  SPD doc: {doc_id} ({spd_doc.get('filename', 'unknown')})")

        # Skip if already extracted and --force not set
        if not force and plan.get("plan_rules"):
            print(f"  Already extracted — skipping (use --force to re-run)\n")
            skipped += 1
            continue

        # Load chunk file
        chunks = find_spd_chunks(plan_id, doc_id)

        if chunks is None:
            print(f"  Chunk file not found at data/processed/{plan_id}_{doc_id}_SPD.json — skipping\n")
            failed += 1
            continue

        print(f"  Loaded {len(chunks)} chunks from processed file")

        # Extract plan facts via Claude
        try:
            plan_rules = extract_plan_facts(
                chunks=chunks,
                plan_name=plan_name,
                plan_id=plan_id,
                doc_id=doc_id
            )
        except Exception as e:
            print(f"  Extraction failed: {e}\n")
            failed += 1
            continue

        # Store in registry
        success = update_plan_rules(plan_id, plan_rules)

        if success:
            print(f"  Saved to registry ✓\n")
            extracted += 1
        else:
            print(f"  Failed to save — plan not found in registry\n")
            failed += 1

    # Summary
    print("=" * 60)
    print(f"Done.")
    print(f"  Extracted: {extracted}")
    print(f"  Skipped:   {skipped}")
    print(f"  Failed:    {failed}")
    print("=" * 60)


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    force = "--force" in sys.argv
    if force:
        print("Running in FORCE mode — will re-extract all plans.\n")
    main(force=force)
