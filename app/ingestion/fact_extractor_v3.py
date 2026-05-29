"""
fact_extractor_v3.py — Dynamic Query Generation Extractor
──────────────────────────────────────────────────────────
Extends V2 with a Phase 1 that dynamically generates search queries using Claude
instead of relying on hardcoded static queries.

Difference from V2:
  V2: FIELD_QUERIES["queries"] are hardcoded strings per group
  V3: Phase 1 asks Claude to generate queries for all groups in one call,
      using the plan name as context so terminology adapts to the plan type
      (401k, 403b, profit sharing, SIMPLE, etc.)

Phase 1 — Query Generation (1 LLM call)
  Input:  plan name + full schema (group names + sub-schemas)
  Output: {group_name: ["query1", "query2", ...]}

Phase 2 — Retrieval + Extraction (6 calls, identical to V2)
  Uses generated queries; field_constraints from FIELD_QUERIES still applied

Phase 3 — Merge (identical to V2)

Returns: (plan_rules, token_stats)
  token_stats includes a query_generation section with exact token cost
  and the generated queries themselves for inspection.
"""

import os
import json
import time
from datetime import date

from dotenv import load_dotenv
load_dotenv()

import anthropic

# Reuse all helpers and config from V2 — no duplication
from app.ingestion.fact_extractor_v2 import (
    FIELD_QUERIES,
    MODEL,
    TOP_K_PER_QUERY,
    _deduplicate_chunks,
    _chunks_to_text,
    _parse_json_response,
    _merge_two,
    _merge_facts,
    _extract_group,
)


# ── Query Generation Prompt ───────────────────────────────────────────────────

QUERY_GENERATION_PROMPT = """You are a retrieval expert for retirement plan documents.

Plan: {plan_name}

Generate 2-3 optimal search queries for each field group below. These queries
will be used to retrieve the most relevant text chunks from this plan's SPD
(Summary Plan Description) document.

Rules:
- Use terminology likely found in this specific type of retirement plan document
- Cover synonyms and alternate phrasings for each concept
- Each query: 5-10 words, specific enough to retrieve targeted chunks
- Adapt your terminology to the plan type implied by the plan name
  (e.g. 401k uses "matching contribution", 403b may use "employer contribution")
- Return ONLY valid JSON with this exact structure:
  {{"group_name": ["query1", "query2", "query3"], ...}}
- Include every group in the output — no omissions

Field groups and their schemas:
{schema_groups}"""


# ── Phase 1 — Query Generation ────────────────────────────────────────────────

