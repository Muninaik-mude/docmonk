# DocMonk — Product & API Specification

> Last updated: 2026-03-06

---

## 1. Product Overview

DocMonk is an AI-powered legal document backend. It provides three core services:

| # | Service | Status |
|---|---|---|
| 1 | **Clause Analysis** — check contract clauses for compliance against an uploaded document | ✅ Live |
| 2 | **Document Q&A** — ask freeform questions on an uploaded document | ✅ Live |
| 3 | **Contract Generation** — generate a full contract document from structured input | 🔜 Planned |

---

## 2. Current API — Clause Analysis

### POST `/v1/analyze`

Accepts a base64-encoded document and a list of clauses. Runs full AI analysis pipeline.

#### Request Body

```json
{
  "document_base64": "<base64-encoded PDF/DOCX/MD/TXT>",
  "document_filename": "contract.pdf",
  "agreement_type": "Commercial Rental Agreement",
  "agreement_details": {
    "agreement_date": "2026-01-01",
    "city": "Mumbai",
    "state": "Maharashtra"
  },
  "parties": {
    "landlord": { "name": "...", "address": "...", "contact": "..." },
    "tenant":   { "name": "...", "company_name": "...", "address": "...", "contact": "..." }
  },
  "property": {
    "type": "Commercial",
    "area_sqft": 5000,
    "address": "..."
  },
  "clauses": [
    {
      "id": "confidentiality_survival_clause",
      "category": "confidentiality",
      "title": "Post-Termination Confidentiality",
      "value": "Confidentiality obligations shall survive termination..."
    }
  ]
}
```

**Notes:**
- `document_base64` preferred. `document_presigned_url` / `pdf_presigned_url` also accepted.
- Report format is always **Markdown**. No PDF or DOCX generation.
- Max 100 clauses per request. Max document size 100 MB.

#### Response (slim — optimized for speed)

```json
{
  "job_id": "uuid",
  "status": "completed",
  "can_resume": false,
  "progress": { "total": 5, "completed": 5, "failed": 0 },
  "report_md_base64": "<base64-encoded markdown report>",
  "summary_md_base64": "<base64-encoded markdown summary>"
}
```

**Design decision:** `analysis_summary` and report URLs are excluded from POST response.
Full results (including `analysis_summary` with `jurisdiction` + `clauses_analysis` dict) are available via `GET /v1/jobs/{job_id}`. This keeps POST fast for the caller.

---

### GET `/v1/jobs/{job_id}`

Poll job status and retrieve full results.

#### Response

```json
{
  "job_id": "uuid",
  "status": "completed",
  "can_resume": false,
  "progress": { "total": 5, "completed": 5, "failed": 0 },
  "analysis_summary": {
    "jurisdiction": {
      "jurisdiction": "Maharashtra, India",
      "agreement_type": "Commercial Rental Agreement",
      "applicable_laws": ["Transfer of Property Act, 1882"],
      "checklist": []
    },
    "clauses_analysis": {
      "confidentiality_survival_clause": {
        "clause_title": "Post-Termination Confidentiality",
        "clause_value": "...",
        "clause_category": "confidentiality",
        "result": "MATCH",
        "reason": "...",
        "relevant_text": "...",
        "color": null,
        "ai_added_text": null,
        "parties_obligated": ["Receiving Party"],
        "missing_values": [],
        "binding_strength": "MUST/SHALL",
        "key_dates_durations": [],
        "analysis_status": "COMPLETED",
        "retry_count": 0
      },
      "rent_payment_clause": {
        "clause_title": "Monthly Rent",
        "clause_value": "...",
        "clause_category": "financial",
        "result": "VIOLATION",
        "reason": "...",
        "relevant_text": "...",
        "color": "red",
        "ai_added_text": "Rent amount should be explicitly stated.",
        "parties_obligated": ["Tenant"],
        "missing_values": ["rent amount"],
        "binding_strength": "MUST/SHALL",
        "key_dates_durations": [],
        "analysis_status": "COMPLETED",
        "retry_count": 0
      }
    }
  },
  "report_pdf_url": "https://...",
  "report_markdown_url": "https://...",
  "created_at": "2026-03-02T10:00:00Z",
  "completed_at": "2026-03-02T10:00:45Z"
}
```

