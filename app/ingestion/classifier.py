import os
from dotenv import load_dotenv
load_dotenv()

import anthropic
import pymupdf as fitz  # PyMuPDF — fast raw text extraction for classification

DOC_TYPES = [
    "SPD",
    "Adoption Agreement",
    "Plan Amendment",
    "IRS Determination Letter",
    "Form 5500",
    "IRS Publication",
    "ERISA Regulation",
    "Other",
]

CLASSIFY_PROMPT = """Classify this retirement plan document into exactly one of these types:
SPD, Adoption Agreement, Plan Amendment, IRS Determination Letter, Form 5500, IRS Publication, ERISA Regulation, Other

Definitions:
- SPD: Summary Plan Description — participant-facing document explaining benefits, vesting, eligibility
- Adoption Agreement: employer elections form for adopting a prototype/volume-submitter plan
- Plan Amendment: document formally amending or modifying plan terms
- IRS Determination Letter: IRS letter confirming the plan's qualified status
- Form 5500: annual government filing (IRS/DOL) reporting plan financials and participation data
- IRS Publication: IRS guidance document (e.g. Publication 590, 560, 15-A)
- ERISA Regulation: Department of Labor or ERISA regulatory/guidance text
- Other: does not clearly fit any of the above

Reply with ONLY the document type string — no explanation, no punctuation, nothing else.

Document text (first few pages):
{text}"""


def _extract_text_for_classification(file_path: str, ext: str) -> str:
    """
    Extract raw text from the first few pages for classification only.
    Uses fitz directly for PDFs — bypasses the full chunking pipeline
    which requires headings/sections and would skip cover/TOC pages.
    For DOCX/Excel falls back to a simple read.
    """
    if ext == ".pdf":
        try:
            doc = fitz.open(file_path)
            pages_to_read = min(2, len(doc))
            parts = []
            for i in range(pages_to_read):
                page_text = doc[i].get_text("text").strip()
                if page_text:
                    parts.append(page_text)
            doc.close()
            return "\n\n".join(parts)
        except Exception:
            return ""

    if ext in (".docx",):
        try:
            import docx
            doc = docx.Document(file_path)
            # First 60 paragraphs covers several pages
            lines = [p.text.strip() for p in doc.paragraphs[:60] if p.text.strip()]
            return "\n".join(lines)
        except Exception:
            return ""

    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            ws = wb.active
            lines = []
            for row in ws.iter_rows(max_row=30, values_only=True):
                row_text = " | ".join(str(c) for c in row if c is not None)
                if row_text.strip():
                    lines.append(row_text)
            wb.close()
            return "\n".join(lines)
        except Exception:
            return ""

    return ""


def classify_document(file_path: str, ext: str) -> tuple[str, int, int]:
    """
    Read the first few pages of a document and classify its type using Claude Haiku.
    Returns (doc_type, input_tokens, output_tokens).
    doc_type is one of DOC_TYPES strings, or "Other" on any failure.
    """
    if ext not in (".pdf", ".docx", ".xlsx", ".xls"):
        return "Other", 0, 0

    text = _extract_text_for_classification(file_path, ext)
    if not text.strip():
        return "Other", 0, 0

    # Cap at 3 000 chars — enough for classification, keeps cost minimal
    if len(text) > 3000:
        text = text[:3000] + "…"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "Other", 0, 0

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(text=text)}],
    )

    input_tokens  = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    detected = response.content[0].text.strip().rstrip(".")

    if detected in DOC_TYPES:
        return detected, input_tokens, output_tokens

    # Fuzzy fallback — Claude occasionally adds extra words
    for doc_type in DOC_TYPES:
        if doc_type.lower() in detected.lower():
            return doc_type, input_tokens, output_tokens

    return "Other", input_tokens, output_tokens
