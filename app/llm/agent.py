import os
import re
import anthropic
from dotenv import load_dotenv

load_dotenv()

from app.llm.prompts import (
    SYSTEM_PROMPT,
    build_plan_context_prompt
)
from app.llm.tools import TOOL_DEFINITIONS, execute_tool


# ── Constants ─────────────────────────────────────────────────────────────────

MAX_TOOL_CALLS = 5
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096


# ── Claude Client ─────────────────────────────────────────────────────────────

_client = None

def get_client() -> anthropic.Anthropic:
    """Get or create Anthropic client."""
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable not set. "
                "Set it with: export ANTHROPIC_API_KEY=your_key"
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


# ── Response Parsing ──────────────────────────────────────────────────────────

def parse_structured_response(text: str) -> dict:
    """
    Parse Claude's structured response into components.
    Extracts ANSWER, SOURCES, CONFIDENCE, CONFIDENCE REASON.
    """
    result = {
        "answer":            "",
        "sources":           [],
        "confidence":        "Low",
        "confidence_reason": "",
        "raw":               text
    }

    # Extract ANSWER
    answer_match = re.search(
        r'ANSWER:\s*(.*?)(?=SOURCES:|CONFIDENCE:|$)',
        text, re.DOTALL | re.IGNORECASE
    )
    if answer_match:
        result["answer"] = answer_match.group(1).strip()

    # Extract SOURCES
    sources_match = re.search(
        r'SOURCES:\s*(.*?)(?=CONFIDENCE:|$)',
        text, re.DOTALL | re.IGNORECASE
    )
    if sources_match:
        sources_text = sources_match.group(1).strip()
        if sources_text.lower() not in ["none", "none found", ""]:
            # Split by newlines and clean up
            sources = [
                s.strip() for s in sources_text.split('\n')
                if s.strip() and s.strip().lower() not in ["none", "-"]
            ]
            result["sources"] = sources

    # Extract CONFIDENCE
    confidence_match = re.search(
        r'CONFIDENCE:\s*(High|Medium|Low)',
        text, re.IGNORECASE
    )
    if confidence_match:
        result["confidence"] = confidence_match.group(1).capitalize()

    # Extract CONFIDENCE REASON
    reason_match = re.search(
        r'CONFIDENCE REASON:\s*(.*?)$',
        text, re.DOTALL | re.IGNORECASE
    )
    if reason_match:
        result["confidence_reason"] = reason_match.group(1).strip()

    # Handle empty answer gracefully
    if not result["answer"]:
        if text.strip():
            # Claude gave a response but didn't include the ANSWER: label —
            # use the raw text rather than discarding a real answer.
            result["answer"] = text.strip()
        else:
            result["answer"] = (
                "I could not find sufficiently relevant information "
                "in the available documents to answer this question confidently.\n\n"
                "Possible reasons:\n"
                "• The relevant document may not have been ingested yet\n"
                "• Try rephrasing with more specific terms\n"
                "• If asking about a specific plan, select that plan as context"
            )
            result["confidence"] = "Low"

    return result


# ── Agentic Loop ──────────────────────────────────────────────────────────────

