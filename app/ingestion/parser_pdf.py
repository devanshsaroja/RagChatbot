import re
from collections import Counter
import fitz  # PyMuPDF — used for Pass 1 only
import pymupdf4llm  # Used for Pass 2


# ══════════════════════════════════════════════════════════════════════════════
# PASS 1 — Detect repeated header/footer lines using raw PyMuPDF
# ══════════════════════════════════════════════════════════════════════════════

def extract_lines_per_page(file_path: str) -> dict[int, list[str]]:
    """
    Extract all text lines from each page using raw PyMuPDF.
    Returns {page_num: [line1, line2, ...]}
    """
    doc = fitz.open(file_path)
    pages = {}

    for page_num, page in enumerate(doc, start=1):
        lines = []
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_text = " ".join(
                    span.get("text", "")
                    for span in line.get("spans", [])
                ).strip()
                if line_text:
                    lines.append(line_text)
        pages[page_num] = lines

    doc.close()
    return pages


def detect_repeated_lines(
    pages: dict[int, list[str]],
    repeat_threshold: float = 0.3
) -> set[str]:
    """
    Find lines that repeat across many pages — these are headers/footers.

    A line appearing on more than repeat_threshold (30%) of pages
    is considered a header or footer and should be filtered out.

    This is universal — works for any document without hardcoding.
    """
    total_pages = len(pages)
    if total_pages == 0:
        return set()

    # Count how many pages each line appears on
    line_page_count = Counter()
    for page_num, lines in pages.items():
        # Use set to count each line once per page
        unique_lines = set(line.strip() for line in lines if line.strip())
        for line in unique_lines:
            line_page_count[line] += 1

    # Lines appearing on > threshold% of pages = header/footer
    min_pages = max(2, int(total_pages * repeat_threshold))
    repeated = {
        line for line, count in line_page_count.items()
        if count >= min_pages
    }

    return repeated

def detect_page_number_lines(
    pages: dict[int, list[str]]
) -> set[str]:
    """
    Detect lines that contain page numbers — these vary per page
    so they won't be caught by exact-match repetition detection.

    Strategy: find lines matching page number patterns
    that appear consistently across multiple pages.
    """
    import re

    # Patterns that indicate a line contains a page number
    page_patterns = [
        r'\bpage\s+\d+\b',           # "page 4"
        r'\b\d+\s+of\s+\d+\b',       # "4 of 58"
        r'\bpage\s+\d+\s+of\s+\d+',  # "page 4 of 58"
        r'^\s*-\s*\d+\s*-\s*$',      # "- 4 -"
        r'^\s*\d+\s*$',              # standalone digit
        r'\b(i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii)\b$',  # roman numerals
    ]

    suspected = set()
    total_pages = len(pages)
    min_pages = max(2, int(total_pages * 0.2))

    # Count how many pages each "base pattern" appears on
    # Strip the digit to find repeated structure
    pattern_page_count = {}

    for page_num, lines in pages.items():
        for line in lines:
            for pattern in page_patterns:
                if re.search(pattern, line.strip(), re.IGNORECASE):
                    # Normalize by removing digits to find structural matches
                    normalized = re.sub(r'\d+', 'N', line.strip())
                    if normalized not in pattern_page_count:
                        pattern_page_count[normalized] = 0
                    pattern_page_count[normalized] += 1
                    break

    # Lines whose normalized form appears on many pages = page number lines
    frequent_patterns = {
        pattern for pattern, count in pattern_page_count.items()
        if count >= min_pages
    }

    # Now collect actual lines matching these frequent patterns
    for page_num, lines in pages.items():
        for line in lines:
            normalized = re.sub(r'\d+', 'N', line.strip())
            if normalized in frequent_patterns:
                suspected.add(line.strip())

    return suspected

def build_header_footer_blocklist(file_path: str) -> set[str]:
    """
    Full Pass 1 pipeline.
    Returns set of lines to filter out during markdown parsing.
    """
    pages = extract_lines_per_page(file_path)

    # Detect exact repeated lines (headers/footers)
    repeated = detect_repeated_lines(pages)

    # Detect page number lines (vary per page but follow same pattern)
    page_number_lines = detect_page_number_lines(pages)

    # Combine both
    blocklist = repeated | page_number_lines

    return blocklist


