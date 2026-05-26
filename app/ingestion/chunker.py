import re

from app.embedding_model import get_model

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_TOKENS    = 512   # Hard model limit
TARGET_TOKENS = 360   # Safe target (leaves room for context header + subwords)
OVERLAP_TOKENS = 40   # ~10% overlap for plain text
MIN_TOKENS    = 15    # Skip chunks smaller than this

# ── Tokenizer ─────────────────────────────────────────────────────────────────

def force_split_oversized_chunk(
    chunk_text: str,
    max_tokens: int = MAX_TOKENS
) -> list[str]:
    """
    Last resort splitter for chunks that exceed max_tokens.
    Splits by words when no other strategy works.
    Used as safety net after all other chunking strategies.
    """
    if count_tokens(chunk_text) <= max_tokens:
        return [chunk_text]

    # Find the context header (lines starting with [)
    lines = chunk_text.split('\n')
    header_lines = []
    content_lines = []
    in_header = True

    for line in lines:
        if in_header and (line.startswith('[') or line.strip() == ''):
            header_lines.append(line)
        else:
            in_header = False
            content_lines.append(line)

    header = '\n'.join(header_lines)
    content = '\n'.join(content_lines)
    header_tokens = count_tokens(header)
    available = max_tokens - header_tokens - 10  # 10 token safety buffer

    # Split content by words
    words = content.split()
    result = []
    current_words = []
    current_count = 0

    for word in words:
        word_tokens = count_tokens(word)
        if current_count + word_tokens > available:
            if current_words:
                result.append(header + '\n' + ' '.join(current_words))
                current_words = []
                current_count = 0
        current_words.append(word)
        current_count += word_tokens

    if current_words:
        result.append(header + '\n' + ' '.join(current_words))

    return result if result else [chunk_text]

def clean_markdown_artifacts(text: str) -> str:
    """
    Clean markdown artifacts that inflate token counts.
    - Convert <br> tags to spaces
    - Remove dot leaders (. . . . .) from IRS forms
    - Remove excessive repeated punctuation
    """
    # Convert <br> to space
    text = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)
    # Remove dot leaders — sequences of ". " repeated 3+ times
    text = re.sub(r'(\.\s){3,}', ' ', text)
    # Remove sequences of dots without spaces
    text = re.sub(r'\.{4,}', ' ', text)
    # Remove picture text markers
    text = re.sub(r'-{5,}\s*Start of picture text\s*-{5,}', '', text)
    text = re.sub(r'-{5,}\s*End of picture text\s*-{5,}', '', text)
    # Normalize multiple spaces
    text = re.sub(r' {2,}', ' ', text)
    return text


def count_tokens(text: str) -> int:
    """
    Count tokens accurately using the model's own tokenizer.
    """
    import warnings
    model = get_model()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tokens = model.tokenizer.encode(
            text,
            add_special_tokens=True,
            truncation=False,
            max_length=None
        )
    return len(tokens)


# ── Context Header ────────────────────────────────────────────────────────────

def build_context_header(metadata: dict) -> str:
    """
    Build context header prepended to every chunk.
    Tells Claude exactly where this chunk came from.
    """
    parts = []

    doc_title = metadata.get("doc_title", "")
    section = metadata.get("section", "")
    subsection = metadata.get("subsection", "")
    subsubsection = metadata.get("subsubsection", "")

    if doc_title:
        parts.append(f"[Document: {doc_title}]")
    if section:
        parts.append(f"[Section: {section}]")
    if subsection:
        parts.append(f"[Subsection: {subsection}]")
    if subsubsection:
        parts.append(f"[Topic: {subsubsection}]")

    return '\n'.join(parts) + '\n\n' if parts else ''


# ── Table Detection + Handling ────────────────────────────────────────────────

def is_table_line(line: str) -> bool:
    """Detect markdown table lines."""
    stripped = line.strip()
    return stripped.startswith('|') and stripped.endswith('|')


def is_separator_line(line: str) -> bool:
    """Detect markdown table separator line: |---|---|"""
    stripped = line.strip()
    return bool(re.match(r'^\|[-|\s:]+\|$', stripped))


def split_block_into_segments(text: str) -> list[dict]:
    """
    Split a block of text into alternating text and table segments.
    Preserves order of content.

    Returns list of:
      {"type": "text", "content": "..."}
      {"type": "table", "headers": [...], "rows": [...], "raw": "..."}
    """
    lines = text.split('\n')
    segments = []
    current_text = []
    current_table = []
    in_table = False

    for line in lines:
        if is_table_line(line):
            # Entering table
            if not in_table:
                # Save accumulated text first
                if current_text:
                    content = '\n'.join(current_text).strip()
                    if content:
                        segments.append({"type": "text", "content": content})
                    current_text = []
                in_table = True
            current_table.append(line)
        else:
            # Leaving table
            if in_table:
                if current_table:
                    table_seg = parse_table_segment(current_table)
                    if table_seg:
                        segments.append(table_seg)
                    current_table = []
                in_table = False
            current_text.append(line)

    # Don't forget last segment
    if in_table and current_table:
        table_seg = parse_table_segment(current_table)
        if table_seg:
            segments.append(table_seg)
    elif current_text:
        content = '\n'.join(current_text).strip()
        if content:
            segments.append({"type": "text", "content": content})

    return segments


