"""
compare_extractors.py
─────────────────────
Compares V1 (full document scan) vs V2 (targeted vector search) fact extractors
on PLAN_001 (Capital One Associate Savings Plan).

What this script does:
  Step 1 — Load V1 baseline results from data/facts/PLAN_001_rules.json
  Step 2 — Measure V1 token cost exactly (3 API calls, same prompt as V1)
  Step 3 — Run V2 extractor live, capture results + token stats
  Step 4 — Compare field by field (match / missed / disagree / v2_extra)
  Step 5 — Print side-by-side report, save to data/comparisons/

NOTE: fact_extractor.py (V1) is NOT modified. The V1 prompt and schema are
      copied verbatim here purely for token measurement. The stored JSON is
      used as the V1 accuracy baseline — no re-extraction needed.

Usage:
  python compare_extractors.py
"""

import os
import sys
import json
import time
from datetime import date

from dotenv import load_dotenv
load_dotenv()

import anthropic

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.registry.registry   import load_registry, get_plan_rules
from app.ingestion.fact_extractor_v2 import extract_plan_facts_v2


# ── Constants ─────────────────────────────────────────────────────────────────

PLAN_ID        = "PLAN_001"
SPD_DOC_ID     = "DOC_002"
PROCESSED_PATH = os.path.join("data", "processed", "PLAN_001_DOC_002_SPD.json")
FACTS_PATH     = os.path.join("data", "facts",     "PLAN_001_rules.json")
OUTPUT_DIR     = os.path.join("data", "comparisons")

V1_MODEL       = "claude-sonnet-4-6"
V1_MAX_TOKENS  = 4096
V1_BATCH_SIZE  = 50
V1_BATCH_DELAY = 65    # seconds — same as fact_extractor.py

# Fields to skip when comparing (traceability meta, not plan data)
SKIP_FIELDS = {"extracted_from_doc_id", "extracted_at", "extractor_version"}

# Free-text fields — wording naturally varies between extractors.
# Shown separately in the report but NOT counted in accuracy score.
TEXT_FIELDS = {
    "formula_readable",   # narrative description — different chunks → different phrasing
    "contribution_type"   # plan's own label for a contribution type
}

# Numeric fields — normalize type before comparing (0 == 0.0, "18" == 18).
# These appear both as top-level fields and inside tier/condition arrays.
NUMERIC_FIELDS = {
    "min_value",
    "from_contrib_pct", "to_contrib_pct", "match_rate_pct",
    "vested_pct", "years_of_service",
    "pct_of_pay", "max_match_pct_of_pay", "full_match_requires_contrib_pct"
}


# ── V1 Prompt & Schema (copied verbatim from fact_extractor.py) ───────────────
# These are NOT imported — fact_extractor.py is untouched.
# Copied here solely to reconstruct the exact V1 API call for token measurement.