# ══════════════════════════════════════════════════════════════════════════════
# PASS 2 — Parse markdown from pymupdf4llm, filter and structure
# ══════════════════════════════════════════════════════════════════════════════

def parse_heading(line: str) -> tuple[int, str]:
    """
    Parse a markdown heading line into (level, text).
    Returns (0, line) if not a heading.

    Level detection logic:
      ## Non-bold text    → level 1 (major section)
      ## **Bold text**    → level 2 (subsection)
      ### Any text        → level 3 (sub-subsection)
      # Any text          → level 1
    """
    def strip_bold(text: str) -> str:
        # Remove bold markers **text**
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        # Remove italic markers _text_ or *text*
        text = re.sub(r'_(.*?)_', r'\1', text)
        text = re.sub(r'\*(.*?)\*', r'\1', text)
        return text.strip()

    if line.startswith('### '):
        return 3, strip_bold(line[4:].strip())

    if line.startswith('## '):
        text = line[3:].strip()
        is_bold = text.startswith('**')
        return (2 if is_bold else 1), strip_bold(text)

    if line.startswith('# '):
        return 1, strip_bold(line[2:].strip())

    return 0, line


def clean_text(text: str) -> str:
    """
    Clean extracted text:
    - Fix duplicate bullet markers
    - Normalize whitespace
    - Remove excessive blank lines
    """
    text = re.sub(r'-\s*•', '•', text)
    text = re.sub(r'•\s*-', '•', text)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def is_standalone_page_number(line: str) -> bool:
    """Detect lines that are just page numbers."""
    return bool(re.match(r'^\d{1,4}$', line.strip()))


def is_toc_section(heading_text: str) -> bool:
    """Detect Table of Contents — not useful for RAG."""
    lower = heading_text.lower().strip()
    return any(toc in lower for toc in [
        "table of contents",
        "contents",
        "index",
    ])

def is_document_title(
    heading_text: str,
    is_first_heading: bool,
    page_num: int,
    has_body_content: bool
) -> bool:
    """
    Detect document title universally.
    Title = first heading on pages 1-2 with no body content before it.
    Works for any document regardless of title length.
    """
    if not is_first_heading:
        return False
    if page_num > 2:
        return False
    if has_body_content:
        return False
    return True

def parse_markdown_to_blocks(
    md_text: str,
    blocklist: set[str],
    file_path: str,
    page_num: int = 1,
    state: dict = None
) -> list[dict]:
    """
    Parse pymupdf4llm markdown into structured content blocks.
    Accepts state dict to maintain context across pages.
    """
    # Use provided state or create fresh one
    if state is None:
        state = {
            "current_h1": "",
            "current_h2": "",
            "current_h3": "",
            "doc_title": "",
            "is_first_heading": True,
            "current_text": [],
            "blocks": []
        }

    results = []

    def flush():
        if state.get("skip_section"):
            state["current_text"] = []
            return
        if state["current_text"]:
            combined = clean_text('\n'.join(state["current_text"]))
            # Skip blocks with no section context
            has_context = (
                state["current_h1"] or
                state["current_h2"] or
                state["current_h3"]
            )
            if combined and len(combined.split()) >= 8 and has_context:
                results.append({
                    "text": combined,
                    "metadata": {
                        "source_file":   file_path,
                        "file_type":     "pdf",
                        "doc_title":     state["doc_title"],
                        "section":       state["current_h1"],
                        "subsection":    state["current_h2"],
                        "subsubsection": state["current_h3"],
                        "page_num":      page_num,
                        "chunk_index":   0,
                        "total_chunks":  0,
                        "token_count":   0,
                    }
                })
            state["current_text"] = []

    for line in md_text.split('\n'):
        stripped = line.strip()

        # Skip standalone page numbers
        if is_standalone_page_number(stripped):
            continue

        # Filter header/footer lines using blocklist
        if stripped in blocklist:
            continue

        # Skip empty lines but preserve paragraph breaks
        if not stripped:
            if state["current_text"] and state["current_text"][-1] != '':
                state["current_text"].append('')
            continue

        # Check if heading
        level, heading_text = parse_heading(line)

        if level > 0:
            flush()

            # Check for document title
            has_body_content = len(state["current_text"]) > 0
            if is_document_title(
                heading_text,
                state["is_first_heading"],
                page_num,
                has_body_content
            ):
                state["doc_title"] = heading_text
                state["is_first_heading"] = False
                state["skip_section"] = False
                continue

            state["is_first_heading"] = False

            # Check if TOC section
            state["skip_section"] = is_toc_section(heading_text)

            # Don't update section context for skipped sections
            if not state["skip_section"]:
                if level == 1:
                    state["current_h1"] = heading_text
                    state["current_h2"] = ""
                    state["current_h3"] = ""
                elif level == 2:
                    state["current_h2"] = heading_text
                    state["current_h3"] = ""
                elif level == 3:
                    state["current_h3"] = heading_text
        else:
            if not state.get("skip_section"):
                # Skip image placeholder lines
                if not any(skip in line for skip in [
                    '==> picture',
                    'intentionally omitted',
                    '----- Start of picture text -----',
                    '----- End of picture text -----',
                    '----- Start of drawing -----',
                    '----- End of drawing -----',
                ]):
                    state["current_text"].append(line)

    # Flush remaining content for this page
    flush()

    return results

# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def parse_pdf(file_path: str) -> list[dict]:
    """
    Universal PDF parser. Works for any document without hardcoding.

    Two-pass approach:
      Pass 1 — Detect repeated header/footer lines using raw PyMuPDF
      Pass 2 — Parse clean markdown from pymupdf4llm page by page
               using page_chunks=True for accurate page tracking
    """
    # Pass 1 — Build header/footer blocklist
    print(f"  Pass 1: Detecting headers/footers...")
    blocklist = build_header_footer_blocklist(file_path)
    print(f"  Filtered {len(blocklist)} repeated lines")

    # Pass 2 — Get page-aware chunks
    print(f"  Pass 2: Parsing markdown structure...")
    page_chunks = pymupdf4llm.to_markdown(
        file_path,
        page_chunks=True
    )

    # Process page by page — inject real page numbers
    all_blocks = []
    accumulated_state = {
        "current_h1": "",
        "current_h2": "",
        "current_h3": "",
        "doc_title": "",
        "is_first_heading": True,
        "current_text": [],
        "blocks": []
    }

    for page_data in page_chunks:
        page_num = page_data["metadata"]["page_number"]
        page_text = page_data.get("text", "")

        if not page_text.strip():
            continue

        # Parse this page's markdown with correct page number
        page_blocks = parse_markdown_to_blocks(
            page_text,
            blocklist,
            file_path,
            page_num=page_num,
            state=accumulated_state
        )
        all_blocks.extend(page_blocks)

    # Flush any remaining content
    if accumulated_state["current_text"]:
        combined = clean_text(
            '\n'.join(accumulated_state["current_text"])
        )
        if combined and len(combined.split()) >= 8:
            all_blocks.append({
                "text": combined,
                "metadata": {
                    "source_file":   file_path,
                    "file_type":     "pdf",
                    "doc_title":     accumulated_state["doc_title"],
                    "section":       accumulated_state["current_h1"],
                    "subsection":    accumulated_state["current_h2"],
                    "subsubsection": accumulated_state["current_h3"],
                    "page_num":      len(page_chunks),
                    "chunk_index":   0,
                    "total_chunks":  0,
                    "token_count":   0,
                }
            })

    print(f"  Extracted {len(all_blocks)} content blocks")
    return all_blocks

# ══════════════════════════════════════════════════════════════════════════════
# TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import os

    file_path = (
        sys.argv[1] if len(sys.argv) > 1
        else "data/raw/Microsoft_401k_SPD.pdf"
    )

    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        sys.exit(1)

    print(f"\nParsing: {file_path}")
    print("-" * 60)

    blocks = parse_pdf(file_path)

    print("\nSample output (first 10 blocks):")
    print("=" * 60)
    for b in blocks[:10]:
        meta = b["metadata"]
        print(f"Page {meta['page_num']}")
        print(f"  Doc Title: '{meta['doc_title']}'")
        print(f"  H1: '{meta['section']}'")
        print(f"  H2: '{meta['subsection']}'")
        print(f"  H3: '{meta['subsubsection']}'")
        print(f"  Words: {len(b['text'].split())}")
        print(f"  Text: {b['text'][:120]}...")
        print("-" * 60)

    print(f"\nTotal blocks: {len(blocks)}")