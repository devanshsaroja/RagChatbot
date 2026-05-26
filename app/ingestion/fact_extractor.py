import os
import re
import json
import time
from datetime import date

from dotenv import load_dotenv
load_dotenv()

import anthropic


# ── Constants ─────────────────────────────────────────────────────────────────

MODEL       = "claude-sonnet-4-6"
MAX_TOKENS  = 4096
BATCH_SIZE  = 50   # chunks per batch — keeps each call within rate limit
BATCH_DELAY = 65   # seconds to wait between batches (10k token/min rate limit)


# ── Extraction Schema ─────────────────────────────────────────────────────────
# Generalized schema — works for any 401k plan regardless of how many tiers
# each field has. Arrays handle 0, 1, 2, or N tiers identically.

EXTRACTION_SCHEMA = {
    "plan_type":     None,
    "record_keeper": None,
    "plan_year_end": None,

    "features": {
        "auto_enrollment":        None,
        "loan_provision":         None,
        "hardship_withdrawal":    None,
        "roth_contributions":     None,
        "after_tax_contributions": None
    },

    "employer_match": {
        "available": None,
        "tiers": [
            {
                "tier":             "<int>",
                "from_contrib_pct": "<float — lower bound of this tier's contribution range>",
                "to_contrib_pct":   "<float — upper bound of this tier's contribution range>",
                "match_rate_pct":   "<float — employer match rate for this tier>"
            }
        ],
        "max_match_pct_of_pay":            None,
        "full_match_requires_contrib_pct": None,
        "true_up_provision":               None,
        "formula_readable":                None
    },

    "nonelective_contribution": {
        "available":         None,
        "pct_of_pay":        None,
        "contribution_type": None,
        "formula_readable":  None
    },

    "vesting": {
        "employee_contributions": {
            "type":             None,
            "formula_readable": None
        },
        "employer_match": {
            "type": None,
            "tiers": [
                {
                    "years_of_service": "<int>",
                    "vested_pct":       "<float>"
                }
            ],
            "immediate_vest_triggers": [],
            "formula_readable":        None
        },
        "nonelective": {
            "type":             None,
            "formula_readable": None
        }
    },

    "eligibility": {
        "conditions": [
            {
                "type":      "<age | service | hours>",
                "min_value": "<number>",
                "unit":      "<years | months | hours_per_year>"
            }
        ],
        "entry_dates":      None,
        "formula_readable": None
    }
}


# ── Extraction Prompt ─────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are extracting structured plan facts from a retirement plan SPD (Summary Plan Description).

Plan: {plan_name}

Your task: Read the document text below and extract specific plan facts into the JSON schema provided.

Rules:
- Only extract what is explicitly stated in the document. Do not infer or guess.
- If a field is not mentioned in the document, set it to null.
- For employer_match tiers: use one object per tier with consistent fields:
    from_contrib_pct (lower bound), to_contrib_pct (upper bound), match_rate_pct.
    Example 2-tier: [{{"tier":1,"from_contrib_pct":0,"to_contrib_pct":3,"match_rate_pct":100}},{{"tier":2,"from_contrib_pct":3,"to_contrib_pct":6,"match_rate_pct":50}}]
    Example 1-tier: [{{"tier":1,"from_contrib_pct":0,"to_contrib_pct":6,"match_rate_pct":50}}]
- For vesting tiers (cliff): one object: [{{"years_of_service":2,"vested_pct":100}}]
- For vesting tiers (graded): one object per step: [{{"years_of_service":2,"vested_pct":20}},{{"years_of_service":3,"vested_pct":40}},...}}]
- For eligibility conditions: one object per condition type (age, service, hours).
- Always populate formula_readable with plain English describing the rule as stated in the document.
- Return ONLY valid JSON — no explanation, no markdown fences, no extra text.

Schema to fill:
{schema}

Document text:
{document_text}"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reconstruct_text(chunks: list[dict]) -> str:
    """
    Reconstruct readable document text from chunks.
    Includes section headers for context so Claude understands structure.
    """
    parts = []
    current_section = None

    for chunk in chunks:
        meta = chunk.get("metadata", {})
        section = meta.get("section", "")
        text    = chunk.get("text", "").strip()

        if not text:
            continue

        # Insert section header when it changes
        if section and section != current_section:
            parts.append(f"\n--- {section} ---")
            current_section = section

        parts.append(text)

    return "\n\n".join(parts)


