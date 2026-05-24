# Retirement Plan RAG

An agentic Retrieval-Augmented Generation (RAG) system for retirement plan documents. Answers questions about 401(k) plans, ERISA regulations, and IRS rules by retrieving relevant context from ingested documents.

## Architecture
Documents (PDF/DOCX/XLSX)
↓
Ingestion Pipeline (parse → chunk → embed → store)
↓
ChromaDB Vector Store
↓
Agentic Retrieval (Claude decides what to search)
↓
Structured Answer (response + citations + confidence)

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | Claude API (claude-sonnet-4-5) |
| Embeddings | sentence-transformers (multi-qa-mpnet-base-cos-v1) |
| Vector DB | ChromaDB (persistent, local) |
| PDF Parser | pymupdf4llm |
| DOCX Parser | python-docx |
| Excel Parser | openpyxl |
| Backend | Python 3.12 |

## Project Structure
```
retirement-rag/
│
├── ingest.py                  # Interactive document ingestion CLI
├── query.py                   # Interactive query CLI
├── .env                       # API keys (never commit)
│
├── app/
│   ├── ingestion/
│   │   ├── parser_pdf.py      # PDF parser (universal, two-pass)
│   │   ├── parser_docx.py     # DOCX parser
│   │   ├── parser_excel.py    # Excel parser
│   │   ├── chunker.py         # Smart chunking with table support
│   │   └── pipeline.py        # Ingestion orchestrator
│   │
│   ├── registry/
│   │   └── registry.py        # Plan/document registry
│   │
│   ├── storage/
│   │   └── vector_store.py    # ChromaDB operations
│   │
│   ├── retrieval/
│   │   └── retriever.py       # Semantic search functions
│   │
│   └── llm/
│       ├── prompts.py         # System prompt + context builders
│       ├── tools.py           # Claude tool definitions
│       └── agent.py           # Agentic reasoning loop
│
└── data/
├── raw/                   # Original uploaded documents
├── processed/             # Chunked JSON files
├── vectordb/              # ChromaDB persistent storage
└── registry.json          # Plan registry
```
## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo>
cd retirement-rag
python -m venv venv
source venv/Scripts/activate  # Windows
# source venv/bin/activate    # Mac/Linux
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

Create `.env` file at project root:
ANTHROPIC_API_KEY=your_anthropic_api_key_here

Get your API key from: https://console.anthropic.com

### 4. Authenticate HuggingFace (for embedding model download)

```bash
hf auth login
```

The embedding model downloads once (~420MB) on first run and caches locally.

## Usage

### Ingest Documents

```bash
python ingest.py
```

Walk through the interactive prompts:
- Enter file path (PDF, DOCX, or XLSX)
- Select document tier (General/Regulatory or Plan-specific)
- Select or create a plan
- Choose document type (SPD, Amendment, Form 5500, etc.)
- Optionally enter effective date
- Confirm and ingest

Supported file types:
.pdf   → Retirement plan SPDs, IRS publications, regulations
.docx  → Plan documents, amendments
.xlsx  → Rule tables, schedules, contribution limits

### Query Documents

```bash
python query.py
```

- Select a plan context or choose general regulatory mode
- Ask questions in plain English
- Type `switch` to change plan context
- Type `exit` to quit

Example questions:
"What is the employer matching contribution percentage?"
"Who is eligible to participate?"
"What are the loan limits?"
"What are the vesting rules for employer contributions?"
"What is the IRS contribution limit for 2025?"

## Document Types Supported

| Type | Description |
|---|---|
| SPD | Summary Plan Description |
| Adoption Agreement | Plan adoption document |
| Plan Amendment | Changes to existing plan |
| IRS Determination Letter | IRS approval letter |
| Form 5500 | Annual plan filing |
| IRS Publication | IRS guidance documents |
| ERISA Regulation | DOL/ERISA regulations |
| Other | Any other document |

## How It Works

### Ingestion Pipeline

1. **Parse** — Extract text with hierarchy (section/subsection/page)
2. **Chunk** — Split into ~400 token pieces with 40 token overlap
3. **Embed** — Convert each chunk to a 768-dimension vector
4. **Store** — Save to ChromaDB with full metadata

### Query Pipeline

1. **User asks** a question (with optional plan context)
2. **Claude decides** which tool to call and what to search
3. **Retriever searches** ChromaDB for relevant chunks
4. **Claude reasons** — enough context? Call again or answer
5. **Structured response** — Answer + Sources + Confidence score

### Metadata on Every Chunk

```python
{
    "plan_id":       "PLAN_001",
    "plan_name":     "Microsoft Savings Plus 401(k)",
    "employer_id":   "EMP_001",
    "doc_type":      "SPD",
    "tier":          "plan_doc",
    "section":       "Eligibility",
    "subsection":    "Eligible Employees",
    "page_num":      3,
    "token_count":   221,
    "effective_date": "2025-01-01"
}
```

## Recommended Generic Documents

For best results, ingest these publicly available documents:
IRS Publication 560 — Retirement Plans for Small Business
https://www.irs.gov/pub/irs-pdf/p560.pdf
IRS Publication 575 — Pension and Annuity Income
https://www.irs.gov/pub/irs-pdf/p575.pdf
IRS 401(k) Fix-It Guide
https://www.irs.gov/pub/irs-pdf/p4531.pdf

## Upgrading to Azure (Production)

When Azure access is available, swap these components:

| Current (Dev) | Production |
|---|---|
| sentence-transformers (local) | Azure OpenAI text-embedding-3-large |
| ChromaDB (local file) | Azure AI Search (Hybrid) |
| FastAPI local | Azure Container Apps |
| Local file storage | Azure Blob Storage |

All swaps are one-class changes — pipeline code stays identical.

## Current Limitations

- DOCX parser works best with properly formatted Word documents
- Generic document quality affects answer quality for regulatory questions
- No web UI yet (Phase 2)
- No user authentication yet — all plans visible to all users (Phase 2)
- No org-level access controls yet — coming when FastAPI layer is built

## Multi-Plan Support

The system already supports multiple plans with full isolation:
- Each plan has a unique plan_id (PLAN_001, PLAN_002...)
- Retrieval filters strictly by plan_id — plans never bleed into each other
- Generic regulatory documents are shared across all plans
- Registry tracks employers, plans, and documents separately

What's not yet built is org-level authentication — controlling
which users or organizations can access which plans.
This is a Phase 2 item when the FastAPI backend is added.