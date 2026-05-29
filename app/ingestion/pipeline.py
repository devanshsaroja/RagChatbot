import os
import json
from datetime import date

from app.ingestion.parser_pdf import parse_pdf
from app.ingestion.parser_docx import parse_docx
from app.ingestion.parser_excel import parse_excel
from app.ingestion.chunker import chunk_all
from app.storage.vector_store import store_chunks
from app.ingestion.fact_extractor_v3 import extract_plan_facts_v3
from app.registry.registry import update_plan_rules

# ── Constants ─────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {
    ".pdf":  parse_pdf,
    ".docx": parse_docx,
    ".xlsx": parse_excel,
    ".xls":  parse_excel,
}

PROCESSED_DIR = os.path.join("data", "processed")


# ── Utilities ─────────────────────────────────────────────────────────────────

def get_file_extension(file_path: str) -> str:
    _, ext = os.path.splitext(file_path)
    return ext.lower()


def get_output_filename(context: dict) -> str:
    """
    Generate output filename for chunks JSON.

    Plan doc:    PLAN_001_DOC_001_SPD.json
    Generic doc: GENERIC_GDOC_001_IRS_Publication.json
    """
    doc_type_clean = context["doc_type"].replace(" ", "_").replace("/", "_")

    if context["tier"] == "plan_doc":
        return f"{context['plan_id']}_{context['doc_id']}_{doc_type_clean}.json"
    else:
        return f"GENERIC_{context['doc_id']}_{doc_type_clean}.json"


def inject_plan_metadata(chunks: list[dict], context: dict) -> list[dict]:
    """
    Inject plan-level metadata into every chunk.
    Parser only knows document-level metadata.
    Pipeline adds plan context on top.
    """
    ingested_at = str(date.today())

    for chunk in chunks:
        chunk["metadata"].update({
            # Document identity
            "doc_id":        context["doc_id"],
            "doc_type":      context["doc_type"],
            "tier":          context["tier"],
            "effective_date": context.get("effective_date"),
            "ingested_at":   ingested_at,

            # Plan context (null for generic docs)
            "plan_id":       context.get("plan_id"),
            "plan_name":     context.get("plan_name"),
            "employer_id":   context.get("employer_id"),
            "employer_name": context.get("employer_name"),
        })

    return chunks


def save_chunks(chunks: list[dict], output_filename: str) -> str:
    """
    Save chunks to data/processed/ folder.
    Returns the full save path.
    """
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    save_path = os.path.join(PROCESSED_DIR, output_filename)

    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(chunks, f, indent=2, ensure_ascii=False)

    return save_path


# ── Main Entry Point ──────────────────────────────────────────────────────────

def ingest_file(file_path: str, context: dict) -> dict:
    """
    Full ingestion pipeline for a single file.

    Args:
        file_path: Path to the document
        context:   Plan metadata from ingest.py
                   Must contain: tier, doc_id, doc_type,
                   plan_id, plan_name, employer_id,
                   employer_name, effective_date

    Returns:
        {
            "chunks":      list of chunk dicts,
            "chunk_count": int,
            "save_path":   str,
            "stats":       dict
        }
    """
    # Validate file
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = get_file_extension(file_path)
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {ext}. "
            f"Supported: {list(SUPPORTED_EXTENSIONS.keys())}"
        )

    print(f"\n{'='*50}")
    print(f"Ingesting: {os.path.basename(file_path)}")
    print(f"Type:      {ext}")
    print(f"Tier:      {context['tier']}")
    if context.get("plan_id"):
        print(f"Plan:      {context['plan_name']} ({context['plan_id']})")
    print(f"Doc type:  {context['doc_type']}")
    print(f"{'='*50}")

    # Step 1 — Parse
    print("\nStep 1/6 — Parsing document...")
    parser_fn = SUPPORTED_EXTENSIONS[ext]
    parsed_blocks = parser_fn(file_path)

    # Step 2 — Chunk
    print("\nStep 2/6 — Chunking blocks...")
    chunks = chunk_all(parsed_blocks)
    print(f"  Created {len(chunks)} chunks")

    # Step 3 — Inject plan metadata
    print("\nStep 3/6 — Injecting metadata...")
    chunks = inject_plan_metadata(chunks, context)
    print(f"  Metadata injected into all chunks")

    # Step 4 — Save
    print("\nStep 4/6 — Saving chunks...")
    output_filename = get_output_filename(context)
    save_path = save_chunks(chunks, output_filename)
    print(f"  Saved to: {save_path}")

    # Step 5 — Store in vector database
    print("\nStep 5/6 — Storing in vector database...")
    vector_stats = store_chunks(chunks)
    print(f"  Stored:  {vector_stats['stored']}")
    print(f"  Skipped: {vector_stats['skipped']}")
    print(f"  Failed:  {vector_stats['failed']}")

    # Step 6 — Extract plan facts (SPD only)
    if context.get("doc_type") == "SPD" and context.get("plan_id"):
        print("\nStep 6/6 — Extracting plan facts...")
        try:
            plan_rules, _ = extract_plan_facts_v3(
                plan_id=context["plan_id"],
                plan_name=context.get("plan_name", ""),
                doc_id=context["doc_id"]
            )
            update_plan_rules(context["plan_id"], plan_rules)
            print("  Plan facts extracted and saved to registry ✓")
        except Exception as e:
            print(f"  Warning: fact extraction failed — {e}")
            print("  Ingestion complete. Run extract_facts.py --force to retry.")
    else:
        print("\nStep 6/6 — Skipped (not an SPD)")

    # Build stats
    token_counts = [c["metadata"]["token_count"] for c in chunks]
    stats = {
        "blocks_extracted": len(parsed_blocks),
        "chunks_created":   len(chunks),
        "token_min":        min(token_counts),
        "token_max":        max(token_counts),
        "token_avg":        sum(token_counts) // len(token_counts),
        "save_path":        save_path,
        "vector_stored":    vector_stats["stored"],
        "vector_skipped":   vector_stats["skipped"],
        "vector_failed":    vector_stats["failed"]
    }

    print(f"\n{'='*50}")
    print(f"✓ Ingestion complete")
    print(f"  Blocks:   {stats['blocks_extracted']}")
    print(f"  Chunks:   {stats['chunks_created']}")
    print(f"  Tokens:   {stats['token_min']} min / "
          f"{stats['token_max']} max / "
          f"{stats['token_avg']} avg")
    print(f"  Vectors:  {stats['vector_stored']} stored / "
          f"{stats['vector_skipped']} skipped / "
          f"{stats['vector_failed']} failed")
    print(f"{'='*50}")


    return {
        "chunks":      chunks,
        "chunk_count": len(chunks),
        "save_path":   save_path,
        "stats":       stats
    }


# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test with Microsoft 401k SPD
    context = {
        "tier":          "plan_doc",
        "doc_id":        "DOC_001",
        "doc_type":      "SPD",
        "plan_id":       "PLAN_001",
        "plan_name":     "Microsoft Savings Plus 401(k) Plan",
        "employer_id":   "EMP_001",
        "employer_name": "Microsoft Corporation",
        "effective_date": "2025-01-01"
    }

    result = ingest_file("data/raw/Microsoft_401k_SPD.pdf", context)

    # Show sample chunk with full metadata
    print("\nSample chunk with full metadata:")
    print("-" * 50)
    sample = result["chunks"][5]
    for key, val in sample["metadata"].items():
        print(f"  {key}: {val}")
    print(f"\n  Text preview: {sample['text'][:150]}...")