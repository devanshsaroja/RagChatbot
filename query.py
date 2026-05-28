import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.registry.registry import list_plans, get_registry_summary
from app.llm.agent import run_agent


# ── Display Helpers ───────────────────────────────────────────────────────────

def print_header():
    print("\n" + "═" * 52)
    print("   Retirement Plan RAG — Query Interface")
    print("═" * 52)


def print_section(title: str):
    print(f"\n── {title} {'─' * (46 - len(title))}")


def _render_sources_table(sources: list[str]) -> str:
    """
    Parse pipe-delimited source citations and render as an aligned terminal table.
    Expected format per line: Doc Type | Plan Name | Section | Page
    Lines that are not pipe-delimited (e.g. markdown table separators) are skipped.
    """
    rows = []
    for s in sources:
        stripped = s.strip()
        # Skip markdown table separator lines (e.g. |---|---|)
        if stripped and set(stripped) <= set("|-: "):
            continue
        if "|" in stripped:
            parts = [p.strip() for p in stripped.split("|") if p.strip()]
            if parts:
                while len(parts) < 4:
                    parts.append("")
                rows.append(parts[:4])

    if not rows:
        return ""

    headers  = ["Type", "Plan", "Section", "Page"]
    col_w    = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_w[i] = max(col_w[i], len(cell))

    sep = "  " + "─" * (sum(col_w) + 3 * (len(headers) - 1) + 2)
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in col_w)

    lines = [sep, fmt.format(*headers), sep]
    for row in rows:
        lines.append(fmt.format(*row))
    lines.append(sep)

    return "\n".join(lines)


def print_answer(result: dict):
    """Display formatted answer from agent."""
    print("\n" + "═" * 52)

    # Answer
    print("\nANSWER:")
    print("-" * 52)
    print(result["answer"])

    # Sources — rendered as aligned table
    if result["sources"]:
        print("\nSOURCES:")
        print("-" * 52)
        table = _render_sources_table(result["sources"])
        if table:
            print(table)
        else:
            # Fallback: plain bullets if parsing produced nothing
            for source in result["sources"]:
                print(f"  • {source}")

    # Confidence — score-based percentage
    confidence_pct   = result.get("confidence_pct", 0)
    confidence_label = result.get("confidence", "Low")
    icons = {"High": "🟢", "Medium": "🟡", "Low": "🔴"}
    icon  = icons.get(confidence_label, "🔴")

    print(f"\nCONFIDENCE: {confidence_pct}%  {icon}  ({confidence_label})")

    # Tool calls
    print(f"Retrieval calls made: {result['tool_calls_made']}")
    print("═" * 52)


# ── Plan Selection ────────────────────────────────────────────────────────────

def select_plan() -> tuple[str | None, str | None]:
    """
    Show available plans and let user select one.
    Returns (plan_id, plan_name) or (None, None) for no plan context.
    """
    print_section("Plan Context")

    plans = list_plans()

    if not plans:
        print("  No plans found in registry.")
        print("  Run ingest.py first to add documents.")
        print("  Continuing without plan context...")
        return None, None

    print("\n  Select a plan context for your questions:")
    print("  (This helps Claude search the right documents)\n")

    for i, plan in enumerate(plans, start=1):
        doc_count = len(plan.get("documents", []))
        print(
            f"  [{i}] {plan['plan_name']}\n"
            f"      Employer: {plan['employer_name']} | "
            f"Documents: {doc_count}"
        )

    print(f"  [G] Ask about general regulations only")
    print(f"  [A] No specific context — search everything")

    while True:
        choice = input("\n  Enter choice: ").strip().upper()

        if choice == "G":
            print("  ✓ General regulatory mode selected")
            return None, None

        if choice == "A":
            print("  ✓ No specific context — searching all documents")
            return None, None

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(plans):
                selected = plans[idx]
                print(
                    f"  ✓ Selected: {selected['plan_name']} "
                    f"({selected['plan_id']})"
                )
                return selected["plan_id"], selected["plan_name"]
        except ValueError:
            pass

        print(f"  Invalid choice. Enter 1-{len(plans)}, G, or A.")


# ── Question Loop ─────────────────────────────────────────────────────────────

def question_loop(plan_id: str | None, plan_name: str | None):
    """
    Main question-answer loop.
    Continues until user types 'exit' or 'quit'.
    """
    print_section("Ask Questions")

    if plan_id:
        print(f"  Context: {plan_name} ({plan_id})")
    else:
        print("  Context: General / No specific plan")

    print("\n  Type your question and press Enter.")
    print("  Commands: 'exit' to quit | 'switch' to change plan | 'clear' to clear screen\n")

    while True:
        try:
            question = input("  Question: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Exiting...")
            break

        if not question:
            continue

        # Commands
        if question.lower() in ["exit", "quit", "q"]:
            print("\n  Goodbye!")
            break

        if question.lower() == "switch":
            return "switch"

        if question.lower() == "clear":
            os.system("cls" if os.name == "nt" else "clear")
            continue

        if question.lower() == "help":
            print("\n  Commands:")
            print("  exit/quit  — Exit the program")
            print("  switch     — Switch to a different plan")
            print("  clear      — Clear the screen")
            print("  help       — Show this help\n")
            continue

        # Run agent
        print("\n  Thinking...\n")
        try:
            result = run_agent(
                question=question,
                plan_id=plan_id,
                plan_name=plan_name,
                verbose=False
            )
            print_answer(result)

        except Exception as e:
            print(f"\n  Error: {e}")
            print("  Please try again.\n")

    return "exit"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print_header()

    # Show registry summary
    summary = get_registry_summary()
    print(f"\n  Documents loaded:")
    print(f"  Plans:             {summary['plans']}")
    print(f"  Plan documents:    {summary['plan_documents']}")
    print(f"  Generic documents: {summary['generic_documents']}")
    print(f"  Total chunks:      {summary['total_chunks']}")

    if summary["total_chunks"] == 0:
        print("\n  ⚠ No documents indexed yet.")
        print("  Run ingest.py first to add documents.")
        return

    # Main loop — allows switching plans
    while True:
        plan_id, plan_name = select_plan()
        result = question_loop(plan_id, plan_name)

        if result == "exit":
            break
        # if result == "switch" → loop back to plan selection

    print("\n" + "═" * 52)
    print("   Session ended.")
    print("═" * 52 + "\n")


if __name__ == "__main__":
    main()