**Shape rationale:**
- `jurisdiction` sits once at `analysis_summary` level — not duplicated per clause
- `clauses_analysis` is a dict keyed by `clause_id` — O(1) client lookup vs O(n) list scan
- `clause_id` is the key, so it is not repeated inside the object (saves payload bytes)

---

### POST `/v1/jobs/{job_id}/resume`

Re-run only FAILED or PENDING clauses. Reuses extracted text from DB — no re-download.
Only callable when `status == "PARTIAL_FAILURE"`.

---

## 3. Document Q&A API

**Status: ✅ Live**

Session-based sequential chatbot. Upload a document once → get a `session_id` → ask questions one at a time. Answers stream in real-time via SSE. Full conversation history stored in DB per session.

> Full API reference: `QA_API_DOCS.md`

### Endpoints

| Method | Path | Purpose | Response |
|---|---|---|---|
| `POST` | `/v1/qa/sessions` | Create session + extract document text | JSON 201 |
| `GET` | `/v1/qa/sessions?user_id=xxx` | List all sessions for a user (paginated) | JSON 200 |
| `GET` | `/v1/qa/sessions/{session_id}` | Session detail + paginated message history | JSON 200 |
| `PATCH` | `/v1/qa/sessions/{session_id}` | Rename session | JSON 200 |
| `DELETE` | `/v1/qa/sessions/{session_id}` | Delete session + all messages + orphan doc cleanup | JSON 200 |
| `POST` | `/v1/qa/sessions/{session_id}/ask` | Ask a question — streams answer | SSE stream |
| `POST` | `/v1/qa/messages/{message_id}/retry` | Re-run a failed/partial answer | SSE stream |
| `POST` | `/v1/qa/messages/{message_id}/regenerate` | Regenerate answer with user feedback | SSE stream |

**Limits:** 1 document per session · 100 questions per session · 20 MB file size

---

### Response Envelope

All non-SSE responses are wrapped:

```json
{
  "status": 200,
  "success": true,
  "message": "...",
  "data": { ... }
}
```

---

### POST `/v1/qa/sessions`

Downloads document from presigned URL, extracts text, creates session. Document text is cached by `document_id` — re-submitting the same `document_id` skips download/extraction.

#### Request

```json
{
  "user_id": "user_abc123",
  "documents": [
    {
      "document_id": "uploads/leases/lease_v2.pdf",
      "s3_download_url": "https://storage.example.com/presigned/...",
      "document_filename": "Lease Agreement.pdf"
    }
  ]
}
```

- `document_id` = S3 object key. Acts as the deduplication cache key. Same key always means the same file.
- `s3_download_url` = presigned download URL, used only on cache miss.
- Exactly 1 document per session (`MAX_DOCS_PER_SESSION = 1`).

#### Response `201 Created`

```json
{
  "status": 201,
  "success": true,
  "message": "Session created successfully",
  "data": {
    "session_id": "525bdede-3851-4465-af73-15d2221409c2",
    "name": "",
    "user_id": "user_abc123",
    "documents": [
      {
        "document_id": "uploads/leases/lease_v2.pdf",
        "document_filename": "Lease Agreement.pdf",
        "file_type": "pdf",
        "char_count": 18400
      }
    ],
    "created_at": "2026-03-06T10:00:00.000000+00:00"
  }
}
```

`name` is empty on creation. Auto-filled with first question text (max 100 chars) after the first `/ask`.

---

### POST `/v1/qa/sessions/{session_id}/ask`

Ask one question. Answer is streamed via SSE. Saves message to DB after streaming completes.

#### Request

```json
{
  "question": "What is the notice period for termination?"
}
```

