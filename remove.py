import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.registry.registry import (
    load_registry,
    find_document,
    remove_document_from_plan,
    remove_generic_document,
    list_plans,
    list_generic_documents
)
from app.storage.vector_store import delete_doc_chunks


# ── Display Helpers ───────────────────────────────────────────────────────────

def print_header():
    print("\n" + "═" * 52)
    print("   Retirement Plan RAG — Remove Document")
    print("═" * 52)


def print_registry():
    """Show current registry state."""
    plans = list_plans()
    generic_docs = list_generic_documents()

    print("\n── Plan Documents ────────────────────────────────")
    if plans:
        for plan in plans:
            print(f"\n  {plan['plan_id']} — {plan['plan_name']}")
            print(f"  Employer: {plan['employer_name']}")
            if plan.get("documents"):
                for doc in plan["documents"]:
                    print(
                        f"    [{doc['doc_id']}] {doc['doc_type']} | "
                        f"{doc['filename']} | "
                        f"{doc['chunk_count']} chunks"
                    )
            else:
                print("    No documents")
    else:
        print("  No plans found.")

    print("\n── Generic Documents ─────────────────────────────")
    if generic_docs:
        for doc in generic_docs:
            print(
                f"  [{doc['doc_id']}] {doc['doc_type']} | "
                f"{doc['filename']} | "
                f"{doc['chunk_count']} chunks"
            )
    else:
        print("  No generic documents found.")


# ── Main Flow ─────────────────────────────────────────────────────────────────

def main():
    print_header()
    print_registry()

    print("\n── Select Document to Remove ─────────────────────")
    doc_id = input("\n  Enter document ID to remove (e.g. DOC_001): ").strip().upper()

    if not doc_id:
        print("  No ID entered. Exiting.")
        return

    # Find the document
    doc, plan_id = find_document(doc_id)

    if not doc:
        print(f"\n  ✗ Document '{doc_id}' not found in registry.")
        return

    # Show what will be removed
    print(f"\n── Document Found ────────────────────────────────")
    print(f"  ID:       {doc['doc_id']}")
    print(f"  File:     {doc['filename']}")
    print(f"  Type:     {doc['doc_type']}")
    print(f"  Chunks:   {doc['chunk_count']}")
    if plan_id:
        registry = load_registry()
        plan = registry["plans"][plan_id]
        print(f"  Plan:     {plan['plan_name']} ({plan_id})")
        # Warn if plan will be empty after removal
        if len(plan["documents"]) == 1:
            print(f"\n  ⚠ This is the only document in {plan['plan_name']}.")
            print(f"    The plan and employer will also be removed.")
    else:
        print(f"  Tier:     Generic / Regulatory")

    # Find processed JSON file — exact match only
    # Avoid substring matches (DOC_001 matching GDOC_001)
    processed_files = []
    if os.path.exists("data/processed"):
        for f in os.listdir("data/processed"):
            # Check for exact doc_id match with word boundaries
            # File format: PLAN_001_DOC_001_SPD.json or GENERIC_GDOC_001_Type.json
            parts = f.replace('.json', '').split('_')
            # Reconstruct potential doc_id patterns
            for i in range(len(parts) - 1):
                potential_id = f"{parts[i]}_{parts[i+1]}"
                if potential_id == doc_id:
                    processed_files.append(f)
                    break

    if processed_files:
        print(f"\n  Processed files to delete:")
        for f in processed_files:
            print(f"    data/processed/{f}")

    # Confirm
    confirm = input(
        f"\n  Remove {doc_id} permanently? [y/n]: "
    ).strip().lower()

    if confirm not in ["y", "yes"]:
        print("\n  Cancelled. Nothing removed.")
        return

    # Step 1 — Remove from ChromaDB
    print(f"\n  Removing from vector database...")
    deleted_chunks = delete_doc_chunks(doc_id)
    print(f"  ✓ Deleted {deleted_chunks} chunks from ChromaDB")

    # Step 2 — Remove processed JSON
    for f in processed_files:
        path = os.path.join("data", "processed", f)
        if os.path.exists(path):
            os.remove(path)
            print(f"  ✓ Deleted processed file: {f}")

    # Step 3 — Remove from registry
    if plan_id:
        removed = remove_document_from_plan(plan_id, doc_id)
    else:
        removed = remove_generic_document(doc_id)

    if removed:
        print(f"  ✓ Removed from registry")
    else:
        print(f"  ✗ Registry removal failed")

    # Show updated registry
    print(f"\n── Registry After Removal ────────────────────────")
    print_registry()

    print("\n" + "═" * 52)
    print("   Done!")
    print("═" * 52 + "\n")


if __name__ == "__main__":
    main()