def _generate_queries(
    client:    anthropic.Anthropic,
    plan_name: str
) -> tuple[dict, dict]:
    """
    Ask Claude to generate search queries for all field groups in one call.

    Returns:
        generated  — {group_name: ["query1", "query2", ...]}
        stats      — {input_tokens, output_tokens}
    """
    # Build schema_groups: group_name → sub-schema only (no queries, no constraints)
    schema_groups = {
        group_name: config["schema"]
        for group_name, config in FIELD_QUERIES.items()
    }

    prompt = QUERY_GENERATION_PROMPT.format(
        plan_name=plan_name,
        schema_groups=json.dumps(schema_groups, indent=2)
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    stats = {
        "input_tokens":  response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens
    }

    try:
        generated = _parse_json_response(response.content[0].text)
    except Exception as e:
        raise ValueError(f"Query generation failed — Claude returned invalid JSON: {e}\n"
                         f"Response: {response.content[0].text[:500]}")

    # Validate all groups are present
    missing = [g for g in FIELD_QUERIES if g not in generated]
    if missing:
        raise ValueError(f"Query generation missing groups: {missing}")

    return generated, stats


# ── Main Extraction Function ──────────────────────────────────────────────────

def extract_plan_facts_v3(
    plan_id:   str,
    plan_name: str,
    doc_id:    str
) -> tuple[dict, dict]:
    """
    Extract structured plan facts using dynamic query generation (V3).

    Phase 1: One LLM call generates search queries for all field groups.
             Queries are tailored to the plan name / plan type.
    Phase 2: Same retrieval + extraction flow as V2, using generated queries.
    Phase 3: Merge group results into one plan_rules dict.

    Args:
        plan_id:   Plan ID to search in VectorDB
        plan_name: Used in query generation prompt and extraction prompt
        doc_id:    Stored in output for traceability

    Returns:
        Tuple of:
          plan_rules  — same schema as V1/V2 (compatible for comparison)
          token_stats — includes query_generation cost + per-group extraction cost
    """
    total_groups = len(FIELD_QUERIES)

    print(f"Extracting plan facts (V3 — dynamic query generation)...")
    print(f"  Plan:   {plan_name} ({plan_id})")
    print(f"  Groups: {total_groups}\n")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=api_key)

    token_stats = {
        "extractor":            "v3",
        "query_generation":     {},
        "total_input_tokens":   0,
        "total_output_tokens":  0,
        "total_llm_calls":      0,
        "by_group":             {}
    }

    start_time = time.time()

    # ── Phase 1: Generate queries ──────────────────────────────────────────────
    print("  Phase 1 — Generating queries...")
    generated_queries, gen_stats = _generate_queries(client, plan_name)

    token_stats["query_generation"] = {
        "input_tokens":    gen_stats["input_tokens"],
        "output_tokens":   gen_stats["output_tokens"],
        "generated_queries": generated_queries
    }
    token_stats["total_input_tokens"]  += gen_stats["input_tokens"]
    token_stats["total_output_tokens"] += gen_stats["output_tokens"]
    token_stats["total_llm_calls"]     += 1

    print(f"  Generated queries for {len(generated_queries)} groups "
          f"(input: {gen_stats['input_tokens']:,} | "
          f"output: {gen_stats['output_tokens']:,})\n")

    # Print generated queries for inspection
    for group_name, queries in generated_queries.items():
        print(f"  {group_name}:")
        for q in queries:
            print(f"    · {q}")
    print()

    # ── Phase 2: Retrieve + extract per group ──────────────────────────────────
    print("  Phase 2 — Retrieving and extracting per group...")
    group_results = []

    for i, (group_name, base_config) in enumerate(FIELD_QUERIES.items(), start=1):
        # Build group_config using generated queries but keeping field_constraints
        group_config = {
            **base_config,
            "queries": generated_queries.get(group_name, base_config["queries"])
        }

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

        token_stats["by_group"][group_name]  = stats
        token_stats["total_input_tokens"]   += stats.get("input_tokens", 0)
        token_stats["total_output_tokens"]  += stats.get("output_tokens", 0)
        if not stats.get("skipped"):
            token_stats["total_llm_calls"] += 1

    elapsed = round(time.time() - start_time, 1)

    # ── Phase 3: Merge ─────────────────────────────────────────────────────────
    print(f"\n  Merging {len(group_results)} group result(s)...")
    plan_rules = _merge_facts(group_results)

    plan_rules["extracted_from_doc_id"] = doc_id
    plan_rules["extracted_at"]          = str(date.today())
    plan_rules["extractor_version"]     = "v3"

    token_stats["elapsed_seconds"] = elapsed

    print(f"  Extraction complete.\n")
    print(f"  {'─' * 42}")
    print(f"  Token summary:")
    print(f"    Query generation:    {gen_stats['input_tokens']:,} in / {gen_stats['output_tokens']:,} out")
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

    plan = plans[0]
    doc  = next((d for d in plan.get("documents", []) if d["doc_type"] == "SPD"), None)

    if not doc:
        print(f"No SPD found for {plan['plan_name']}")
        sys.exit(1)

    print(f"Testing fact_extractor_v3 on: {plan['plan_name']}\n")

    plan_rules, token_stats = extract_plan_facts_v3(
        plan_id=plan["plan_id"],
        plan_name=plan["plan_name"],
        doc_id=doc["doc_id"]
    )

    print("\nExtracted plan_rules (first 1000 chars):")
    print(json.dumps(plan_rules, indent=2)[:1000])

    print("\nGenerated queries used:")
    for group, queries in token_stats["query_generation"]["generated_queries"].items():
        print(f"  {group}: {queries}")

    print(f"\nTotal tokens: {token_stats['total_input_tokens']:,} in / {token_stats['total_output_tokens']:,} out")