#### SSE Stream Response

Content-Type: `text/event-stream`

```
data: "The"

data: " notice"

data: " period"

data: " is"

data: " **90 days**,"

data: " as"

data: " stated"

data: " in"

data: " **Clause 9**."

event: done
data: {"success":true}
```

Each `data:` value is a JSON-encoded string — `JSON.parse(event.data)`.
After `event: done`, call `GET /sessions/{id}` to get the saved `message_id`.

#### SSE terminal events

| Event | Meaning |
|---|---|
| `event: done` | Complete answer saved to DB. |
| `event: partial` | Stream cut short; partial answer saved. Show with regenerate prompt. |
| `event: error` | DB save failed. Answer not saved. Prompt user to re-ask. |
| `: keepalive` | Heartbeat comment. Ignore. Sent ~every 15 s during provider waits. |

---

### GET `/v1/qa/sessions/{session_id}`

Returns session metadata and paginated message records. Each Q&A exchange produces **2 records**: a `user` record (question) and an `assistant` record (answer).

#### Query params

| Param | Default | Notes |
|---|---|---|
| `page` | `1` | 1-based |
| `page_size` | `10` | Max 100. This is individual records, not Q&A pairs. |

#### Response `200 OK`

```json
{
  "status": 200,
  "success": true,
  "message": "Session fetched successfully",
  "data": {
    "session_id": "525bdede-3851-4465-af73-15d2221409c2",
    "name": "What is the notice period for termination?",
    "user_id": "user_abc123",
    "documents": [
      {
        "document_id": "uploads/leases/lease_v2.pdf",
        "document_filename": "Lease Agreement.pdf",
        "file_type": "pdf",
        "char_count": 18400
      }
    ],
    "created_at": "2026-03-06T10:00:00.000000+00:00",
    "updated_at": "2026-03-06T10:10:00.000000+00:00",
    "pagination_info": {
      "total_records": 6,
      "total_pages": 1,
      "page_size": 20,
      "current_page": 1,
      "next_page": null,
      "prev_page": null
    },
    "records": [
      {
        "message_id": "7f3a1b2c-0001-4000-8000-000000000001",
        "role": "user",
        "content": "What is the notice period for termination?",
        "created_at": "2026-03-06 10:05:00.000000+00:00"
      },
      {
        "message_id": "f3928795-467a-4384-bbd2-30ffb9bf8a1d",
        "role": "assistant",
        "content": "The notice period is **90 days**, as stated in **Clause 9 – Termination**.",
        "relevant_excerpt": "Either party may terminate this agreement by giving 90 days written notice to the other party.",
        "page_hint": 3,
        "created_at": "2026-03-06 10:05:12.000000+00:00",
        "updated_at": "2026-03-06 10:05:12.000000+00:00"
      }
    ]
  }
}
```

**Assistant record fields:**

| Field | Description |
|---|---|
| `message_id` | Use this for `/retry` and `/regenerate`. |
| `content` | AI answer in Markdown. |
| `relevant_excerpt` | AI-extracted verbatim sentence/paragraph from the document most relevant to the question. Empty string if nothing relevant found. |
| `page_hint` | 1-based page number where `relevant_excerpt` was located. `null` if unresolvable. |

---

### POST `/v1/qa/messages/{message_id}/retry`

Re-runs AI only for answers with status `"failed"` or `"partial"`. Streams the new answer. Same SSE format as `/ask`.

No request body. Returns JSON (not stream) if nothing is retryable:

```json
{
  "status": 200,
  "success": true,
  "message": "No failed or partial answers found. Nothing to retry.",
  "data": { "status": "no_failures" }
}
```

---

### POST `/v1/qa/messages/{message_id}/regenerate`

Regenerates the answer using user feedback. The new answer **replaces** the original in DB.

#### Request

```json
{
  "reason": "Too vague. I need the exact clause number and number of days."
}
```

Streams the improved answer. Same SSE format as `/ask`.

---

### DB Models

