"""
Parser comparison test.
Runs all three parsers on the same PDF and saves outputs for comparison.
"""
import json
import os

TEST_PDF = "data/raw/Microsoft_401k_SPD.pdf"
OUTPUT_DIR = "data/processed"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── Test 1: pymupdf4llm ───────────────────────────────────────────────────────

def test_pymupdf4llm():
    print("\n" + "="*60)
    print("TEST 1: pymupdf4llm")
    print("="*60)

    import pymupdf4llm
    import re

    md_text = pymupdf4llm.to_markdown(TEST_PDF)

    # Parse markdown into structured sections
    sections = []
    current_h1 = ""
    current_h2 = ""
    current_h3 = ""
    current_text = []
    current_page = 1

    def flush():
        if current_text:
            combined = '\n'.join(current_text).strip()
            if combined:
                sections.append({
                    "section": current_h1,
                    "subsection": current_h2,
                    "subsubsection": current_h3,
                    "text_preview": combined[:200],
                    "text_length": len(combined)
                })

    for line in md_text.split('\n'):
        if line.startswith('# '):
            flush()
            current_text = []
            current_h1 = line[2:].strip()
            current_h2 = ""
            current_h3 = ""
        elif line.startswith('## '):
            flush()
            current_text = []
            current_h2 = line[3:].strip()
            current_h3 = ""
        elif line.startswith('### '):
            flush()
            current_text = []
            current_h3 = line[4:].strip()
        else:
            if line.strip():
                current_text.append(line.strip())

    flush()

    # Save full markdown for inspection
    with open(f"{OUTPUT_DIR}/test_pymupdf4llm.md", 'w', encoding='utf-8') as f:
        f.write(md_text)

    # Save structured sections
    with open(f"{OUTPUT_DIR}/test_pymupdf4llm.json", 'w', encoding='utf-8') as f:
        json.dump(sections, f, indent=2, ensure_ascii=False)

    print(f"Total sections extracted: {len(sections)}")
    print(f"\nSample sections:")
    for s in sections[:5]:
        print(f"\n  H1: {s['section']}")
        print(f"  H2: {s['subsection']}")
        print(f"  H3: {s['subsubsection']}")
        print(f"  Text: {s['text_preview'][:120]}...")

    print(f"\nFull markdown saved to: {OUTPUT_DIR}/test_pymupdf4llm.md")
    print(f"Structured JSON saved to: {OUTPUT_DIR}/test_pymupdf4llm.json")
    return sections


# ── Test 2: Unstructured Fast Mode ────────────────────────────────────────────

def test_unstructured_fast():
    print("\n" + "="*60)
    print("TEST 2: Unstructured — Fast Mode")
    print("="*60)

    from unstructured.partition.pdf import partition_pdf

    elements = partition_pdf(
        filename=TEST_PDF,
        strategy="fast",
    )

    sections = []
    current_h1 = ""
    current_h2 = ""
    current_text = []

    def flush():
        if current_text:
            combined = '\n'.join(current_text).strip()
            if combined:
                sections.append({
                    "section": current_h1,
                    "subsection": current_h2,
                    "element_types": [],
                    "text_preview": combined[:200],
                    "text_length": len(combined)
                })

    element_type_counts = {}
    raw_elements = []

    for el in elements:
        el_type = type(el).__name__
        element_type_counts[el_type] = element_type_counts.get(el_type, 0) + 1

        raw_elements.append({
            "type": el_type,
            "text": str(el.text)[:200]
        })

        if el_type == "Title":
            flush()
            current_text = []
            # Heuristic: short titles = h1, longer = h2
            if len(el.text.split()) <= 6:
                current_h1 = el.text
                current_h2 = ""
            else:
                current_h2 = el.text
        else:
            current_text.append(str(el.text))

    flush()

    # Save raw elements
    with open(f"{OUTPUT_DIR}/test_unstructured_fast_raw.json", 'w', encoding='utf-8') as f:
        json.dump(raw_elements, f, indent=2, ensure_ascii=False)

    # Save structured sections
    with open(f"{OUTPUT_DIR}/test_unstructured_fast.json", 'w', encoding='utf-8') as f:
        json.dump(sections, f, indent=2, ensure_ascii=False)

    print(f"Element types found: {element_type_counts}")
    print(f"Total sections extracted: {len(sections)}")
    print(f"\nSample sections:")
    for s in sections[:5]:
        print(f"\n  H1: {s['section']}")
        print(f"  H2: {s['subsection']}")
        print(f"  Text: {s['text_preview'][:120]}...")

    print(f"\nRaw elements saved to: {OUTPUT_DIR}/test_unstructured_fast_raw.json")
    print(f"Structured JSON saved to: {OUTPUT_DIR}/test_unstructured_fast.json")
    return sections