def _parse_json_response(response_text: str) -> dict:
    """
    Parse Claude's JSON response.
    Strips accidental markdown fences if present.
    """
    text = response_text.strip()

    # Strip markdown fences if Claude added them despite instructions
    if text.startswith("```"):
        text = re.sub(r'^```[a-z]*\n?', '', text)
        text = re.sub(r'\n?```$',       '', text)
        text = text.strip()

    return json.loads(text)


# ── Batch Helpers ─────────────────────────────────────────────────────────────

def _extract_batch(
    client:        anthropic.Anthropic,
    document_text: str,
    plan_name:     str,
    batch_num:     int,
    total_batches: int
) -> dict:
    """
    Run one Claude extraction call for a single batch of document text.
    Returns the parsed plan_rules dict for that batch.
    """
    prompt = EXTRACTION_PROMPT.format(
        plan_name=plan_name,
        schema=json.dumps(EXTRACTION_SCHEMA, indent=2),
        document_text=document_text
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}]
    )

    response_text = response.content[0].text
    print(f"    Batch {batch_num}/{total_batches} complete "
          f"({len(response_text):,} chars returned)")

    return _parse_json_response(response_text)


def _merge_two(a, b):
    """
    Recursively merge b into a.
    - Dicts:   merge key by key recursively
    - Lists:   keep the longer list (more complete tier data)
    - Scalars: a takes priority — first non-null value wins
    """
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        result = {}
        for key in set(list(a.keys()) + list(b.keys())):
            result[key] = _merge_two(a.get(key), b.get(key))
        return result
    if isinstance(a, list) and isinstance(b, list):
        # Keep whichever list is longer (more tier entries)
        return a if len(a) >= len(b) else b
    # Scalar — a wins (first non-null across batches)
    return a


def _merge_facts(results: list[dict]) -> dict:
    """
    Merge a list of per-batch extraction results into one final plan_rules dict.
    """
    if not results:
        return {}
    merged = results[0]
    for result in results[1:]:
        merged = _merge_two(merged, result)
    return merged


# ── Main Extraction Function ──────────────────────────────────────────────────

def extract_plan_facts(
    chunks:    list[dict],
    plan_name: str,
    plan_id:   str,
    doc_id:    str
) -> dict:
    """
    Extract structured plan facts from SPD chunks using Claude.

    Splits chunks into batches of BATCH_SIZE, calls Claude once per batch,
    then merges results. This avoids API rate limit errors on large documents.

    Called from two places:
      - pipeline.py    : automatically during new SPD ingestion
      - extract_facts.py : one-time extraction for already-ingested plans

    Args:
        chunks:    List of chunk dicts loaded from data/processed/*.json
        plan_name: Plan name — used as context in the extraction prompt
        plan_id:   Plan ID — stored in output for traceability
        doc_id:    Doc ID  — stored in output for traceability

    Returns:
        plan_rules dict matching the generalized schema.
        Always includes extracted_from_doc_id and extracted_at.
    """
    total_chunks  = len(chunks)
    batches       = [
        chunks[i : i + BATCH_SIZE]
        for i in range(0, total_chunks, BATCH_SIZE)
    ]
    total_batches = len(batches)

    print(f"  Extracting plan facts from {total_chunks} chunks "
          f"({total_batches} batch(es) of up to {BATCH_SIZE} chunks each)...")

    # Set up Claude client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=api_key)

    # Process each batch
    results = []

    for i, batch_chunks in enumerate(batches, start=1):
        batch_text = _reconstruct_text(batch_chunks)
        print(f"  Batch {i}/{total_batches}: "
              f"{len(batch_chunks)} chunks, {len(batch_text):,} chars")

        try:
            result = _extract_batch(client, batch_text, plan_name, i, total_batches)
            results.append(result)
        except Exception as e:
            print(f"    Batch {i} failed: {e} — skipping")

        # Wait between batches to respect rate limit (skip after last batch)
        if i < total_batches:
            print(f"  Waiting {BATCH_DELAY}s before next batch (rate limit)...")
            time.sleep(BATCH_DELAY)

    if not results:
        raise ValueError("All batches failed — no facts could be extracted")

    # Merge all batch results
    print(f"  Merging {len(results)} batch result(s)...")
    plan_rules = _merge_facts(results)

    # Add traceability fields
    plan_rules["extracted_from_doc_id"] = doc_id
    plan_rules["extracted_at"]          = str(date.today())

    print(f"  Extraction complete.")
    return plan_rules
