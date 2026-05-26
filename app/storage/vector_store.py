import os
import chromadb
from chromadb.config import Settings

from app.embedding_model import get_model


# ── Constants ─────────────────────────────────────────────────────────────────

VECTORDB_PATH = os.path.join("data", "vectordb")
COLLECTION_NAME = "retirement_rag"
BATCH_SIZE = 32  # Process chunks in batches to avoid memory issues


# ── ChromaDB Client ───────────────────────────────────────────────────────────

_client = None

def get_client() -> chromadb.PersistentClient:
    """Get or create persistent ChromaDB client."""
    global _client
    if _client is None:
        os.makedirs(VECTORDB_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=VECTORDB_PATH,
            settings=Settings(anonymized_telemetry=False)
        )
    return _client


def get_collection() -> chromadb.Collection:
    """
    Get or create the main collection.
    Uses cosine similarity — matches our embedding model.
    """
    client = get_client()
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )
    return collection


# ── Metadata Sanitization ─────────────────────────────────────────────────────

def sanitize_metadata(metadata: dict) -> dict:
    """
    ChromaDB only supports str, int, float, bool in metadata.
    Convert None → "" and remove unsupported types.
    """
    sanitized = {}
    allowed_types = (str, int, float, bool)

    for key, value in metadata.items():
        if value is None:
            sanitized[key] = ""
        elif isinstance(value, allowed_types):
            sanitized[key] = value
        else:
            # Convert anything else to string
            sanitized[key] = str(value)

    return sanitized


# ── ID Generation ─────────────────────────────────────────────────────────────

def build_chunk_id(chunk: dict, global_index: int) -> str:
    """
    Build unique ID for a chunk using global index.
    Format: PLAN_001_DOC_001_0 or GENERIC_GDOC_001_0
    Global index ensures uniqueness across all blocks.
    """
    meta = chunk["metadata"]
    plan_id = meta.get("plan_id") or "GENERIC"
    doc_id = meta.get("doc_id", "UNKNOWN")
    return f"{plan_id}_{doc_id}_{global_index}"


# ── Core Operations ───────────────────────────────────────────────────────────

def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Convert list of texts to embeddings.
    Processes in batches to avoid memory issues.
    """
    model = get_model()
    all_embeddings = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        embeddings = model.encode(
            batch,
            normalize_embeddings=True,  # Required for cosine similarity
            show_progress_bar=False
        )
        all_embeddings.extend(embeddings.tolist())

    return all_embeddings


def store_chunks(chunks: list[dict]) -> dict:
    """
    Embed and store chunks in ChromaDB.

    Args:
        chunks: List of chunk dicts from pipeline

    Returns:
        {
            "stored":    int,
            "skipped":   int,
            "failed":    int
        }
    """
    collection = get_collection()
    stats = {"stored": 0, "skipped": 0, "failed": 0}

    if not chunks:
        return stats

    # Build all IDs upfront, then fetch existing ones in a single DB call
    all_ids = [build_chunk_id(chunk, i) for i, chunk in enumerate(chunks)]

    existing = collection.get(ids=all_ids)
    existing_set = set(existing["ids"])

    ids = []
    texts = []
    metadatas = []

    for chunk_id, chunk in zip(all_ids, chunks):
        if chunk_id in existing_set:
            stats["skipped"] += 1
        else:
            ids.append(chunk_id)
            texts.append(chunk["text"])
            metadatas.append(sanitize_metadata(chunk["metadata"]))

    if not ids:
        print(f"  All {stats['skipped']} chunks already in vector store.")
        return stats

    # Embed in batches
    print(f"  Embedding {len(ids)} chunks...")
    try:
        embeddings = embed_texts(texts)
    except Exception as e:
        print(f"  Embedding failed: {e}")
        stats["failed"] = len(ids)
        return stats

    # Store in batches
    print(f"  Storing in ChromaDB...")
    for i in range(0, len(ids), BATCH_SIZE):
        batch_ids = ids[i:i + BATCH_SIZE]
        batch_texts = texts[i:i + BATCH_SIZE]
        batch_embeddings = embeddings[i:i + BATCH_SIZE]
        batch_metadatas = metadatas[i:i + BATCH_SIZE]

        try:
            collection.add(
                ids=batch_ids,
                documents=batch_texts,
                embeddings=batch_embeddings,
                metadatas=batch_metadatas
            )
            stats["stored"] += len(batch_ids)
        except Exception as e:
            print(f"  Batch storage failed: {e}")
            stats["failed"] += len(batch_ids)

    return stats


def chunk_exists(chunk_id: str) -> bool:
    """Check if a chunk already exists in the vector store."""
    collection = get_collection()
    result = collection.get(ids=[chunk_id])
    return len(result["ids"]) > 0


def delete_plan_chunks(plan_id: str) -> int:
    """
    Delete all chunks belonging to a plan.
    Returns number of chunks deleted.
    """
    collection = get_collection()

    # Get all chunk IDs for this plan
    results = collection.get(
        where={"plan_id": plan_id}
    )

    if not results["ids"]:
        return 0

    collection.delete(ids=results["ids"])
    return len(results["ids"])


def delete_doc_chunks(doc_id: str) -> int:
    """
    Delete all chunks belonging to a specific document.
    Returns number of chunks deleted.
    """
    collection = get_collection()

    results = collection.get(
        where={"doc_id": doc_id}
    )

    if not results["ids"]:
        return 0

    collection.delete(ids=results["ids"])
    return len(results["ids"])


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_collection_stats() -> dict:
    """
    Return stats about what's stored in ChromaDB.
    """
    collection = get_collection()
    total = collection.count()

    # Count plan chunks
    plan_results = collection.get(
        where={"tier": "plan_doc"}
    )
    plan_chunks = len(plan_results["ids"])

    # Count generic chunks
    generic_results = collection.get(
        where={"tier": "generic"}
    )
    generic_chunks = len(generic_results["ids"])

    # Get unique plan IDs
    all_results = collection.get(include=["metadatas"])
    plan_ids = set()
    for meta in all_results["metadatas"]:
        if meta.get("plan_id"):
            plan_ids.add(meta["plan_id"])

    return {
        "total_chunks":    total,
        "plan_chunks":     plan_chunks,
        "generic_chunks":  generic_chunks,
        "plans_indexed":   len(plan_ids),
        "plan_ids":        list(plan_ids)
    }


# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("Testing vector store...\n")

    # Load chunks from processed folder
    chunks_file = os.path.join(
        "data", "processed", "PLAN_001_DOC_001_SPD.json"
    )

    if not os.path.exists(chunks_file):
        print(f"Chunks file not found: {chunks_file}")
        print("Run ingest.py first to generate chunks.")
        exit(1)

    with open(chunks_file, 'r', encoding='utf-8') as f:
        chunks = json.load(f)

    print(f"Loaded {len(chunks)} chunks from {chunks_file}")

    # Store chunks
    print("\nStoring chunks...")
    stats = store_chunks(chunks)
    print(f"  Stored:  {stats['stored']}")
    print(f"  Skipped: {stats['skipped']}")
    print(f"  Failed:  {stats['failed']}")

    # Collection stats
    print("\nCollection stats:")
    cstats = get_collection_stats()
    for key, val in cstats.items():
        print(f"  {key}: {val}")

    print("\nVector store test complete.")
    print(f"Data saved to: {VECTORDB_PATH}")