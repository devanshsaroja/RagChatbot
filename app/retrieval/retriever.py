import os
from app.storage.vector_store import (
    get_collection,
    get_model,
    embed_texts
)


# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_TOP_K = 10
MIN_SCORE_THRESHOLD = 0.0


# ── Core Search ───────────────────────────────────────────────────────────────

def search(
    query: str,
    where_filter: dict,
    top_k: int = DEFAULT_TOP_K
) -> list[dict]:
    """
    Core search function.
    Embeds query, searches ChromaDB with filter, returns ranked results.

    Args:
        query:        The search query text
        where_filter: ChromaDB metadata filter
        top_k:        Number of results to return

    Returns:
        List of result dicts with text, score, metadata
    """
    collection = get_collection()

    # Embed the query
    query_embedding = embed_texts([query])[0]

    # Search ChromaDB
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where=where_filter,
        include=["documents", "metadatas", "distances"]
    )

    # ChromaDB returns distance (lower = more similar)
    # Convert to similarity score (higher = more similar)
    formatted = []
    ids = results["ids"][0]
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for i, (doc_id, text, metadata, distance) in enumerate(
        zip(ids, documents, metadatas, distances)
    ):
        # Convert cosine distance to similarity score
        # ChromaDB cosine distance: 0 = identical, 2 = opposite
        # Similarity: 1 = identical, -1 = opposite
        score = 1 - distance

        # Filter low relevance results
        if score < MIN_SCORE_THRESHOLD:
            continue

        formatted.append({
            "rank":     i + 1,
            "score":    round(score, 4),
            "text":     text,
            "metadata": metadata,
            "chunk_id": doc_id
        })

    return formatted


# ── Public Retrieval Functions ────────────────────────────────────────────────

def retrieve_from_plan(
    query: str,
    plan_id: str,
    top_k: int = DEFAULT_TOP_K
) -> list[dict]:
    """
    Search plan-specific chunks only.
    Used when question is about a specific plan's rules.

    Args:
        query:   The question or search query
        plan_id: Which plan to search (e.g. "PLAN_001")
        top_k:   Number of results to return

    Returns:
        Ranked list of relevant chunks from this plan
    """
    where_filter = {
        "$and": [
            {"tier":    {"$eq": "plan_doc"}},
            {"plan_id": {"$eq": plan_id}}
        ]
    }

    results = search(query, where_filter, top_k)
    return results


def retrieve_generic(
    query: str,
    top_k: int = DEFAULT_TOP_K
) -> list[dict]:
    """
    Search generic/regulatory chunks only.
    Used for ERISA rules, IRS limits, general concepts.

    Args:
        query: The question or search query
        top_k: Number of results to return

    Returns:
        Ranked list of relevant chunks from generic docs
    """
    where_filter = {"tier": {"$eq": "generic"}}
    results = search(query, where_filter, top_k)
    return results


def retrieve_all(
    query: str,
    top_k: int = DEFAULT_TOP_K
) -> list[dict]:
    """
    Search across all chunks — both plan and generic.
    Used when no specific plan context is provided.
    Results re-ranked by score.
    """
    collection = get_collection()
    query_embedding = embed_texts([query])[0]

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"]
    )

    formatted = []
    ids = results["ids"][0]
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for i, (doc_id, text, metadata, distance) in enumerate(
        zip(ids, documents, metadatas, distances)
    ):
        score = 1 - distance
        if score < MIN_SCORE_THRESHOLD:
            continue
        formatted.append({
            "rank":     i + 1,
            "score":    round(score, 4),
            "text":     text,
            "metadata": metadata,
            "chunk_id": doc_id
        })

    # Re-rank by score
    formatted.sort(key=lambda x: x["score"], reverse=True)
    for i, r in enumerate(formatted):
        r["rank"] = i + 1

    return formatted


# ── Result Formatting ─────────────────────────────────────────────────────────

def format_results_for_claude(results: list[dict]) -> str:
    """
    Format retrieval results into clean text for Claude.
    Each chunk clearly labeled with source and score.
    """
    if not results:
        return "No relevant chunks found."

    formatted_parts = []
    for r in results:
        meta = r["metadata"]

        # Build source citation
        source_parts = []
        if meta.get("plan_name"):
            source_parts.append(meta["plan_name"])
        if meta.get("doc_type"):
            source_parts.append(meta["doc_type"])
        if meta.get("section"):
            source_parts.append(f"Section: {meta['section']}")
        if meta.get("subsection"):
            source_parts.append(f"Subsection: {meta['subsection']}")
        if meta.get("page_num"):
            source_parts.append(f"Page {meta['page_num']}")

        source = " | ".join(source_parts)

        formatted_parts.append(
            f"[CHUNK {r['rank']} | Score: {r['score']} | {source}]\n"
            f"{r['text']}\n"
        )

    return "\n---\n".join(formatted_parts)


# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing retriever...\n")

    # Test 1 — Plan specific query
    print("=" * 60)
    print("TEST 1: Plan-specific query")
    print("Query: 'What are the eligibility requirements?'")
    print("=" * 60)

    results = retrieve_from_plan(
        query="What are the eligibility requirements?",
        plan_id="PLAN_001",
        top_k=3
    )

    for r in results:
        print(f"\nRank {r['rank']} | Score: {r['score']}")
        print(f"Section:    {r['metadata'].get('section', '')}")
        print(f"Subsection: {r['metadata'].get('subsection', '')}")
        print(f"Page:       {r['metadata'].get('page_num', '')}")
        print(f"Text: {r['text'][:200]}...")

    # Test 2 — Generic query
    print("\n" + "=" * 60)
    print("TEST 2: Generic query")
    print("Query: 'What are the 401k contribution limits?'")
    print("=" * 60)

    results = retrieve_generic(
        query="What are the 401k contribution limits?",
        top_k=3
    )

    if results:
        for r in results:
            print(f"\nRank {r['rank']} | Score: {r['score']}")
            print(f"Section:    {r['metadata'].get('section', '')}")
            print(f"Text: {r['text'][:200]}...")
    else:
        print("No generic chunks found.")
        print("(Expected — only plan chunks stored so far)")

    # Test 3 — Format for Claude
    print("\n" + "=" * 60)
    print("TEST 3: Formatted output for Claude")
    print("=" * 60)

    results = retrieve_from_plan(
        query="What is the employer match?",
        plan_id="PLAN_001",
        top_k=2
    )
    formatted = format_results_for_claude(results)
    print(formatted)