#### `QADocument`
| Field | Type | Notes |
|---|---|---|
| `id` | BigAutoField PK | |
| `document_id` | CharField unique | S3 object key — deduplication key |
| `s3_download_url` | TextField | Original presigned URL |
| `document_filename` | CharField | Display name |
| `file_type` | CharField | pdf / docx / md / txt / pptx |
| `full_text` | TextField | Extracted document text |
| `char_count` | IntegerField | Length of full_text |
| `char_page_map` | JSONField | `[{page, start, end}]` for page resolution |
| `created_at` | DateTimeField | Auto |

#### `QASession`
| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | Session identifier |
| `user_id` | CharField | Caller-supplied user identifier |
| `name` | CharField | Auto-filled from first question; renameable |
| `created_at` | DateTimeField | Auto |
| `updated_at` | DateTimeField | Auto |

#### `QASessionDocument`
| Field | Type | Notes |
|---|---|---|
| `id` | BigAutoField PK | |
| `session` | FK → `QASession` CASCADE | |
| `document` | FK → `QADocument` | |
| `position` | IntegerField | Always 0 (single-doc sessions) |

#### `QAMessage`
| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | Assistant message ID — used for retry/regenerate |
| `question_message_id` | UUID | Separate ID exposed as the user record's `message_id` |
| `session` | FK → `QASession` CASCADE | |
| `question` | TextField | The question asked |
| `answers_per_document` | JSONField | Always a single-element list `[{document_id, document_filename, position, answer, relevant_excerpt, page_hint, status}]` |
| `created_at` | DateTimeField | Auto |
| `updated_at` | DateTimeField | Auto |

`answers_per_document[0].status` values: `"success"` · `"partial"` · `"failed"`

---

### AI Pipeline (per `/ask` call)

1. `qa_service.find_relevant_window(question, full_text)` — keyword-based sliding window scan → best 5,000-char context slice
2. Fetch last 10 Q&A exchanges as conversation history (oldest first) — enables follow-up questions
3. `groq_service.answer_question_stream(question, context, history)` — streams Markdown answer chunks
4. After stream ends: `groq_service.extract_relevant_excerpt(question, context)` — AI extracts verbatim relevant passage (offloaded to gevent threadpool)
5. `qa_service.find_page_for_excerpt(relevant_excerpt, full_text, char_page_map)` — resolves 1-based page number
6. `QAMessage` saved atomically; session auto-named from first question

---

### Service & URL Location

- Service: `qa/services/qa_service.py`
- Views: `qa/views.py`
- URLs registered under `/v1/qa/` in `qa/urls.py`, included in `config/urls.py`

---

## 4. Planned API — Contract Generation

### POST `/v1/contracts/generate`

Generate a complete, legally-structured contract document from structured input. Returns the generated contract as a base64-encoded file.

#### Use Cases
- Generate a Commercial Rental Agreement between two parties
- Generate an NDA based on party names and scope
- Generate an Employment Agreement with specific clauses

#### Request Body

```json
{
  "agreement_type": "Commercial Rental Agreement",
  "language": "en",
  "agreement_details": {
    "agreement_date": "2026-04-01",
    "city": "Mumbai",
    "state": "Maharashtra",
    "duration_months": 24,
    "start_date": "2026-04-01"
  },
  "parties": {
    "landlord": {
      "name": "Ravi Kumar",
      "address": "123 MG Road, Mumbai",
      "contact": "+91-9876543210"
    },
    "tenant": {
      "name": "TechCorp Pvt Ltd",
      "company_name": "TechCorp Pvt Ltd",
      "authorized_signatory": "Priya Shah",
      "address": "456 BKC, Mumbai",
      "contact": "+91-9123456789"
    }
  },
  "property": {
    "type": "Commercial Office",
    "area_sqft": 2500,
    "address": "Unit 12, Tech Park, Andheri East, Mumbai"
  },
  "clauses": [
    {
      "id": "rent_payment",
      "category": "financial",
      "title": "Monthly Rent",
      "value": "Monthly rent of INR 1,50,000 payable by the 5th of each month."
    }
  ],
  "output_format": "docx"
}
```

