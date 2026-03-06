import uuid
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django.db.models.manager import RelatedManager


class QADocument(models.Model):
    """
    Text extraction cache — one row per unique external document_id.
    Multiple users/sessions can reference the same QADocument without re-extracting.
    """
    id                = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_id       = models.CharField(max_length=500, unique=True, db_index=True)
    s3_download_url   = models.URLField(max_length=2000, null=True, blank=True)
    document_filename = models.CharField(max_length=500, default="document")
    file_type         = models.CharField(max_length=20, default="")
    full_text         = models.TextField()
    char_count        = models.IntegerField(default=0)
    # char_page_map: [{page: int, start: int, end: int}] — PDF/PPTX page boundaries
    # Used to resolve page_hint for answers. Empty for TXT/DOCX.
    char_page_map     = models.JSONField(default=list)
    created_at        = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        session_links: "RelatedManager[QASessionDocument]"

    class Meta:
        db_table = "qa_documents"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.document_filename} ({self.document_id})"


class QASession(models.Model):
    """
    One session per user × request. Isolates Q&A history per user.
    user_id is a plain string — no auth validation, caller is responsible.
    """
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id    = models.CharField(max_length=500, db_index=True)
    name       = models.CharField(max_length=300, default="", blank=True)
    # name is auto-set to first question[:100] on first /ask call.
    # User can update it later via PATCH /v1/qa/sessions/{id}/rename.
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        session_documents: "RelatedManager[QASessionDocument]"
        messages:          "RelatedManager[QAMessage]"

    class Meta:
        db_table = "qa_sessions"
        ordering = ["-created_at"]
        indexes  = [models.Index(fields=["user_id", "created_at"])]

    def __str__(self):
        return f"Session {self.id} (user={self.user_id})"


class QASessionDocument(models.Model):
    """
    Junction between QASession and QADocument.
    Supports multi-doc sessions (currently limited to 1 via serializer).
    position tracks the order documents were added to the session.
    """
    session  = models.ForeignKey(QASession,  on_delete=models.CASCADE, related_name="session_documents")
    document = models.ForeignKey(QADocument, on_delete=models.PROTECT,  related_name="session_links")
    position = models.IntegerField(default=0)

    class Meta:
        db_table        = "qa_session_documents"
        ordering        = ["position"]
        unique_together = [("session", "document")]
        indexes         = [models.Index(fields=["session", "position"])]

    def __str__(self):
        return f"Session {self.session_id} → Doc {self.document.document_id} (pos={self.position})"


class QAMessage(models.Model):
    """
    One row per question asked. Belongs to a session (hence to a user).
    Exposes two message_ids in the API — question_message_id (role: user)
    and id (role: assistant) — so the frontend can render a proper chat thread.
    """
    id                   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Separate UUID for the user-side message (question). The primary key (id)
    # is used as the assistant message_id and in retry/regenerate URLs.
    question_message_id  = models.UUIDField(default=uuid.uuid4, editable=False)
    session              = models.ForeignKey(QASession, on_delete=models.CASCADE, related_name="messages")
    question             = models.TextField()
    # answers_per_document: list of dicts:
    # {document_id, document_filename, answer, relevant_excerpt, page_hint, status}
    answers_per_document = models.JSONField(default=list)
    created_at           = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qa_messages"
        ordering = ["created_at"]

    def __str__(self):
        return f"Q: {self.question[:60]}... (session={self.session_id})"
