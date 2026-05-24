import re
from docx import Document
from docx.oxml.ns import qn


def clean_text(text: str) -> str:
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_paragraph_text(para) -> str:
    text_parts = []
    for child in para._element:
        if child.tag == qn('w:r'):
            for t in child.findall(qn('w:t')):
                if t.text:
                    text_parts.append(t.text)
        elif child.tag == qn('w:hyperlink'):
            for run in child.findall(qn('w:r')):
                for t in run.findall(qn('w:t')):
                    if t.text:
                        text_parts.append(t.text)
    return ''.join(text_parts).strip()


def extract_table(table) -> str:
    rows = []
    headers = []
    for row_idx, row in enumerate(table.rows):
        cells = [cell.text.strip() for cell in row.cells]
        cells = list(dict.fromkeys(cells))
        if row_idx == 0:
            headers = cells
        else:
            if headers:
                paired = ' | '.join(
                    f"{headers[i]}: {cells[i]}"
                    for i in range(min(len(headers), len(cells)))
                    if cells[i]
                )
                if paired:
                    rows.append(paired)
            else:
                rows.append(' | '.join(c for c in cells if c))
    return '\n'.join(rows)


def parse_docx(file_path: str) -> list[dict]:
    """
    Parse a DOCX file and return a list of sections with text and metadata.

    Document title detection (two strategies, in priority order):
      1. Word built-in 'Title' style — most reliable
      2. First Heading 1 with no body content before it — fallback

    Heading hierarchy:
      Heading 1 → section
      Heading 2+ → subsection
    """
    doc = Document(file_path)
    results = []
    current_section = ""
    current_subsection = ""
    current_text = []
    page_num = 1
    doc_title = ""
    is_first_heading = True

    def flush():
        if current_text:
            combined = clean_text('\n'.join(current_text))
            if combined and len(combined.split()) >= 8:
                results.append({
                    "text": combined,
                    "metadata": {
                        "source_file":   file_path,
                        "file_type":     "docx",
                        "doc_title":     doc_title,
                        "section":       current_section,
                        "subsection":    current_subsection,
                        "subsubsection": "",
                        "page_num":      page_num,
                        "chunk_index":   0,
                        "total_chunks":  0,
                        "token_count":   0,
                    }
                })

    for element in doc.element.body:
        tag = element.tag

        if tag == qn('w:p'):
            from docx.text.paragraph import Paragraph
            para = Paragraph(element, doc)
            text = extract_paragraph_text(para)

            if not text:
                continue

            # Strategy 1 — Word built-in Title style
            if para.style.name == 'Title':
                doc_title = text
                continue

            if para.style.name.startswith('Heading'):
                flush()

                # Strategy 2 — First Heading 1 with no body content yet
                if is_first_heading and not current_text:
                    heading_level = para.style.name
                    if '1' in heading_level:
                        doc_title = text
                        current_text = []
                        is_first_heading = False
                        continue

                current_text = []
                is_first_heading = False

                # Heading 1 → section, Heading 2+ → subsection
                heading_level = para.style.name
                if '1' in heading_level:
                    current_section = text
                    current_subsection = ""
                    page_num += 1
                else:
                    current_subsection = text

            elif para.style.name.startswith('List'):
                is_first_heading = False
                current_text.append(f"• {text}")

            else:
                is_first_heading = False
                current_text.append(text)

        elif tag == qn('w:tbl'):
            from docx.table import Table
            table = Table(element, doc)
            table_text = extract_table(table)
            if table_text:
                current_text.append(f"\n[TABLE]\n{table_text}\n[/TABLE]")

    # Save last section
    flush()
    return results


if __name__ == "__main__":
    import sys
    import os

    if len(sys.argv) < 2:
        print("Usage: python parser_docx.py <path_to_docx>")
        sys.exit(0)

    file_path = sys.argv[1]
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        sys.exit(1)

    sections = parse_docx(file_path)
    for s in sections:
        print(f"\nDoc Title:  '{s['metadata']['doc_title']}'")
        print(f"Section:    '{s['metadata']['section']}'")
        print(f"Subsection: '{s['metadata']['subsection']}'")
        print(f"Words: {len(s['text'].split())}")
        print(f"Text: {s['text'][:200]}")
        print('-' * 50)
    print(f"\nTotal sections: {len(sections)}")