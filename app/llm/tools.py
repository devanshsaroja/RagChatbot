from app.retrieval.retriever import (
    retrieve_from_plan,
    retrieve_generic,
    retrieve_all,
    format_results_for_claude
)
from app.registry.registry import (
    list_plans,
    list_generic_documents,
    get_registry_summary,
    get_plan_rules
)


# ── Tool Definitions for Claude API ──────────────────────────────────────────
# These are passed to Claude so it knows what tools are available
# and when/how to call them.

TOOL_DEFINITIONS = [
    {
        "name": "retrieve_from_plan",
        "description": (
            "Search for information in a specific retirement plan's documents. "
            "Use this when the question is about a specific plan's rules — "
            "eligibility, vesting, contributions, distributions, loans, etc. "
            "Returns the most relevant chunks from that plan's documents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search query. Be specific — use key terms "
                        "from the question. Example: 'employer match "
                        "percentage' not just 'match'."
                    )
                },
                "plan_id": {
                    "type": "string",
                    "description": (
                        "The plan ID to search. Example: 'PLAN_001'. "
                        "Use enumerate_plans if you don't know the plan ID."
                    )
                },
                "top_k": {
                    "type": "integer",
                    "description": (
                        "Number of results to return. Default 8. "
                        "Use 4-5 for focused questions, 8 for broad topics."
                    ),
                    "default": 8
                }
            },
            "required": ["query", "plan_id"]
        }
    },
    {
        "name": "retrieve_generic",
        "description": (
            "Search general retirement plan regulations and IRS rules. "
            "Use this for questions about ERISA requirements, IRS contribution "
            "limits, regulatory compliance, or general 401(k) concepts that "
            "apply across all plans — not specific to one plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search query for general regulatory information. "
                        "Example: 'IRS 401k contribution limit 2025' or "
                        "'ERISA vesting requirements'."
                    )
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return. Default 8.",
                    "default": 8
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "retrieve_all",
        "description": (
            "Search across ALL documents simultaneously — both plan-specific "
            "and general/regulatory. Use this when no specific plan has been "
            "selected, or when the question could span multiple plans and "
            "regulatory sources. More efficient than calling retrieve_from_plan "
            "and retrieve_generic separately. Results are ranked by relevance "
            "across the entire document corpus."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search query. Be specific — use key terms "
                        "from the question. Example: 'vesting schedule "
                        "employer match' or '401k contribution limits 2025'."
                    )
                },
                "top_k": {
                    "type": "integer",
                    "description": (
                        "Number of results to return. Default 8. "
                        "Use 10-12 for broad cross-plan questions."
                    ),
                    "default": 8
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "enumerate_plans",
        "description": (
            "List all available retirement plans and documents in the system. "
            "Use this when: the user asks what plans are available, "
            "you need to find a plan_id, or you want to show "
            "what information is available before answering."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_plan_facts",
        "description": (
            "Retrieve pre-extracted structured facts for a specific plan — "
            "employer match formula, vesting schedule, eligibility rules, "
            "plan features. Use this FIRST for simple factual questions about "
            "a known plan before falling back to vector search. "
            "Returns null if facts have not been extracted yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "string",
                    "description": (
                        "The plan ID to look up. Example: 'PLAN_001'."
                    )
                }
            },
            "required": ["plan_id"]
        }
    }
]


# ── Tool Execution ────────────────────────────────────────────────────────────

def execute_tool(tool_name: str, tool_input: dict) -> tuple[str, list[float]]:
    """
    Execute a tool call from Claude and return formatted result + retrieval scores.

    Args:
        tool_name:  Name of the tool Claude wants to call
        tool_input: Arguments Claude provided

    Returns:
        Tuple of:
          text   — formatted string result to send back to Claude
          scores — list of cosine similarity scores from retrieval (empty for non-retrieval tools)
    """

    if tool_name == "retrieve_from_plan":
        query   = tool_input.get("query", "")
        plan_id = tool_input.get("plan_id", "")
        top_k   = tool_input.get("top_k", 8)

        if not query or not plan_id:
            return "Error: Both 'query' and 'plan_id' are required.", []

        results = retrieve_from_plan(query=query, plan_id=plan_id, top_k=top_k)
        scores  = [r["score"] for r in results]
        return format_results_for_claude(results), scores

    elif tool_name == "retrieve_generic":
        query = tool_input.get("query", "")
        top_k = tool_input.get("top_k", 8)

        if not query:
            return "Error: 'query' is required.", []

        results = retrieve_generic(query=query, top_k=top_k)
        scores  = [r["score"] for r in results]
        return format_results_for_claude(results), scores

    elif tool_name == "retrieve_all":
        query = tool_input.get("query", "")
        top_k = tool_input.get("top_k", 8)

        if not query:
            return "Error: 'query' is required.", []

        results = retrieve_all(query=query, top_k=top_k)
        scores  = [r["score"] for r in results]
        return format_results_for_claude(results), scores

    elif tool_name == "enumerate_plans":
        return _enumerate_plans(), []

    elif tool_name == "get_plan_facts":
        plan_id = tool_input.get("plan_id", "")
        if not plan_id:
            return "Error: 'plan_id' is required.", []
        result = _get_plan_facts(plan_id)
        # Structured facts are pre-verified — treat as highest-confidence source.
        # Only assign a score when facts were actually found (not a "not found" response).
        score = [1.0] if result.startswith("Pre-extracted plan facts for") else []
        return result, score

    else:
        return f"Error: Unknown tool '{tool_name}'.", []


