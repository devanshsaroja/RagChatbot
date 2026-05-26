import logging
# Suppress transformers tokenizer length warning — handled by chunker
logging.getLogger("transformers.tokenization_utils_base").setLevel(logging.ERROR)

from sentence_transformers import SentenceTransformer


# ── Constants ─────────────────────────────────────────────────────────────────

EMBEDDING_MODEL = "multi-qa-mpnet-base-cos-v1"


# ── Shared Model Singleton ────────────────────────────────────────────────────

_model = None


def get_model() -> SentenceTransformer:
    """
    Load the embedding model once and reuse across all modules.
    Shared by chunker.py (tokenization) and vector_store.py (embedding)
    so the model is only loaded into RAM once per process.
    """
    global _model
    if _model is None:
        print("  Loading embedding model...")
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model