# ── Test 3: Unstructured Hi-Res Mode ─────────────────────────────────────────

def test_unstructured_hires():
    print("\n" + "="*60)
    print("TEST 3: Unstructured — Hi-Res Mode")
    print("="*60)
    print("Note: This may take several minutes on a 58-page PDF...")

    try:
        from unstructured.partition.pdf import partition_pdf

        elements = partition_pdf(
            filename=TEST_PDF,
            strategy="hi_res",
        )

        sections = []
        current_h1 = ""
        current_h2 = ""
        current_text = []

        def flush():
            if current_text:
                combined = '\n'.join(current_text).strip()
                if combined:
                    sections.append({
                        "section": current_h1,
                        "subsection": current_h2,
                        "text_preview": combined[:200],
                        "text_length": len(combined)
                    })

        element_type_counts = {}
        raw_elements = []

        for el in elements:
            el_type = type(el).__name__
            element_type_counts[el_type] = element_type_counts.get(el_type, 0) + 1

            raw_elements.append({
                "type": el_type,
                "text": str(el.text)[:200]
            })

            if el_type == "Title":
                flush()
                current_text = []
                if len(el.text.split()) <= 6:
                    current_h1 = el.text
                    current_h2 = ""
                else:
                    current_h2 = el.text
            else:
                current_text.append(str(el.text))

        flush()

        # Save raw elements
        with open(f"{OUTPUT_DIR}/test_unstructured_hires_raw.json", 'w', encoding='utf-8') as f:
            json.dump(raw_elements, f, indent=2, ensure_ascii=False)

        # Save structured sections
        with open(f"{OUTPUT_DIR}/test_unstructured_hires.json", 'w', encoding='utf-8') as f:
            json.dump(sections, f, indent=2, ensure_ascii=False)

        print(f"Element types found: {element_type_counts}")
        print(f"Total sections extracted: {len(sections)}")
        print(f"\nSample sections:")
        for s in sections[:5]:
            print(f"\n  H1: {s['section']}")
            print(f"  H2: {s['subsection']}")
            print(f"  Text: {s['text_preview'][:120]}...")

        print(f"\nRaw elements saved to: {OUTPUT_DIR}/test_unstructured_hires_raw.json")
        print(f"Structured JSON saved to: {OUTPUT_DIR}/test_unstructured_hires.json")
        return sections

    except Exception as e:
        print(f"Hi-res mode failed: {e}")
        print("This usually means additional dependencies are needed.")
        print("We'll skip hi-res and decide between fast and pymupdf4llm.")
        return []


# ── Run all tests ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Starting parser comparison...")
    print(f"Test file: {TEST_PDF}")

    results = {}

    # Test 1
    try:
        results["pymupdf4llm"] = test_pymupdf4llm()
    except Exception as e:
        print(f"pymupdf4llm failed: {e}")

    # Test 2
    try:
        results["unstructured_fast"] = test_unstructured_fast()
    except Exception as e:
        print(f"Unstructured fast failed: {e}")

    # Test 3
    try:
        results["unstructured_hires"] = test_unstructured_hires()
    except Exception as e:
        print(f"Unstructured hi-res failed: {e}")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for parser, sections in results.items():
        print(f"{parser}: {len(sections)} sections extracted")

    print("\nCheck data/processed/ folder for full output files.")