def run_agent(
    question: str,
    plan_id: str | None = None,
    plan_name: str | None = None,
    verbose: bool = False
) -> dict:
    """
    Run the full agentic RAG loop.

    Args:
        question:  User's question
        plan_id:   Optional plan context
        plan_name: Optional plan name for display
        verbose:   Print tool calls and reasoning

    Returns:
        {
            "answer":            str,
            "sources":           list,
            "confidence":        str,
            "confidence_reason": str,
            "tool_calls_made":   int,
            "raw_response":      str
        }
    """
    client = get_client()

    # Build initial messages
    plan_context = build_plan_context_prompt(plan_id, plan_name)

    messages = [
        {
            "role": "user",
            "content": f"[CONTEXT]\n{plan_context}"
        },
        {
            "role": "assistant",
            "content": (
                "Understood. I'll use the appropriate retrieval tools "
                "based on the plan context provided."
            )
        },
        {
            "role": "user",
            "content": question
        }
    ]

    tool_calls_made       = 0
    final_response        = ""
    all_retrieval_scores  = []   # collects cosine scores across all retrieval calls
    total_input_tokens    = 0
    total_output_tokens   = 0

    if verbose:
        print(f"\n{'='*60}")
        print(f"Question: {question}")
        if plan_id:
            print(f"Plan context: {plan_name} ({plan_id})")
        print(f"{'='*60}")

    # Agentic loop
    while tool_calls_made <= MAX_TOOL_CALLS:

        # Call Claude
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages
        )

        total_input_tokens  += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        # Check stop reason
        stop_reason = response.stop_reason

        if verbose:
            print(f"\nClaude stop reason: {stop_reason}")

        # ── Tool use ──
        if stop_reason == "tool_use":
            # Add Claude's response to messages
            messages.append({
                "role": "assistant",
                "content": response.content
            })

            # Process all tool calls in this response
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    tool_calls_made += 1

                    if verbose:
                        print(
                            f"\nTool call {tool_calls_made}: "
                            f"{block.name}({block.input})"
                        )

                    # Hard cap check
                    if tool_calls_made > MAX_TOOL_CALLS:
                        if verbose:
                            print(
                                f"  Hard cap reached ({MAX_TOOL_CALLS}). "
                                f"Forcing answer."
                            )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": (
                                "Maximum tool calls reached. "
                                "Please synthesize an answer "
                                "from the context already retrieved."
                            )
                        })
                        continue

                    # Execute tool — returns (text_for_claude, retrieval_scores)
                    tool_text, tool_scores = execute_tool(block.name, block.input)
                    all_retrieval_scores.extend(tool_scores)

                    if verbose:
                        print(f"  Result preview: {tool_text[:150]}...")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": tool_text
                    })

            # Add tool results to messages
            messages.append({
                "role": "user",
                "content": tool_results
            })

            # If hard cap reached — force final answer
            if tool_calls_made >= MAX_TOOL_CALLS:
                messages.append({
                    "role": "user",
                    "content": (
                        "You have reached the maximum number of tool calls. "
                        "Please provide your final structured answer now "
                        "using only the context already retrieved."
                    )
                })

        # ── End turn — Claude gave final answer ──
        elif stop_reason == "end_turn":
            # Extract text from response
            for block in response.content:
                if hasattr(block, "text"):
                    final_response = block.text
                    break

            if verbose:
                print(f"\nFinal response received.")
                print(f"Tool calls made: {tool_calls_made}")

            break

        else:
            # Unexpected stop reason
            if verbose:
                print(f"Unexpected stop reason: {stop_reason}")
            break

    # ── Compute score-based confidence ────────────────────────────────────────
    if all_retrieval_scores:
        avg = sum(all_retrieval_scores) / len(all_retrieval_scores)
        confidence_pct   = round(avg * 100)
        confidence_label = (
            "High"   if avg >= 0.75 else
            "Medium" if avg >= 0.35 else
            "Low"
        )
    else:
        confidence_pct   = 0
        confidence_label = "Low"

    # Parse structured response, then override confidence with score-based value
    parsed = parse_structured_response(final_response)
    parsed["confidence"]      = confidence_label
    parsed["confidence_pct"]  = confidence_pct
    parsed["tool_calls_made"] = tool_calls_made
    parsed["input_tokens"]    = total_input_tokens
    parsed["output_tokens"]   = total_output_tokens
    parsed.pop("confidence_reason", None)   # no longer from Claude

    return parsed


# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing agent...\n")
    print("Make sure ANTHROPIC_API_KEY is set.\n")

    # Test 1 — Plan specific question
    print("=" * 60)
    print("TEST 1: Plan-specific question")
    print("=" * 60)

    result = run_agent(
        question="What is the employer matching contribution percentage?",
        plan_id="PLAN_001",
        plan_name="Microsoft Retirement Plan",
        verbose=True
    )

    print(f"\nANSWER:\n{result['answer']}")
    print(f"\nSOURCES:")
    for s in result["sources"]:
        print(f"  {s}")
    print(f"\nCONFIDENCE: {result['confidence_pct']}%  ({result['confidence']})")
    print(f"Tool calls made: {result['tool_calls_made']}")

    # Test 2 — Generic question
    print("\n" + "=" * 60)
    print("TEST 2: Generic regulatory question")
    print("=" * 60)

    result = run_agent(
        question="What are the general 401k contribution rules?",
        plan_id=None,
        plan_name=None,
        verbose=True
    )

    print(f"\nANSWER:\n{result['answer']}")
    print(f"\nCONFIDENCE: {result['confidence_pct']}%  ({result['confidence']})")
    print(f"Tool calls made: {result['tool_calls_made']}")