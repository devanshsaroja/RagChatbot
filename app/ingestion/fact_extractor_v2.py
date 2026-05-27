import os
import re
import json
import time
from datetime import date

from dotenv import load_dotenv
load_dotenv()

import anthropic

from app.retrieval.retriever import retrieve_from_plan


# ── Constants ─────────────────────────────────────────────────────────────────

MODEL           = "claude-sonnet-4-6"
MAX_TOKENS      = 1024   # one field group at a time — smaller responses
TOP_K_PER_QUERY = 8      # chunks retrieved per query


# ── Field Queries ─────────────────────────────────────────────────────────────
# Each group defines:
#   queries : 1–3 search strings to retrieve relevant chunks from VectorDB
#   schema  : the sub-schema Claude fills in for this group only
#
# Static queries tuned for common SPD / retirement plan document terminology.
# When schema is extended (loans, distributions, etc.) — add a new group here.

FIELD_QUERIES = {

    "plan_info": {
        "queries": [
            "plan type 401k record keeper administrator trustee",
            "plan year end date fiscal year"
        ],
        "schema": {
            "plan_type":     None,
            "record_keeper": None,
            "plan_year_end": None
        },
        "field_constraints": {
            "plan_type":     "Return the short canonical type only — e.g. '401(k)', '403(b)', 'profit sharing'. Never a full sentence or description.",
            "record_keeper": "Return the brand/company name only — e.g. 'Fidelity', 'Empower', 'Vanguard'. Not the full legal entity name."
        }
    },

    "features": {
        "queries": [
            "automatic enrollment auto enrollment default deferral percentage",
            "loan provision hardship withdrawal roth after-tax contribution"
        ],
        "schema": {
            "features": {
                "auto_enrollment":         None,
                "loan_provision":          None,
                "hardship_withdrawal":     None,
                "roth_contributions":      None,
                "after_tax_contributions": None
            }
        }
    },

    "employer_match": {
        "queries": [
            "employer matching contribution percentage formula tiers",
            "employer match dollar for dollar safe harbor true up provision"
        ],
        "schema": {
            "employer_match": {
                "available":                       None,
                "tiers": [
                    {
                        "tier":             "<int>",
                        "from_contrib_pct": "<float>",
                        "to_contrib_pct":   "<float>",
                        "match_rate_pct":   "<float>"
                    }
                ],
                "max_match_pct_of_pay":            None,
                "full_match_requires_contrib_pct": None,
                "true_up_provision":               None,
                "formula_readable":                None
            }
        }
    },

    "nonelective_contribution": {
        "queries": [
            "nonelective contribution safe harbor profit sharing percentage of pay"
        ],
        "schema": {
            "nonelective_contribution": {
                "available":         None,
                "pct_of_pay":        None,
                "contribution_type": None,
                "formula_readable":  None
            }
        },
        "field_constraints": {
            "contribution_type": "Return the plan's own name for this contribution as it appears in the document — e.g. 'Basic Non-Elective Company Contribution', 'Safe Harbor', 'Profit Sharing'. Use the document's exact terminology, not a code-like slug."
        }
    },

    "vesting": {
        "queries": [
            "vesting schedule cliff graded years of service percentage employer contributions",
            "vesting requirements immediate vesting triggers employee contributions"
        ],
        "schema": {
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
            }
        }
    },

    "eligibility": {
        "queries": [
            "eligibility requirements age service hours entry date enrollment",
            "participation requirements eligible employee class exclusion",
            "plan entry date first day of month hire date when employee can join"
        ],
        "schema": {
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
        },
        "field_constraints": {
            "min_value":   "Always return as a number (integer or float), never as a string. Example: 18 not '18'.",
            "entry_dates": "Return the specific entry date rule as stated — e.g. 'First day of work (hire date)', 'First day of the month following hire date', 'January 1 or July 1'. Do not return null if any entry date language is present in the passages."
        }
    }

}


# ── Extraction Prompt ─────────────────────────────────────────────────────────

