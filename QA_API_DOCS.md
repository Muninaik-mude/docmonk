# DocMonk — Document Q&A API Reference

> Backend reference for all Q&A endpoints. Covers every API, exact request/response shapes, SSE event formats, and field descriptions.

---

## Table of Contents

1. [How the Flow Works](#1-how-the-flow-works)
2. [Base URL](#2-base-url)
3. [API List](#3-api-list)
4. [Response Envelope](#4-response-envelope)
5. [Create Session](#5-create-session)
6. [List Sessions](#6-list-sessions)
7. [Session Detail + Message History](#7-session-detail--message-history)
8. [Rename Session](#8-rename-session)
9. [Delete Session](#9-delete-session)
10. [Ask a Question — SSE Streaming](#10-ask-a-question--sse-streaming)
11. [Retry a Failed Answer — SSE Streaming](#11-retry-a-failed-answer--sse-streaming)
12. [Regenerate an Answer — SSE Streaming](#12-regenerate-an-answer--sse-streaming)
13. [SSE Event Reference](#13-sse-event-reference)
14. [Error Responses](#14-error-responses)

---

## 1. How the Flow Works

```
1. POST /sessions          → upload document, get session_id
2. POST /sessions/{id}/ask → ask a question, stream SSE answer
3. GET  /sessions/{id}     → fetch full conversation history
4. POST /messages/{id}/retry       → re-run a failed answer (SSE)
5. POST /messages/{id}/regenerate  → improve an answer with feedback (SSE)
6. PATCH  /sessions/{id}   → rename session
7. DELETE /sessions/{id}   → delete session + all messages
```

**Key rules:**
- A session must be created before any questions can be asked.
- Each session belongs to one `user_id` (plain string — no auth).
- Session name is **auto-set** from the first question asked. Can be renamed.
- All AI responses (ask, retry, regenerate) are **streamed via SSE**.
- Conversation history (last 10 exchanges) is passed to the AI on every question.
- Each session is limited to **1 document** and **100 questions**.
- Document text is extracted once and **cached by `document_id`** — re-submitting the same `document_id` skips download/extraction entirely.

---

## 2. Base URL

```
https://docmonk-production.up.railway.app/v1/qa
```

All endpoints below are relative to this base URL.

---

## 3. API List

| # | Method | Endpoint | Purpose | Response Type |
|---|--------|----------|---------|---------------|
| 1 | `POST` | `/sessions` | Upload doc + create session | JSON 201 |
| 2 | `GET` | `/sessions?user_id=xxx` | List all sessions for a user (paginated) | JSON 200 |
| 3 | `GET` | `/sessions/{session_id}` | Session detail + paginated message history | JSON 200 |
| 4 | `PATCH` | `/sessions/{session_id}` | Rename session | JSON 200 |
| 5 | `DELETE` | `/sessions/{session_id}` | Delete session + all messages | JSON 200 |
| 6 | `POST` | `/sessions/{session_id}/ask` | Ask a question | **SSE Stream** |
| 7 | `POST` | `/messages/{message_id}/retry` | Retry a failed/partial answer | **SSE Stream** |
| 8 | `POST` | `/messages/{message_id}/regenerate` | Regenerate with user feedback | **SSE Stream** |

---

## 4. Response Envelope

Every non-SSE response is wrapped in this envelope:

```json
{
  "status": 200,
  "success": true,
  "message": "Human-readable result message",
  "data": { ... }
}
```

On error:

```json
{
  "status": 400,
  "success": false,
  "message": "Validation failed.",
  "data": { ... }
}
```

All payload shapes described below refer to the `data` object inside the envelope.

---

## 5. Create Session

> Downloads the document from the provided URL, extracts its text, and creates a session. If the same `document_id` was submitted before, the cached text is reused — no re-download.

**`POST /sessions`**

### Request

```json
{
  "user_id": "user_abc123",
  "documents": [
    {
      "document_id": "uploads/leases/lease_v2.pdf",
      "s3_download_url": "https://your-storage.com/presigned-url/...",
      "document_filename": "Lease Agreement.pdf"
    }
  ]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `user_id` | string | ✅ | Any string, max 500 chars |
| `documents` | array | ✅ | Exactly 1 document |
| `documents[].document_id` | string | ✅ | S3 object key. Used as the deduplication/cache key. Same key = same file, extraction is skipped. |
| `documents[].s3_download_url` | string (URL) | ✅ | Presigned download URL. Only used on cache miss. |
| `documents[].document_filename` | string | ❌ | Display name. Default: `"document"` |

**Supported file types:** PDF, DOCX, TXT, MD, PPT, PPTX

**File size limit:** 20 MB

### Response `201 Created`

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
    "created_at": "2026-03-04T10:00:00.000000+00:00"
  }
}
```

| Field | Description |
|-------|-------------|
| `session_id` | UUID. Required for all subsequent calls on this session. |
| `name` | Empty on creation. Auto-filled with the first question text (max 100 chars) after the first `/ask`. |
| `documents[].char_count` | Total character count of extracted text. Useful for estimating document size. |

### Error Responses

| Status | When |
|--------|------|
| `400` | Missing/invalid fields, download failed, unsupported file type, no extractable text |
| `413` | File exceeds 20 MB limit |

---

## 6. List Sessions

> Returns all sessions for a user, newest first, paginated.

**`GET /sessions?user_id=user_abc123&page=1&page_size=10`**

### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `user_id` | string | — | **Required.** User to fetch sessions for. |
| `page` | int | `1` | Page number (1-based). |
| `page_size` | int | `10` | Records per page. Max `100`. |

### Response `200 OK`

```json
{
  "status": 200,
  "success": true,
  "message": "Sessions fetched successfully",
  "data": {
    "user_id": "user_abc123",
    "pagination_info": {
      "total_records": 12,
      "total_pages": 2,
      "page_size": 10,
      "current_page": 1,
      "next_page": 2,
      "prev_page": null
    },
    "records": [
      {
        "session_id": "525bdede-3851-4465-af73-15d2221409c2",
        "name": "What is the notice period for termination?",
        "user_id": "user_abc123",
        "documents": [
          {
            "document_id": "uploads/leases/lease_v2.pdf",
            "document_filename": "Lease Agreement.pdf",
            "file_type": "pdf"
          }
        ],
        "created_at": "2026-03-04T10:00:00.000000+00:00",
        "updated_at": "2026-03-04T10:05:00.000000+00:00"
      }
    ]
  }
}
```

| Field | Description |
|-------|-------------|
| `pagination_info.next_page` | Next page number, or `null` if on last page. |
| `pagination_info.prev_page` | Previous page number, or `null` if on first page. |
| `records` | Array of session objects, newest first. |

### Error Responses

| Status | When |
|--------|------|
| `400` | `user_id` missing or exceeds 500 chars |

---

## 7. Session Detail + Message History

> Returns session metadata and its paginated message history. Each Q&A pair produces **two records**: one `user` record (the question) and one `assistant` record (the answer).

**`GET /sessions/525bdede-3851-4465-af73-15d2221409c2?page=1&page_size=20`**

### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page` | int | `1` | Page number (1-based). |
| `page_size` | int | `10` | Number of **individual records** per page (not Q&A pairs). Max `100`. |

> **Note on pagination:** Each Q&A exchange produces 2 records (user + assistant). `total_records` = number of questions × 2. Set `page_size=20` to fetch 10 full Q&A pairs per page.

### Response `200 OK`

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
    "created_at": "2026-03-04T10:00:00.000000+00:00",
    "updated_at": "2026-03-04T10:10:00.000000+00:00",
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
        "created_at": "2026-03-04 10:05:00.000000+00:00"
      },
      {
        "message_id": "f3928795-467a-4384-bbd2-30ffb9bf8a1d",
        "role": "assistant",
        "content": "The notice period is **90 days**, as stated in **Clause 9 – Termination**.\n\n> \"Either party may terminate the agreement by giving **90 days written notice**.\"\n\nSo the exact notice period is **90 days**.",
        "relevant_excerpt": "Either party may terminate this agreement by giving 90 days written notice to the other party.",
        "page_hint": 3,
        "created_at": "2026-03-04 10:05:12.000000+00:00",
        "updated_at": "2026-03-04 10:05:12.000000+00:00"
      }
    ]
  }
}
```

### Record Fields

**User record:**

| Field | Description |
|-------|-------------|
| `message_id` | ID of the question record. |
| `role` | Always `"user"`. |
| `content` | The question text. |
| `created_at` | When the question was asked. |

**Assistant record:**

| Field | Description |
|-------|-------------|
| `message_id` | UUID. Use this ID for `/retry` and `/regenerate`. |
| `role` | Always `"assistant"`. |
| `content` | AI answer in **Markdown format**. Render with a Markdown renderer. |
| `relevant_excerpt` | Verbatim sentence or paragraph extracted by AI from the document that is most relevant to the question. Empty string if nothing relevant was found. |
| `page_hint` | 1-based page number where `relevant_excerpt` was located in the original document. `null` if not resolvable (e.g. plain text files with no page structure). |
| `created_at` | Timestamp of the original answer. |
| `updated_at` | Timestamp of the last update (retry or regenerate). |

> `content` is Markdown — use **bold**, bullet points, and `> blockquote` for direct document citations.

### Error Responses

| Status | When |
|--------|------|
| `404` | Session not found |

---

## 8. Rename Session

**`PATCH /sessions/525bdede-3851-4465-af73-15d2221409c2`**

### Request

```json
{
  "name": "Lease Agreement – Termination Clauses"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | ✅ | Max 200 chars |

### Response `200 OK`

```json
{
  "status": 200,
  "success": true,
  "message": "Session renamed successfully",
  "data": {
    "session_id": "525bdede-3851-4465-af73-15d2221409c2",
    "name": "Lease Agreement – Termination Clauses",
    "updated_at": "2026-03-04T10:15:00.000000+00:00"
  }
}
```

### Error Responses

| Status | When |
|--------|------|
| `400` | Validation failed (missing or too-long name) |
| `404` | Session not found |

---

## 9. Delete Session

> Permanently deletes the session, all its messages, and any document cache entries that are no longer referenced by any other session.

**`DELETE /sessions/525bdede-3851-4465-af73-15d2221409c2`**

No request body.

### Response `200 OK`

```json
{
  "status": 200,
  "success": true,
  "message": "Session deleted successfully",
  "data": {
    "session_id": "525bdede-3851-4465-af73-15d2221409c2"
  }
}
```

### Error Responses

| Status | When |
|--------|------|
| `404` | Session not found |

---

## 10. Ask a Question — SSE Streaming

> The core chat endpoint. Sends a question and streams the answer chunk by chunk. Saves the message to DB once streaming is complete.

**`POST /sessions/525bdede-3851-4465-af73-15d2221409c2/ask`**

### Request

```json
{
  "question": "What is the notice period for termination?"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `question` | string | ✅ | Max 2000 chars |

### How the AI answers

1. A 5,000-character keyword-matched window is extracted from the document text as context.
2. The last 10 Q&A exchanges from the session are sent as conversation history — so follow-up questions work correctly.
3. AI streams a Markdown-formatted answer.
4. After streaming ends, an AI call extracts the single most relevant verbatim passage from the context window → saved as `relevant_excerpt`.
5. The page number of that excerpt is resolved → saved as `page_hint`.
6. The full message is saved atomically to the database.

### Response — SSE Stream

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

- Each `data:` value is a **JSON-encoded string** — call `JSON.parse(event.data)` to get the text chunk.
- Append chunks in order to build the full answer as it streams.
- `event: done` means streaming is complete and the message **has been saved** to the database.
- After `done`, call `GET /sessions/{id}` to retrieve the saved `message_id` (needed for retry/regenerate).

### Error Responses (JSON, before stream starts)

| Status | When |
|--------|------|
| `400` | Validation failed, session has no documents |
| `404` | Session not found |
| `429` | Session has reached the 100-question limit |

---

## 11. Retry a Failed Answer — SSE Streaming

> Re-runs the AI for answers with status `"failed"` or `"partial"`. Successful answers in the same message are preserved as-is.

**`POST /messages/f3928795-467a-4384-bbd2-30ffb9bf8a1d/retry`**

No request body.

### When to use

Call this when an assistant record in session detail has:
- Empty `content`, or
- `content` indicating a failure (e.g. stream cut short mid-sentence)

The backend tracks answer status internally — it only re-runs answers marked `"failed"` or `"partial"`.

### Response — SSE Stream (same format as Ask)

```
data: "The"

data: " notice"

data: " period"

data: " is"

data: " **90 days**"

event: done
data: {"success":true}
```

After `event: done`, refresh the message via `GET /sessions/{id}` to get the updated answer.

### Special Case — Nothing to Retry

If no answers in the message are failed or partial, returns JSON (not a stream):

```json
{
  "status": 200,
  "success": true,
  "message": "No failed or partial answers found. Nothing to retry.",
  "data": {
    "status": "no_failures"
  }
}
```

### Error Responses

| Status | When |
|--------|------|
| `400` | Could not find documents for failed answers |
| `404` | Message not found |

---

## 12. Regenerate an Answer — SSE Streaming

> Re-runs the AI with the user's feedback as additional context. The new answer **replaces** the original in the database.

**`POST /messages/f3928795-467a-4384-bbd2-30ffb9bf8a1d/regenerate`**

### Request

```json
{
  "reason": "The answer was too vague. I need the exact notice period in days and which clause mentions it."
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `reason` | string | ✅ | Min 3 chars, max 1000 chars |

### How it works

The AI receives:
- The original question
- The original answer (so it knows what was unsatisfactory)
- The user's reason for regeneration
- The same document context window

It produces an improved, targeted response that directly addresses the user's concern.

### Response — SSE Stream

```
data: "Based"

data: " on"

data: " **Clause 9 – Termination**"

data: " of"

data: " the"

data: " agreement:\n\n"

data: "> \"Either party may terminate"

data: " the agreement by giving"

data: " **90 days written notice**.\"\n\n"

data: "The exact notice period is"

data: " **90 days**."

event: done
data: {"success":true}
```

The regenerated answer replaces the original. After `event: done`, refresh via `GET /sessions/{id}`.

### Error Responses

| Status | When |
|--------|------|
| `400` | Validation failed, no answer found to regenerate |
| `404` | Message not found, document not found |

---

## 13. SSE Event Reference

All streaming endpoints (`/ask`, `/retry`, `/regenerate`) use the same SSE event types:

| Event | Format | Meaning |
|-------|--------|---------|
| Text chunk | `data: "word or phrase"\n\n` | Append to answer buffer. `JSON.parse(event.data)` to get the string. |
| Keepalive | `: keepalive\n\n` | Server heartbeat sent during AI provider wait (~every 15 s). Ignore — prevents proxy idle-timeout. |
| Stream complete | `event: done`<br>`data: {"success":true}\n\n` | All chunks received. Message **saved** to DB. Fetch session detail to get `message_id`. |
| Partial answer | `event: partial`<br>`data: {"success":true,"status":"partial","message":"..."}\n\n` | Stream was cut short (network/rate-limit). Partial answer **saved** to DB. Show it with a regenerate prompt. |
| Save failed | `event: error`<br>`data: {"success":false,"message":"Answer could not be saved. Please try again."}\n\n` | Stream completed but DB save failed. Answer was **not saved**. Prompt user to retry the question. |

---

## 14. Error Responses

All error responses follow the standard envelope:

```json
{
  "status": 404,
  "success": false,
  "message": "Session '525bdede-...' not found.",
  "data": null
}
```

### Validation errors (400)

When `data` is present on a 400, it contains field-level errors:

```json
{
  "status": 400,
  "success": false,
  "message": "Validation failed.",
  "data": {
    "question": ["This field is required."]
  }
}
```

### Common status codes

| Status | Meaning |
|--------|---------|
| `200` | OK |
| `201` | Created (session created) |
| `400` | Bad request — invalid input, download failure, extraction failure |
| `404` | Resource not found |
| `413` | File too large (> 20 MB) |
| `429` | Session question limit reached (100 questions max) |