def parse_table_segment(table_lines: list[str]) -> dict | None:
    """
    Parse raw markdown table lines into structured format.
    Extracts headers and data rows separately.
    """
    if not table_lines:
        return None

    headers = []
    rows = []
    raw = '\n'.join(table_lines)

    for line in table_lines:
        if is_separator_line(line):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        # Clean bold markers from cells
        cells = [re.sub(r'\*\*(.*?)\*\*', r'\1', c) for c in cells]

        if not headers:
            headers = cells
        else:
            if any(c for c in cells):  # Skip empty rows
                rows.append(cells)

    if not headers:
        return None

    return {
        "type": "table",
        "headers": headers,
        "rows": rows,
        "raw": raw
    }


# ── Text Chunking ─────────────────────────────────────────────────────────────

def split_text_into_chunks(
    text: str,
    context_header: str,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS
) -> list[str]:
    """
    Split plain text into overlapping chunks.
    Splits on sentence boundaries where possible.
    Each chunk includes context header.
    """
    # Account for header tokens
    header_tokens = count_tokens(context_header)
    available_tokens = target_tokens - header_tokens

    if available_tokens <= 0:
        available_tokens = target_tokens

    # Split into sentences
    sentences = re.split(r'(?<=[.?!])\s+', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks = []
    current_sentences = []
    current_count = 0

    for sentence in sentences:
        sentence_tokens = count_tokens(sentence)

        # Single sentence exceeds limit — force add as own chunk
        if sentence_tokens > available_tokens:
            if current_sentences:
                chunk_text = ' '.join(current_sentences)
                chunks.append(context_header + chunk_text)
                # Keep last few sentences for overlap
                overlap_sentences = get_overlap_sentences(
                    current_sentences, overlap_tokens
                )
                current_sentences = overlap_sentences
                current_count = count_tokens(' '.join(current_sentences))

            # Add the long sentence as its own chunk
            chunks.append(context_header + sentence)
            current_sentences = []
            current_count = 0
            continue

        # Adding this sentence would exceed limit
        if current_count + sentence_tokens > available_tokens:
            if current_sentences:
                chunk_text = ' '.join(current_sentences)
                chunks.append(context_header + chunk_text)
                # Overlap — carry forward last N tokens worth of sentences
                overlap_sentences = get_overlap_sentences(
                    current_sentences, overlap_tokens
                )
                current_sentences = overlap_sentences + [sentence]
                current_count = count_tokens(' '.join(current_sentences))
            else:
                current_sentences = [sentence]
                current_count = sentence_tokens
        else:
            current_sentences.append(sentence)
            current_count += sentence_tokens

    # Last chunk
    if current_sentences:
        chunk_text = ' '.join(current_sentences)
        chunks.append(context_header + chunk_text)

    return chunks


def get_overlap_sentences(sentences: list[str], overlap_tokens: int) -> list[str]:
    """
    Get sentences from the end of a list that fit within overlap_tokens.
    Used to carry context forward into next chunk.
    """
    overlap = []
    token_count = 0
    for sentence in reversed(sentences):
        t = count_tokens(sentence)
        if token_count + t <= overlap_tokens:
            overlap.insert(0, sentence)
            token_count += t
        else:
            break
    return overlap


# ── Table Chunking ────────────────────────────────────────────────────────────

def split_table_into_chunks(
    table_seg: dict,
    context_header: str,
    target_tokens: int = TARGET_TOKENS
) -> list[str]:
    """
    Split a table into chunks.
    Headers are repeated on every chunk.
    Never splits mid-row.

    If entire table fits in target_tokens → one chunk.
    If too large → split by rows, repeat headers each time.
    If single row exceeds limit → keep as one chunk (better than splitting).
    """
    headers = table_seg["headers"]
    rows = table_seg["rows"]

    # Build header row markdown
    header_line = '| ' + ' | '.join(headers) + ' |'
    separator_line = '| ' + ' | '.join(['---'] * len(headers)) + ' |'
    header_block = f"{header_line}\n{separator_line}"

    header_tokens = count_tokens(context_header + header_block)
    available_tokens = target_tokens - header_tokens

    chunks = []
    current_rows = []
    current_count = 0

    for row in rows:
        # Build row markdown
        row_line = '| ' + ' | '.join(
            str(cell) for cell in row[:len(headers)]
        ) + ' |'
        row_tokens = count_tokens(row_line)

        # Single row exceeds limit — split by cells vertically
        if row_tokens > available_tokens:
            if current_rows:
                chunk = build_table_chunk(
                    context_header, header_block, current_rows
                )
                chunks.append(chunk)
                current_rows = []
                current_count = 0
            # Split oversized row into key:value pairs
            for i, cell in enumerate(row[:len(headers)]):
                if cell and str(cell).strip():
                    pair = f"{headers[i]}: {cell}"
                    chunks.append(
                        f"{context_header}[Table row data]\n{pair}"
                    )
            continue

        # Row exceeds available space — save current batch
        if current_count + row_tokens > available_tokens:
            if current_rows:
                chunk = build_table_chunk(
                    context_header, header_block, current_rows
                )
                chunks.append(chunk)
                current_rows = []
                current_count = 0

        current_rows.append(row_line)
        current_count += row_tokens

    # Last chunk
    if current_rows:
        chunk = build_table_chunk(context_header, header_block, current_rows)
        chunks.append(chunk)

    # If no rows were processed, return raw table
    if not chunks:
        chunks.append(context_header + table_seg["raw"])

    return chunks


def build_table_chunk(
    context_header: str,
    header_block: str,
    row_lines: list[str]
) -> str:
    """Assemble a table chunk with context header and repeated headers."""
    rows_text = '\n'.join(row_lines)
    return f"{context_header}{header_block}\n{rows_text}"


# ── Master Chunker ────────────────────────────────────────────────────────────

def chunk_document(parsed_block: dict) -> list[dict]:
    """
    Take a single parsed block and split into chunks.

    Handles:
    - Plain text → sentence boundary splitting with overlap
    - Tables → row-based splitting with repeated headers
    - Mixed → split into segments, chunk each separately

    Returns list of chunk dicts with full metadata.
    """
    text = clean_markdown_artifacts(parsed_block["text"])
    metadata = parsed_block["metadata"]

    context_header = build_context_header(metadata)

    # Split block into text and table segments
    segments = split_block_into_segments(text)

    raw_chunks = []

    for segment in segments:
        if segment["type"] == "text":
            text_chunks = split_text_into_chunks(
                segment["content"], context_header
            )
            raw_chunks.extend(text_chunks)

        elif segment["type"] == "table":
            table_chunks = split_table_into_chunks(
                segment, context_header
            )
            raw_chunks.extend(table_chunks)

    # Filter empty or too-small chunks
    raw_chunks = [
        c for c in raw_chunks
        if count_tokens(c) >= MIN_TOKENS
    ]

    # Build final chunk dicts with metadata
    total = len(raw_chunks)
    result = []

    for idx, chunk_text in enumerate(raw_chunks):
        chunk_metadata = metadata.copy()
        chunk_metadata["chunk_index"] = idx
        chunk_metadata["total_chunks"] = total
        chunk_metadata["token_count"] = count_tokens(chunk_text)

        result.append({
            "text": chunk_text,
            "metadata": chunk_metadata
        })

    return result


def chunk_all(parsed_blocks: list[dict]) -> list[dict]:
    """
    Chunk all parsed blocks from a document.
    Returns flat list of all chunks ready for embedding.
    Applies safety net splitting for any chunks still over limit.
    """
    all_chunks = []
    for block in parsed_blocks:
        chunks = chunk_document(block)
        all_chunks.extend(chunks)

    # Safety net — force split any chunks still over MAX_TOKENS
    final_chunks = []
    for chunk in all_chunks:
        if chunk["metadata"]["token_count"] > MAX_TOKENS:
            # Force split
            sub_texts = force_split_oversized_chunk(chunk["text"])
            for i, sub_text in enumerate(sub_texts):
                new_meta = chunk["metadata"].copy()
                new_meta["token_count"] = count_tokens(sub_text)
                new_meta["chunk_index"] = chunk["metadata"]["chunk_index"] + i
                final_chunks.append({
                    "text": sub_text,
                    "metadata": new_meta
                })
        else:
            final_chunks.append(chunk)

    return final_chunks


# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from app.ingestion.parser_pdf import parse_pdf

    print("Testing chunker with Microsoft 401k SPD...\n")
    blocks = parse_pdf("data/raw/Microsoft_401k_SPD.pdf")

    print(f"\nChunking {len(blocks)} blocks...")
    chunks = chunk_all(blocks)

    print(f"\nTotal chunks: {len(chunks)}")
    print(f"\nToken distribution:")

    token_counts = [c["metadata"]["token_count"] for c in chunks]
    print(f"  Min:     {min(token_counts)} tokens")
    print(f"  Max:     {max(token_counts)} tokens")
    print(f"  Average: {sum(token_counts) // len(token_counts)} tokens")
    over_limit = sum(1 for t in token_counts if t > MAX_TOKENS)
    print(f"  Over 512 limit: {over_limit} chunks")

    print(f"\nSample chunks:")
    print("=" * 60)
    for chunk in chunks[:3]:
        meta = chunk["metadata"]
        print(f"Chunk {meta['chunk_index'] + 1}/{meta['total_chunks']}")
        print(f"  Section:    {meta['section']}")
        print(f"  Subsection: {meta['subsection']}")
        print(f"  Tokens:     {meta['token_count']}")
        print(f"  Text:\n{chunk['text'][:300]}")
        print("-" * 60)