TARGETED_EXTRACTION_PROMPT = """You are extracting specific structured facts from a retirement plan SPD (Summary Plan Description).

Plan: {plan_name}
Extracting: {field_group}

Rules:
- Only extract what is explicitly stated in the passages below. Do not infer or guess.
- If a field is not mentioned in the passages, set it to null.
- For tiers and conditions arrays: populate only if data is present, otherwise use empty list [].
- Always populate formula_readable with plain English describing the rule as stated.
- Return ONLY valid JSON matching the schema exactly — no explanation, no markdown fences.
{field_constraints}
Schema to fill:
{schema}

Retrieved passages:
{passages}"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _deduplicate_chunks(results: list[dict]) -> list[dict]:
    """
    Deduplicate retrieval results by chunk_id.
    When the same chunk appears from multiple queries, keep the
    highest-score occurrence (results are pre-sorted by score desc).
    """
    seen    = set()
    unique  = []

    # Sort by score descending so highest-score version is kept
    for r in sorted(results, key=lambda x: x["score"], reverse=True):
        if r["chunk_id"] not in seen:
            seen.add(r["chunk_id"])
            unique.append(r)

    return unique


def _chunks_to_text(results: list[dict]) -> str:
    """
    Build clean passage text from retrieval results.
    Includes section headers for context.
    No rank/score labels — cleaner for extraction than format_results_for_claude().
    """
    parts           = []
    current_section = None

    # Sort by page number for natural document order
    sorted_results = sorted(
        results,
        key=lambda x: (x["metadata"].get("page_num") or 0)
    )

    for r in sorted_results:
        meta    = r["metadata"]
        section = meta.get("section", "")
        text    = r["text"].strip()

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

    if text.startswith("```"):
        text = re.sub(r'^```[a-z]*\n?', '', text)
        text = re.sub(r'\n?```$',       '', text)
        text = text.strip()

    return json.loads(text)


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
        return a if len(a) >= len(b) else b
    # Scalar — a wins (first non-null across groups)
    return a


def _merge_facts(results: list[dict]) -> dict:
    """Merge a list of per-group extraction results into one plan_rules dict."""
    if not results:
        return {}
    merged = results[0]
    for result in results[1:]:
        merged = _merge_two(merged, result)
    return merged


# ── Group Extraction ──────────────────────────────────────────────────────────

def _extract_group(
    client:       anthropic.Anthropic,
    group_name:   str,
    group_config: dict,
    plan_id:      str,
    plan_name:    str,
    group_num:    int,
    total_groups: int
) -> tuple[dict, dict]:
    """
    Extract facts for one field group.

    Steps:
      1. Run all queries for this group against VectorDB
      2. Deduplicate results by chunk_id
      3. Build passage text
      4. Call Claude with the group's sub-schema and passages
      5. Track token usage

    Returns:
        (extracted_dict, stats_dict)
    """
    # 1. Run all queries, collect results
    all_results = []
    for query in group_config["queries"]:
        results = retrieve_from_plan(
            query=query,
            plan_id=plan_id,
            top_k=TOP_K_PER_QUERY
        )
        all_results.extend(results)

    # 2. Deduplicate
    unique_chunks = _deduplicate_chunks(all_results)

    # 3. Build passage text
    passage_text = _chunks_to_text(unique_chunks)

    if not passage_text.strip():
        print(f"  [{group_num}/{total_groups}] {group_name:<25} →  0 chunks   ✗ (no results)")
        return {}, {
            "input_tokens":  0,
            "output_tokens": 0,
            "chunks_used":   0,
            "skipped":       True
        }

    # 4. Build field-specific constraints section (if any defined for this group)
    constraints_dict = group_config.get("field_constraints", {})
    if constraints_dict:
        lines = "\n".join(
            f"  - {field}: {instruction}"
            for field, instruction in constraints_dict.items()
        )
        field_constraints = f"\nField-specific instructions:\n{lines}\n"
    else:
        field_constraints = ""

    # 5. Call Claude
    prompt = TARGETED_EXTRACTION_PROMPT.format(
        plan_name=plan_name,
        field_group=group_name,
        schema=json.dumps(group_config["schema"], indent=2),
        field_constraints=field_constraints,
        passages=passage_text
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}]
    )

    response_text = response.content[0].text

    # 6. Track tokens
    stats = {
        "input_tokens":  response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "chunks_used":   len(unique_chunks),
        "skipped":       False
    }

    print(
        f"  [{group_num}/{total_groups}] {group_name:<25} "
        f"→ {len(unique_chunks):2d} unique chunks   ✓  "
        f"(input: {response.usage.input_tokens:,} | "
        f"output: {response.usage.output_tokens:,})"
    )

    try:
        extracted = _parse_json_response(response_text)
    except Exception as e:
        print(f"    Warning: JSON parse failed for {group_name} — {e}")
        extracted = {}

    return extracted, stats


# ── Main Extraction Function ──────────────────────────────────────────────────

def extract_plan_facts_v2(
    plan_id:   str,
    plan_name: str,
    doc_id:    str
) -> tuple[dict, dict]:
    """
    Extract structured plan facts using targeted vector search (V2).

    Instead of reading all document chunks in batches (V1), this function:
      - Runs 1–3 vector search queries per schema field group
      - Retrieves only the most relevant chunks for that group
      - Calls Claude once per group with a targeted sub-schema
      - Tracks exact token usage per group via response.usage

    No rate-limit delays needed — each call is small (~2–4k tokens).

    Args:
        plan_id:   Plan ID to search in VectorDB
        plan_name: Used as context in the extraction prompt
        doc_id:    Stored in output for traceability

    Returns:
        Tuple of:
          plan_rules  — same schema as V1 (compatible for comparison)
          token_stats — detailed per-group token usage
    """
    total_groups = len(FIELD_QUERIES)

    print(f"Extracting plan facts (V2 — targeted vector search)...")
    print(f"  Plan:   {plan_name} ({plan_id})")
    print(f"  Groups: {total_groups}\n")

    # Set up Claude client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=api_key)

    # Process each field group
    group_results = []
    token_stats   = {
        "extractor":           "v2",
        "total_input_tokens":  0,
        "total_output_tokens": 0,
        "total_llm_calls":     0,
        "by_group":            {}
    }

    start_time = time.time()

    for i, (group_name, group_config) in enumerate(FIELD_QUERIES.items(), start=1):
        extracted, stats = _extract_group(
            client=client,
            group_name=group_name,
            group_config=group_config,
            plan_id=plan_id,
            plan_name=plan_name,
            group_num=i,
            total_groups=total_groups
        )

        if extracted:
            group_results.append(extracted)

        # Accumulate token stats
        token_stats["by_group"][group_name]  = stats
        token_stats["total_input_tokens"]   += stats.get("input_tokens", 0)
        token_stats["total_output_tokens"]  += stats.get("output_tokens", 0)
        if not stats.get("skipped"):
            token_stats["total_llm_calls"] += 1

    elapsed = round(time.time() - start_time, 1)

    # Merge all group results into one plan_rules dict
    print(f"\n  Merging {len(group_results)} group result(s)...")
    plan_rules = _merge_facts(group_results)

    # Add traceability fields
    plan_rules["extracted_from_doc_id"] = doc_id
    plan_rules["extracted_at"]          = str(date.today())
    plan_rules["extractor_version"]     = "v2"

    token_stats["elapsed_seconds"] = elapsed

    # Print summary
    print(f"  Extraction complete.\n")
    print(f"  {'─' * 42}")
    print(f"  Token summary:")
    print(f"    Total input tokens:  {token_stats['total_input_tokens']:,}")
    print(f"    Total output tokens: {token_stats['total_output_tokens']:,}")
    print(f"    Total LLM calls:     {token_stats['total_llm_calls']}")
    print(f"    Elapsed:             {elapsed}s")
    print(f"  {'─' * 42}")

    return plan_rules, token_stats


# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

    from app.registry.registry import list_plans

    plans = list_plans()
    if not plans:
        print("No plans in registry. Ingest documents first.")
        sys.exit(1)

    # Run on first plan by default
    plan  = plans[0]
    doc   = next((d for d in plan.get("documents", []) if d["doc_type"] == "SPD"), None)

    if not doc:
        print(f"No SPD found for {plan['plan_name']}")
        sys.exit(1)

    print(f"Testing fact_extractor_v2 on: {plan['plan_name']}\n")

    plan_rules, token_stats = extract_plan_facts_v2(
        plan_id=plan["plan_id"],
        plan_name=plan["plan_name"],
        doc_id=doc["doc_id"]
    )

    print("\nExtracted plan_rules (first 1000 chars):")
    print(json.dumps(plan_rules, indent=2)[:1000])

    print("\nToken stats:")
    print(json.dumps(token_stats, indent=2))
