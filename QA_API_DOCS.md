# DocMonk — Document Q&A API Reference

> This document is for reference.
> It covers every API in the Q&A feature, the full user workflow, and exact request/response shapes.

---

## Table of Contents

1. [How the Flow Works](#1-how-the-flow-works)
2. [Base URL](#2-base-url)
3. [API List](#3-api-list)
4. [Create Session (Upload Document)](#4-create-session-upload-document)
5. [List Sessions](#5-list-sessions)
6. [Session Detail + Message History](#6-session-detail--message-history)
7. [Rename Session](#7-rename-session)
8. [Delete Session](#8-delete-session)
9. [Ask a Question — SSE Streaming](#9-ask-a-question--sse-streaming)
10. [Retry a Failed Answer — SSE Streaming](#10-retry-a-failed-answer--sse-streaming)
11. [Regenerate an Answer — SSE Streaming](#11-regenerate-an-answer--sse-streaming)
12. [SSE Streaming — How to Handle on Frontend](#12-sse-streaming--how-to-handle-on-frontend)
13. [UI Screens Needed](#13-ui-screens-needed)

---

## 1. How the Flow Works

```
┌─────────────────────────────────────────────────────────────────┐
│                      USER JOURNEY                               │
│                                                                 │
│  1. User uploads a document                                     │
│         ↓                                                       │
│  2. Frontend calls POST /sessions  →  gets session_id           │
│         ↓                                                       │
│  3. Session is created. Document text is extracted & cached.    │
│         ↓                                                       │
│  4. User types a question                                       │
│         ↓                                                       │
│  5. Frontend calls POST /sessions/{id}/ask                      │
│         ↓                                                       │
│  6. Server streams the answer word-by-word (SSE)                │
│         ↓                                                       │
│  7. Answer appears live on screen (chatbot style)               │
│         ↓                                                       │
│  8. User can:                                                   │
│       • Ask another question  →  repeat from step 4            │
│       • Retry (if answer failed)  →  POST /messages/{id}/retry  │
│       • Regenerate (unsatisfied)  →  POST /messages/{id}/regenerate │
│       • Rename the session  →  PATCH /sessions/{id}            │
│       • Delete the session  →  DELETE /sessions/{id}           │
│       • Come back later and see history  →  GET /sessions/{id} │
└─────────────────────────────────────────────────────────────────┘
```

**Key rules:**
- A session must be created (document uploaded) **before** any questions can be asked.
- Each session belongs to one `user_id` (a plain string — no login required from the API side).
- Session name is **auto-set** from the first question asked. User can rename it later.
- All AI responses (ask, retry, regenerate) are **streamed** — text appears word by word.
- Full conversation history is stored and retrievable via the session detail API.

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
| 1 | `POST` | `/sessions` | Upload doc + create session | JSON |
| 2 | `GET` | `/sessions?user_id=xxx` | List all sessions for a user | JSON |
| 3 | `GET` | `/sessions/{session_id}` | Session detail + full chat history | JSON |
| 4 | `PATCH` | `/sessions/{session_id}` | Rename a session | JSON |
| 5 | `DELETE` | `/sessions/{session_id}` | Delete session + all messages | JSON |
| 6 | `POST` | `/sessions/{session_id}/ask` | Ask a question (streaming) | **SSE Stream** |
| 7 | `POST` | `/messages/{message_id}/retry` | Retry a failed answer (streaming) | **SSE Stream** |
| 8 | `POST` | `/messages/{message_id}/regenerate` | Regenerate with user feedback (streaming) | **SSE Stream** |

---

## 4. Create Session (Upload Document)

> Call this when the user uploads a document. The document is downloaded from the provided URL, text is extracted, and a session is created. The same document is never extracted twice — it is cached by `document_id`.

**`POST /sessions`**

### Request

```json
{
  "user_id": "user_abc123",
  "documents": [
    {
      "document_id": "doc_lease_001",
      "s3_download_url": "https://your-storage.com/presigned-url/lease.pdf",
      "document_filename": "Lease Agreement.pdf"
    }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | ✅ | Unique identifier for the user (any string) |
| `documents` | array | ✅ | List of documents (currently max 1) |
| `documents[].document_id` | string | ✅ | The S3 object key for this document. Used as the cache key — if the same key is submitted again, the document is NOT re-downloaded or re-extracted; the cached text is reused. |
| `documents[].s3_download_url` | string (URL) | ✅ | Presigned URL to download the file (used only on cache miss) |
| `documents[].document_filename` | string | ❌ | Display name of the file (default: "document") |

**Supported file types:** PDF, DOCX, TXT, PPT, PPTX

### Response `201 Created`

```json
{
  "session_id": "525bdede-3851-4465-af73-15d2221409c2",
  "name": "",
  "user_id": "user_abc123",
  "documents": [
    {
      "document_id": "doc_lease_001",
      "document_filename": "Lease Agreement.pdf",
      "file_type": "pdf",
      "char_count": 18400,
    }
  ],
  "created_at": "2026-03-04T10:00:00.000000"
}
```

> `name` is empty on creation. It gets auto-filled with the first question when the user asks something.

### Error Responses

| Status | When |
|--------|------|
| `400` | Missing fields, download failed, no extractable text |
| `413` | File exceeds size limit |

---

## 5. List Sessions

> Load all past sessions for a user when they open the app (sidebar/history list).

**`GET /sessions?user_id=user_abc123`**

### Response `200 OK`

```json
{
  "user_id": "user_abc123",
  "sessions": [
    {
      "session_id": "525bdede-3851-4465-af73-15d2221409c2",
      "name": "What is the notice period for termination?",
      "user_id": "user_abc123",
      "documents": [
        {
          "document_id": "doc_lease_001",
          "document_filename": "Lease Agreement.pdf",
          "file_type": "pdf"
        }
      ],
      "created_at": "2026-03-04T10:00:00.000000",
      "updated_at": "2026-03-04T10:05:00.000000"
    }
  ]
}
```

> Sessions are ordered newest first. Use `name` to display the session title in the sidebar.

---

## 6. Session Detail + Message History

> Load a full conversation when the user clicks on a past session.

**`GET /sessions/525bdede-3851-4465-af73-15d2221409c2`**

### Response `200 OK`

```json
{
  "session_id": "525bdede-3851-4465-af73-15d2221409c2",
  "name": "What is the notice period for termination?",
  "user_id": "user_abc123",
  "documents": [
    {
      "document_id": "doc_lease_001",
      "document_filename": "Lease Agreement.pdf",
      "file_type": "pdf",
      "char_count": 18400,
    }
  ],
  "messages": [
    {
      "message_id": "2e3fb6f2-5640-4312-85cf-7bd2d0d49265",
      "role": "user",
      "content": "what do you know about flutter",
      "created_at": "2026-01-19 02:30:33.045952"
    },
    {
      "message_id": "2d7dac95-18cc-454b-9f78-1f4425de521a",
      "role": "assistant",
      "content": "I'm sorry, but I couldn't find any relevant context in the knowledge base regarding Flutter. Ifhave specific questions or need information on a particular aspect of Flutter, feel free to ask!",
      "created_at": "2026-01-19 02:31:04.984518"
    }    
  ],
  "created_at": "2026-03-04T10:00:00.000000",
  "updated_at": "2026-03-04T10:10:00.000000"
}
```

**Message object fields:**

| Field | Description |
|-------|-------------|
| `message_id` | Unique ID — needed for retry and regenerate |
| `role` | Always `"assistant"` — all stored messages are AI responses |
| `question` | The question the user asked |
| `content` | The AI's answer in **Markdown format** |
| `relevant_excerpt` | Direct quote from the document (may be empty) |
| `created_at` | Timestamp of when the question was asked |

> `content` is Markdown — render it with a Markdown renderer on the frontend for bold text, bullet points, blockquotes, etc.

---

## 7. Rename Session

> Let the user edit the session name (shown in the sidebar).

**`PATCH /sessions/525bdede-3851-4465-af73-15d2221409c2`**

### Request

```json
{
  "name": "Lease Agreement – Termination Clauses"
}
```

### Response `200 OK`

```json
{
  "session_id": "525bdede-3851-4465-af73-15d2221409c2",
  "name": "Lease Agreement – Termination Clauses",
  "updated_at": "2026-03-04T10:15:00.000000"
}
```

---

## 8. Delete Session

> Permanently delete a session and all its messages.

**`DELETE /sessions/525bdede-3851-4465-af73-15d2221409c2`**

No request body needed.

### Response `200 OK`

```json
{
  "status": "deleted",
  "session_id": "525bdede-3851-4465-af73-15d2221409c2"
}
```

---

## 9. Ask a Question — SSE Streaming

> The core chat API. Sends a question and streams the answer word by word.

**`POST /sessions/525bdede-3851-4465-af73-15d2221409c2/ask`**

### Request

```json
{
  "question": "What is the notice period for termination?"
}
```

### Response — SSE Stream

The response is a **text/event-stream**. Each word/chunk arrives as a separate SSE event:

```
data: "The"

data: " notice"

data: " period"

data: " is"

data: " **90 days**,"

data: " as"

data: " stated"

data: " in"

data: " **Clause"

data: " 9**."

event: done
data: {"success":true}
```

- Every `data:` line contains a **JSON-encoded string** — call `JSON.parse(event.data)` to get the text.
- The final `event: done` signals the stream is complete and the message has been saved to the database.
- After `done`, fetch the session detail (`GET /sessions/{id}`) to get the `message_id` for the new message (needed for retry/regenerate).

### Answer format

The AI responds in **Markdown**. Examples of what you'll see:

```markdown
The notice period is **90 days**, as stated in **Clause 9 – Termination**.

> "Either party may terminate the agreement by giving **90 days written notice**."

So the exact notice period is **90 days**.
```

```markdown
According to **Clause 5 – Maintenance**:

- **Tenant** is responsible for day-to-day upkeep
- **Landlord** is responsible for structural repairs
```

---

## 10. Retry a Failed Answer — SSE Streaming

> When an answer fails (due to API rate limits or errors), a retry button should appear. This re-runs the AI for that specific message.

**When to show the retry button:** If the session detail's `message.content` is empty or contains error text like `"Analysis failed: ..."`.

**`POST /messages/a264a501-8d18-4714-815c-fb29b8442041/retry`**

No request body needed.

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

- Streams the new answer exactly like `/ask`.
- Only failed answers are re-run. Successful answers in the same message are preserved.
- After `done`, refresh the message from session detail to get the updated answer.

### Special case — nothing to retry

If no answers were failed, returns JSON (not a stream):

```json
{
  "status": "no_failures",
  "message": "No failed answers found. Nothing to retry."
}
```

---

## 11. Regenerate an Answer — SSE Streaming

> When the user is not satisfied with an answer, they can click "Regenerate" and provide a reason. The AI will produce a more targeted answer using the user's feedback.

**UX Flow:**
1. User clicks **Regenerate** on a message
2. A text input appears: *"Why do you want to regenerate? (e.g. too vague, missing clause details)"*
3. User types their reason and submits
4. Frontend calls this API — streams the improved answer

**`POST /messages/a264a501-8d18-4714-815c-fb29b8442041/regenerate`**

### Request

```json
{
  "reason": "The answer was too vague. I need the exact notice period in days and which clause mentions it."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `reason` | string | ✅ | User's reason for regenerating (min 3 chars, max 1000 chars) |

### Response — SSE Stream

```
data: "Based"

data: " on"

data: " **Clause"

data: " 9**"

data: " of"

data: " the"

data: " Commercial"

data: " Rental"

data: " Agreement:"

data: "\n\n"

data: "> \"Either"

data: " party"

data: " may"

data: " terminate"

data: " the"

data: " agreement"

data: " by"

data: " giving"

data: " **90 days"

data: " written"

data: " notice**.\""

data: "\n\n"

data: "So,"

data: " the"

data: " exact"

data: " notice"

data: " period"

data: " is"

data: " **90 days**."

event: done
data: {"success":true}
```

- The regenerated answer **replaces** the original answer in the database.
- After `done`, refresh the session detail to see the updated message.

---

## 12. SSE Streaming 
### Summary of SSE Events

| Event | Format | When |
|-------|--------|------|
| Text chunk | `data: "word or phrase"` | During streaming — append to chat bubble |
| Stream end | `event: done` + `data: {"success":true}` | After last chunk — stop typing indicator |

---