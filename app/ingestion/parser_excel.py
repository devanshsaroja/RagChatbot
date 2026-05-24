import re
import openpyxl


def clean_text(text: str) -> str:
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def cell_value(cell) -> str:
    if cell.value is None:
        return ""
    return str(cell.value).strip()


def is_header_row(row_values: list[str]) -> bool:
    non_empty = [v for v in row_values if v]
    if not non_empty:
        return False
    numeric_count = sum(
        1 for v in non_empty
        if v.replace('.', '').replace('%', '').replace('-', '').isdigit()
    )
    return numeric_count < len(non_empty) / 2


def parse_sheet_as_table(sheet) -> str:
    rows = []
    headers = []
    for row_idx, row in enumerate(sheet.iter_rows()):
        values = [cell_value(cell) for cell in row]
        if not any(values):
            continue
        if row_idx == 0 and is_header_row(values):
            headers = values
        else:
            if headers:
                paired = ' | '.join(
                    f"{headers[i]}: {values[i]}"
                    for i in range(min(len(headers), len(values)))
                    if values[i]
                )
                if paired:
                    rows.append(paired)
            else:
                joined = ' | '.join(v for v in values if v)
                if joined:
                    rows.append(joined)
    return '\n'.join(rows)


def parse_sheet_as_text(sheet) -> str:
    lines = []
    for row in sheet.iter_rows():
        values = [cell_value(cell) for cell in row]
        non_empty = [v for v in values if v]
        if not non_empty:
            if lines and lines[-1] != '':
                lines.append('')
            continue
        lines.append(' '.join(non_empty))
    return '\n'.join(lines)


def detect_sheet_type(sheet) -> str:
    rows = list(sheet.iter_rows(max_row=3))
    if not rows:
        return "text"
    first_row = [cell_value(cell) for cell in rows[0]]
    non_empty = [v for v in first_row if v]
    if not non_empty:
        return "text"
    if len(rows) > 1 and is_header_row(non_empty):
        second_row = [cell_value(cell) for cell in rows[1]]
        if any(second_row):
            return "table"
    return "text"


def detect_workbook_title(wb) -> str:
    """
    Detect workbook title from:
    1. Built-in document properties (title field)
    2. First non-empty cell of first sheet as fallback
    Universal — works for any Excel file.
    """
    # Try built-in title property first
    if wb.properties.title and wb.properties.title.strip():
        return wb.properties.title.strip()

    # Fallback — first non-empty cell of first sheet
    first_sheet = wb[wb.sheetnames[0]]
    for row in first_sheet.iter_rows(max_row=3):
        for cell in row:
            val = cell_value(cell)
            if val and len(val.split()) > 2:
                return val

    return ""


def parse_excel(file_path: str) -> list[dict]:
    """
    Parse an Excel file and return a list of sections with text and metadata.
    Each sheet becomes one section.
    Detects workbook title from document properties or first cell.
    """
    wb = openpyxl.load_workbook(file_path, data_only=True)
    doc_title = detect_workbook_title(wb)
    results = []

    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]

        if sheet.max_row == 0 or sheet.max_column == 0:
            continue

        sheet_type = detect_sheet_type(sheet)

        if sheet_type == "table":
            text = parse_sheet_as_table(sheet)
        else:
            text = parse_sheet_as_text(sheet)

        text = clean_text(text)
        if not text:
            continue

        results.append({
            "text": text,
            "metadata": {
                "source_file":   file_path,
                "file_type":     "xlsx",
                "doc_title":     doc_title,
                "section":       sheet_name,
                "subsection":    "",
                "subsubsection": "",
                "page_num":      1,
                "chunk_index":   0,
                "total_chunks":  0,
                "token_count":   0,
            }
        })

    wb.close()
    return results


if __name__ == "__main__":
    import sys
    import os

    if len(sys.argv) < 2:
        print("Usage: python parser_excel.py <path_to_excel>")
        sys.exit(0)

    file_path = sys.argv[1]
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        sys.exit(1)

    sections = parse_excel(file_path)
    for s in sections:
        print(f"\nDoc Title:  '{s['metadata']['doc_title']}'")
        print(f"Section:    '{s['metadata']['section']}'")
        print(f"Subsection: '{s['metadata']['subsection']}'")
        print(f"Text: {s['text'][:200]}")
        print('-' * 50)
    print(f"\nTotal sections: {len(sections)}")