V1_EXTRACTION_SCHEMA = {
    "plan_type":     None,
    "record_keeper": None,
    "plan_year_end": None,

    "features": {
        "auto_enrollment":         None,
        "loan_provision":          None,
        "hardship_withdrawal":     None,
        "roth_contributions":      None,
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

V1_EXTRACTION_PROMPT = """You are extracting structured plan facts from a retirement plan SPD (Summary Plan Description).

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


# ── V1 Helpers (copied logic — no import from fact_extractor.py) ──────────────

def _v1_reconstruct_text(chunks: list[dict]) -> str:
    """Reconstruct readable text from chunks — same logic as V1."""
    parts           = []
    current_section = None

    for chunk in chunks:
        meta    = chunk.get("metadata", {})
        section = meta.get("section", "")
        text    = chunk.get("text", "").strip()

        if not text:
            continue

        if section and section != current_section:
            parts.append(f"\n--- {section} ---")
            current_section = section

        parts.append(text)

    return "\n\n".join(parts)


# ── Step 1 — Load V1 baseline ─────────────────────────────────────────────────

def load_v1_baseline() -> tuple[dict, str]:
    """
    Load stored V1 extraction results and plan name.
    Returns (v1_results, plan_name).
    """
    if not os.path.exists(FACTS_PATH):
        raise FileNotFoundError(
            f"V1 facts not found at {FACTS_PATH}. "
            "Run extract_facts.py first."
        )

    with open(FACTS_PATH, "r", encoding="utf-8") as f:
        v1_results = json.load(f)

    registry  = load_registry()
    plan      = registry["plans"].get(PLAN_ID, {})
    plan_name = plan.get("plan_name", PLAN_ID)

    return v1_results, plan_name


# ── Step 2 — Measure V1 tokens (exact) ───────────────────────────────────────

def measure_v1_tokens(plan_name: str) -> dict:
    """
    Re-run V1 API calls on PLAN_001 purely for token measurement.
    Uses the exact same prompt and batching as fact_extractor.py.
    Output is discarded — V1 accuracy baseline comes from stored JSON.

    Returns token_stats dict.
    """
    print("\n" + "═" * 60)
    print("STEP 2 — Measuring V1 token cost (exact API calls)")
    print("═" * 60)

    if not os.path.exists(PROCESSED_PATH):
        raise FileNotFoundError(f"Processed chunks not found: {PROCESSED_PATH}")

    with open(PROCESSED_PATH, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    batches       = [
        chunks[i : i + V1_BATCH_SIZE]
        for i in range(0, len(chunks), V1_BATCH_SIZE)
    ]
    total_batches = len(batches)

    print(f"  Plan:    {plan_name} ({PLAN_ID})")
    print(f"  Chunks:  {len(chunks)}")
    print(f"  Batches: {total_batches} "
          f"({[len(b) for b in batches]} chunks each)")
    print(f"  Note:    Output discarded — measuring tokens only\n")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=api_key)

    token_stats = {
        "extractor":           "v1",
        "total_input_tokens":  0,
        "total_output_tokens": 0,
        "total_llm_calls":     total_batches,
        "by_batch":            {}
    }

    start_time = time.time()

    for i, batch_chunks in enumerate(batches, start=1):
        document_text = _v1_reconstruct_text(batch_chunks)

        prompt = V1_EXTRACTION_PROMPT.format(
            plan_name=plan_name,
            schema=json.dumps(V1_EXTRACTION_SCHEMA, indent=2),
            document_text=document_text
        )

        response = client.messages.create(
            model=V1_MODEL,
            max_tokens=V1_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}]
        )

        batch_key = f"batch_{i}"
        token_stats["by_batch"][batch_key] = {
            "chunks":        len(batch_chunks),
            "input_tokens":  response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens
        }
        token_stats["total_input_tokens"]  += response.usage.input_tokens
        token_stats["total_output_tokens"] += response.usage.output_tokens

        print(
            f"  Batch {i}/{total_batches}: {len(batch_chunks)} chunks  "
            f"input: {response.usage.input_tokens:,}  "
            f"output: {response.usage.output_tokens:,}"
        )

        # Wait between batches — same delay as fact_extractor.py
        if i < total_batches:
            print(f"  Waiting {V1_BATCH_DELAY}s (rate limit)...")
            time.sleep(V1_BATCH_DELAY)

    token_stats["elapsed_seconds"] = round(time.time() - start_time, 1)

    print(f"\n  V1 token measurement complete.")
    print(f"  Total input:  {token_stats['total_input_tokens']:,}")
    print(f"  Total output: {token_stats['total_output_tokens']:,}")
    print(f"  Elapsed:      {token_stats['elapsed_seconds']}s")

    return token_stats


# ── Step 3 — Run V2 live ──────────────────────────────────────────────────────

def run_v2(plan_name: str) -> tuple[dict, dict]:
    """Run V2 extractor and return (plan_rules, token_stats)."""
    print("\n" + "═" * 60)
    print("STEP 3 — Running V2 extractor (targeted vector search)")
    print("═" * 60 + "\n")

    return extract_plan_facts_v2(
        plan_id=PLAN_ID,
        plan_name=plan_name,
        doc_id=SPD_DOC_ID
    )


# ── Step 4 — Field-by-field comparison ───────────────────────────────────────

def _get_leaf_fields(d: dict, path: str = "") -> list[tuple[str, object]]:
    """
    Recursively walk a dict and return (dotted_path, value) for every leaf.
    Arrays are treated as leaves (compared as a whole, not walked into).
    Skips SKIP_FIELDS at any level.
    """
    leaves = []

    for key, value in d.items():
        if key in SKIP_FIELDS:
            continue

        full_path = f"{path}.{key}" if path else key

        if isinstance(value, dict):
            leaves.extend(_get_leaf_fields(value, full_path))
        else:
            # Scalar or list — treat as leaf
            leaves.append((full_path, value))

    return leaves


def _get_nested(d: dict, dotted_path: str):
    """Retrieve a value from a nested dict using a dotted path string."""
    keys = dotted_path.split(".")
    current = d
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
    return current


def _normalize_numeric(val):
    """Convert numeric strings or ints to float for type-safe comparison."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return val


def _normalize_list(lst) -> list:
    """
    Normalize numeric field values inside a list of dicts.
    Handles tier arrays (match tiers, vesting tiers) and condition arrays.
    """
    if not isinstance(lst, list):
        return lst
    result = []
    for item in lst:
        if isinstance(item, dict):
            result.append({
                k: _normalize_numeric(v) if k in NUMERIC_FIELDS else v
                for k, v in item.items()
            })
        else:
            result.append(item)
    return result


def _values_match(v1, v2, field_leaf: str = "") -> bool:
    """
    Compare two field values with type normalization.
    - Numeric fields:  normalize to float before comparing (0 == 0.0, '18' == 18)
    - List fields:     normalize numerics within, then compare as JSON
    - Scalar fields:   direct equality
    """
    if field_leaf in NUMERIC_FIELDS:
        return _normalize_numeric(v1) == _normalize_numeric(v2)
    if isinstance(v1, list) or isinstance(v2, list):
        return (
            json.dumps(_normalize_list(v1 or []), sort_keys=True) ==
            json.dumps(_normalize_list(v2 or []), sort_keys=True)
        )
    return v1 == v2


def compare_results(v1: dict, v2: dict) -> list[dict]:
    """
    Compare V1 and V2 results field by field.

    Each result has:
      field       : dotted path (e.g. 'employer_match.tiers')
      field_type  : 'structured' | 'text'
                    structured = counted in accuracy score
                    text       = free-text fields, shown for review only
      status      : match | missed | disagree | v2_extra | both_null
      v1, v2      : the values from each extractor
    """
    results  = []
    v1_paths = set()

    for path, v1_value in _get_leaf_fields(v1):
        v1_paths.add(path)
        v2_value   = _get_nested(v2, path)
        field_leaf = path.split(".")[-1]
        field_type = "text" if field_leaf in TEXT_FIELDS else "structured"

        if v1_value is None and (v2_value is None or v2_value == [] or v2_value == ""):
            status = "both_null"
        elif v1_value is None:
            status = "v2_extra"
        elif v2_value is None or v2_value == [] or v2_value == "":
            status = "missed"
        elif _values_match(v1_value, v2_value, field_leaf):
            status = "match"
        else:
            status = "disagree"

        results.append({
            "field":      path,
            "field_type": field_type,
            "status":     status,
            "v1":         v1_value,
            "v2":         v2_value
        })

    # Check V2 for any extra fields not in V1
    for path, v2_value in _get_leaf_fields(v2):
        if path not in v1_paths and v2_value is not None and v2_value != []:
            field_leaf = path.split(".")[-1]
            field_type = "text" if field_leaf in TEXT_FIELDS else "structured"
            results.append({
                "field":      path,
                "field_type": field_type,
                "status":     "v2_extra",
                "v1":         None,
                "v2":         v2_value
            })

    return results


# ── Step 5 — Report ───────────────────────────────────────────────────────────

def _pct_reduction(v1_val: int, v2_val: int) -> str:
    if v1_val == 0:
        return "n/a"
    reduction = round((1 - v2_val / v1_val) * 100, 1)
    arrow = "▼" if reduction > 0 else "▲"
    return f"{arrow} {abs(reduction)}%"


def print_report(
    plan_name:   str,
    v1_tokens:   dict,
    v2_tokens:   dict,
    comparison:  list[dict]
):
    W = 66  # report width

    def _fmt(val):
        """Truncate long values for display."""
        if val is None:
            return "null"
        s = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
        return s[:42] + "…" if len(s) > 42 else s

    def _print_row(icon, field, v1_val, v2_val, status):
        f = f"{field:<47}"
        if status == "match":
            print(f"  {icon}  {f} {_fmt(v1_val)}")
        elif status == "missed":
            print(f"  {icon}  {f} V1={_fmt(v1_val)}   V2=null")
        elif status == "disagree":
            print(f"  {icon}  {f} V1={_fmt(v1_val)}")
            print(f"     {'':47} V2={_fmt(v2_val)}")
        elif status == "v2_extra":
            print(f"  {icon}  {f} V1=null   V2={_fmt(v2_val)}")
        elif status == "text":
            print(f"  ~  {f} V1={_fmt(v1_val)}")
            print(f"     {'':47} V2={_fmt(v2_val)}")

    print("\n" + "═" * W)
    print(f"  EXTRACTOR COMPARISON — {PLAN_ID}")
    print(f"  {plan_name}")
    print("═" * W)

    # ── Cost table ──
    v1_in  = v1_tokens["total_input_tokens"]
    v1_out = v1_tokens["total_output_tokens"]
    v2_in  = v2_tokens["total_input_tokens"]
    v2_out = v2_tokens["total_output_tokens"]

    print(f"\n{'COST':<32} {'V1 Full Scan':>14}  {'V2 Targeted':>12}")
    print("─" * W)
    print(f"{'Input tokens':<32} {v1_in:>14,}  {v2_in:>12,}  {_pct_reduction(v1_in, v2_in)}")
    print(f"{'Output tokens':<32} {v1_out:>14,}  {v2_out:>12,}")
    print(f"{'Total tokens':<32} {v1_in+v1_out:>14,}  {v2_in+v2_out:>12,}  {_pct_reduction(v1_in+v1_out, v2_in+v2_out)}")
    print(f"{'LLM calls':<32} {v1_tokens['total_llm_calls']:>14}  {v2_tokens['total_llm_calls']:>12}")
    print(f"{'Elapsed (seconds)':<32} {v1_tokens['elapsed_seconds']:>14.1f}  {v2_tokens['elapsed_seconds']:>12.1f}")

    # ── Split into structured vs text fields ──
    structured = [r for r in comparison if r["field_type"] == "structured"]
    text_flds  = [r for r in comparison if r["field_type"] == "text"]

    s_match    = [r for r in structured if r["status"] == "match"]
    s_missed   = [r for r in structured if r["status"] == "missed"]
    s_disagree = [r for r in structured if r["status"] == "disagree"]
    s_v2extra  = [r for r in structured if r["status"] == "v2_extra"]
    s_bothnull = [r for r in structured if r["status"] == "both_null"]

    # Comparable = structured fields where at least one extractor had a value
    s_comparable = [r for r in structured if r["status"] != "both_null"]

    STATUS_ICONS = {"match": "✓", "missed": "✗", "disagree": "⚠", "v2_extra": "+"}

    # ── Structured fields section ──
    print(f"\n\nSTRUCTURED FIELDS  (exact match — counted in accuracy)")
    print("─" * W)
    for r in structured:
        if r["status"] == "both_null":
            continue
        icon = STATUS_ICONS.get(r["status"], "?")
        _print_row(icon, r["field"], r["v1"], r["v2"], r["status"])

    # ── Text fields section ──
    if text_flds:
        print(f"\n\nTEXT FIELDS  (wording varies — informational only, not counted)")
        print("─" * W)
        for r in text_flds:
            if r["v1"] is None and r["v2"] is None:
                continue
            if r["status"] == "match":
                print(f"  ✓  {r['field']:<47} {_fmt(r['v1'])}")
            else:
                _print_row("~", r["field"], r["v1"], r["v2"], "text")

    # ── Summary ──
    total_s = len(s_comparable)
    print("\n" + "─" * W)
    print(f"  STRUCTURED ACCURACY")
    print(f"  ✓  Match:        {len(s_match):3d} / {total_s}")
    print(f"  ✗  Missed:       {len(s_missed):3d}   (V2 returned null, V1 had value)")
    print(f"  ⚠  Disagree:     {len(s_disagree):3d}   (both non-null but different)")
    print(f"  +  V2 extra:     {len(s_v2extra):3d}   (V2 found field V1 missed)")
    print(f"  ·  Both null:    {len(s_bothnull):3d}   (neither extractor found value)")
    if total_s > 0:
        accuracy = round(len(s_match) / total_s * 100, 1)
        print(f"\n  Accuracy:  {accuracy}%  ({len(s_match)}/{total_s} structured fields matched)")
    print(f"\n  TEXT FIELDS (not counted):  {len(text_flds)} fields reviewed separately")
    print("═" * W)


def save_comparison(
    plan_name:  str,
    v1_tokens:  dict,
    v2_tokens:  dict,
    v2_results: dict,
    comparison: list[dict]
):
    """Save full comparison data to data/comparisons/."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"{PLAN_ID}_comparison.json")

    output = {
        "plan_id":       PLAN_ID,
        "plan_name":     plan_name,
        "compared_at":   str(date.today()),
        "v1_token_stats": v1_tokens,
        "v2_token_stats": v2_tokens,
        "v2_results":    v2_results,
        "field_comparison": comparison,
        "summary": {
            "structured": {
                "match":     len([r for r in comparison if r["status"] == "match"    and r["field_type"] == "structured"]),
                "missed":    len([r for r in comparison if r["status"] == "missed"   and r["field_type"] == "structured"]),
                "disagree":  len([r for r in comparison if r["status"] == "disagree" and r["field_type"] == "structured"]),
                "v2_extra":  len([r for r in comparison if r["status"] == "v2_extra" and r["field_type"] == "structured"]),
                "both_null": len([r for r in comparison if r["status"] == "both_null"and r["field_type"] == "structured"]),
            },
            "text": {
                "total":    len([r for r in comparison if r["field_type"] == "text"]),
                "match":    len([r for r in comparison if r["status"] == "match"    and r["field_type"] == "text"]),
                "disagree": len([r for r in comparison if r["status"] == "disagree" and r["field_type"] == "text"]),
            }
        }
    }

    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    os.replace(tmp, output_path)

    print(f"\n  Comparison saved → {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("  EXTRACTOR COMPARISON — V1 Full Scan vs V2 Targeted Search")
    print(f"  Plan: {PLAN_ID}")
    print("=" * 64)

    # Step 1 — Load V1 baseline
    print("\n" + "═" * 60)
    print("STEP 1 — Loading V1 baseline from stored JSON")
    print("═" * 60)
    v1_results, plan_name = load_v1_baseline()
    print(f"  Loaded: {FACTS_PATH}")
    print(f"  Plan:   {plan_name}")
    print(f"  V1 extractor version: {v1_results.get('extractor_version', 'v1')}")

    # Step 2 — Measure V1 tokens
    v1_tokens = measure_v1_tokens(plan_name)

    # Step 3 — Run V2
    v2_results, v2_tokens = run_v2(plan_name)

    # Step 4 — Compare
    print("\n" + "═" * 60)
    print("STEP 4 — Comparing field by field")
    print("═" * 60 + "\n")
    comparison = compare_results(v1_results, v2_results)

    # Step 5 — Report + save
    print_report(plan_name, v1_tokens, v2_tokens, comparison)
    save_comparison(plan_name, v1_tokens, v2_tokens, v2_results, comparison)


if __name__ == "__main__":
    main()