def _enumerate_plans() -> str:
    """
    Build a formatted list of all plans and documents.
    Returned to Claude so it knows what's available.
    """
    summary = get_registry_summary()
    plans = list_plans()
    generic_docs = list_generic_documents()

    if not plans and not generic_docs:
        return (
            "No plans or documents are currently loaded in the system. "
            "Please ingest documents first using ingest.py."
        )

    result = "Available plans and documents in the system:\n\n"

    # Plans
    if plans:
        result += "RETIREMENT PLANS:\n"
        for plan in plans:
            result += (
                f"  Plan ID:   {plan['plan_id']}\n"
                f"  Plan Name: {plan['plan_name']}\n"
                f"  Employer:  {plan['employer_name']}\n"
            )
            if plan.get("documents"):
                result += "  Documents:\n"
                for doc in plan["documents"]:
                    result += (
                        f"    - {doc['doc_type']}: {doc['filename']} "
                        f"({doc['chunk_count']} chunks)\n"
                    )
            result += "\n"

    # Generic documents
    if generic_docs:
        result += "GENERAL / REGULATORY DOCUMENTS:\n"
        for doc in generic_docs:
            result += (
                f"  - {doc['doc_type']}: {doc['filename']} "
                f"({doc['chunk_count']} chunks)\n"
            )

    result += (
        f"\nSummary: {summary['plans']} plan(s), "
        f"{summary['plan_documents']} plan document(s), "
        f"{summary['generic_documents']} generic document(s), "
        f"{summary['total_chunks']} total chunks indexed."
    )

    return result


def _get_plan_facts(plan_id: str) -> str:
    """
    Return pre-extracted plan facts as a formatted JSON string.
    Called when Claude uses the get_plan_facts tool.
    """
    import json
    plan_rules = get_plan_rules(plan_id)

    if plan_rules is None:
        return (
            f"No pre-extracted facts found for plan {plan_id}. "
            "Facts may not have been extracted yet. "
            "Use retrieve_from_plan to search the document instead."
        )

    return (
        f"Pre-extracted plan facts for {plan_id}:\n\n"
        + json.dumps(plan_rules, indent=2)
    )


# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing tools...\n")

    # Test 1 — enumerate_plans
    print("=" * 60)
    print("TEST 1: enumerate_plans")
    print("=" * 60)
    result = execute_tool("enumerate_plans", {})
    print(result)

    # Test 2 — retrieve_from_plan
    print("\n" + "=" * 60)
    print("TEST 2: retrieve_from_plan")
    print("=" * 60)
    result = execute_tool("retrieve_from_plan", {
        "query": "What is the employer matching contribution?",
        "plan_id": "PLAN_001",
        "top_k": 2
    })
    print(result[:500] + "...")

    # Test 3 — retrieve_generic
    print("\n" + "=" * 60)
    print("TEST 3: retrieve_generic")
    print("=" * 60)
    result = execute_tool("retrieve_generic", {
        "query": "401k contribution limits",
        "top_k": 2
    })
    print(result[:500] + "...")

    # Test 4 — unknown tool
    print("\n" + "=" * 60)
    print("TEST 4: unknown tool")
    print("=" * 60)
    result = execute_tool("unknown_tool", {})
    print(result)

    print("\nTool definitions count:", len(TOOL_DEFINITIONS))
    print("Tool names:", [t["name"] for t in TOOL_DEFINITIONS])