**Notes:**
- `clauses` are optional — if omitted, AI generates standard clauses for the agreement type
- `output_format`: `"pdf"` | `"docx"` | `"markdown"`
- `language`: `"en"` (default) — future: `"hi"`, `"mr"` etc.

#### Response

```json
{
  "contract_id": "uuid",
  "agreement_type": "Commercial Rental Agreement",
  "output_format": "docx",
  "document_base64": "<base64-encoded DOCX>",
  "clauses_generated": 12,
  "jurisdiction_detected": "Maharashtra, India"
}
```

#### Pipeline

1. Validate request (parties, agreement_type required)
2. Detect jurisdiction from state/city
3. AI call 1: generate standard clause list for this agreement type + jurisdiction (if clauses not provided)
4. AI call 2: expand each clause into full legal text incorporating party names, property details, dates
5. Compose full document (DOCX/PDF/Markdown) via `report_service`
6. Return base64-encoded document

#### Design Decisions (TBD — to finalize before implementation)
- Store generated contracts in DB? (`GeneratedContract` model with parties, clauses, output)
- R2 storage for generated files?
- Template-based generation vs pure AI generation?
- Clause library — predefined standard clauses per agreement type?

---

## 5. Response Time Optimization Strategies

### Current bottlenecks (POST /v1/analyze)
1. Sequential jurisdiction detection before parallel clause analysis
2. Report generation (PDF especially) after AI analysis
3. File upload to R2 after report generation
4. Large response payload (analysis_summary) serialization

### Applied strategies
| Strategy | Status | Impact |
|---|---|---|
| Parallel clause analysis (ThreadPoolExecutor, 3 workers) | ✅ Done | High |
| Multi-provider AI pool (4 clients, round-robin) | ✅ Done | Medium |
| Slim POST response (no analysis_summary, no URLs) | ✅ Done | Medium |
| 5,000-char context window per clause (not full doc) | ✅ Done | High |
| Merged jurisdiction + clause AI calls | ✅ Done | Medium |
| Store full_text in DB (resume without re-download) | ✅ Done | Medium |

### Future strategies to consider
| Strategy | Complexity | Impact |
|---|---|---|
| Increase parallel workers to 5–8 (monitor rate limits) | Low | Medium |
| Skip PDF annotation on first call, generate lazily via GET | Low | Medium |
| Async job processing (Celery/RQ) — POST returns `job_id` immediately, client polls | High | Very High |
| Jurisdiction cache by document hash (same doc → skip detection) | Medium | Low |
| Stream AI responses and process chunks | High | Medium |
| Pre-warm AI clients on server startup | Low | Low |

---

## 6. Data Models Summary

```
AnalysisJob          — top-level job (status, full_text, counters)
  └── JobClause[]    — one per clause (state machine)
        └── ClauseResult  — AI output (result, reason, parties, dates...)
  └── JobJurisdiction      — jurisdiction + laws + checklist
  └── ClauseConflict[]     — conflict pairs (dormant)
  └── JobReport[]          — stored report files
```

---

## 7. Compliance Status Reference

| Status | Color | Meaning |
|---|---|---|
| `MATCH` | green | Clause fully satisfied in document |
| `NOT_FOUND` | blue | Clause absent from document |
| `PARTIALLY_SATISFIED` | orange | Clause present but vague/incomplete |
| `VIOLATION` | red | Document contradicts the clause |

---

## 8. Deployment

- **Platform:** Railway
- **Process:** `gunicorn config.wsgi:application --bind 0.0.0.0:8080 --workers 2 --timeout 120`
- **DB:** PostgreSQL via `DATABASE_URL` env var
- **Static files:** WhiteNoise
- **Storage:** Cloudflare R2 (`STORAGE_BACKEND=r2`) or local fallback
