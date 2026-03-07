import uuid
from typing import TYPE_CHECKING
from django.db import models

if TYPE_CHECKING:
    from django.db.models.manager import RelatedManager


class JobStatus(models.TextChoices):
    PENDING                = "PENDING",                "Pending"
    EXTRACTING             = "EXTRACTING",             "Extracting Document"
    DETECTING_JURISDICTION = "DETECTING_JURISDICTION", "Detecting Jurisdiction"
    ANALYZING              = "ANALYZING",              "Analyzing Clauses"
    GENERATING_REPORTS     = "GENERATING_REPORTS",     "Generating Reports"
    COMPLETED              = "COMPLETED",              "Completed"
    PARTIAL_FAILURE        = "PARTIAL_FAILURE",        "Partial Failure (Resumable)"
    FAILED                 = "FAILED",                 "Failed"


class AnalysisJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # State machine
    status        = models.CharField(max_length=30, choices=JobStatus.choices,
                                     default=JobStatus.PENDING, db_index=True)
    error_message = models.TextField(blank=True, default="")

    # Document source
    document_url      = models.TextField(blank=True, default="")
    document_filename = models.CharField(max_length=500, default="document")
    file_type         = models.CharField(max_length=20, blank=True, default="")
    file_size_bytes   = models.BigIntegerField(null=True, blank=True)

    # Extracted text stored for RESUME — avoids re-downloading the document
    full_text = models.TextField(blank=True, default="")

    # Optional user-supplied context about the document (passed to AI during clause analysis)
    context = models.TextField(blank=True, default="")

    # Agreement metadata
    agreement_type    = models.CharField(max_length=500, blank=True, default="")
    agreement_details = models.JSONField(default=dict)
    parties           = models.JSONField(default=dict)
    property_details  = models.JSONField(default=dict)
    report_format     = models.CharField(max_length=20, default="markdown")

    # Progress counters (denormalized for fast polling)
    total_clauses     = models.IntegerField(default=0)
    completed_clauses = models.IntegerField(default=0)
    failed_clauses    = models.IntegerField(default=0)

    created_at   = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at   = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    if TYPE_CHECKING:
        clauses:           "RelatedManager[JobClause]"
        jurisdiction_info: "JobJurisdiction"
        conflict_records:  "RelatedManager[ClauseConflict]"
        reports:           "RelatedManager[JobReport]"

    class Meta:
        db_table = "analysis_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"Job {self.id} [{self.status}]"


class ClauseStatus(models.TextChoices):
    PENDING     = "PENDING",     "Pending"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    COMPLETED   = "COMPLETED",   "Completed"
    FAILED      = "FAILED",      "Failed"


class JobClause(models.Model):
    id  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="clauses")

    # Clause definition (from request)
    clause_ref_id = models.CharField(max_length=200)   # The "id" from request body
    title         = models.CharField(max_length=1000)
    value         = models.TextField()
    category      = models.CharField(max_length=200, blank=True, default="")
    position      = models.IntegerField()              # Original order in request

    # State machine
    status        = models.CharField(max_length=20, choices=ClauseStatus.choices,
                                     default=ClauseStatus.PENDING, db_index=True)
    retry_count   = models.IntegerField(default=0)     # Incremented on each failure
    error_message = models.TextField(blank=True, default="")
    failed_at     = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        result: "ClauseResult"

    class Meta:
        db_table = "job_clauses"
        ordering = ["position"]
        unique_together = [("job", "clause_ref_id")]
        indexes = [
            models.Index(fields=["job", "status"]),         # Resume: find FAILED clauses
            models.Index(fields=["job", "position"]),       # Ordered result reconstruction
            models.Index(fields=["job", "clause_ref_id"]),  # Lookup by external ID
        ]

    def __str__(self):
        return f"Clause {self.clause_ref_id} [{self.status}]"


class ComplianceResult(models.TextChoices):
    MATCH               = "MATCH",               "Match"
    NOT_FOUND           = "NOT_FOUND",           "Not Found"
    VIOLATION           = "VIOLATION",           "Violation"
    PARTIALLY_SATISFIED = "PARTIALLY_SATISFIED", "Partially Satisfied"


class BindingStrength(models.TextChoices):
    MUST_SHALL = "MUST/SHALL", "Must/Shall"
    SHOULD     = "SHOULD",     "Should"
    MAY_CAN    = "MAY/CAN",    "May/Can"
    VAGUE      = "VAGUE",      "Vague"


class ClauseResult(models.Model):
    id     = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clause = models.OneToOneField(JobClause, on_delete=models.CASCADE, related_name="result")

    # Core AI output
    result        = models.CharField(max_length=30, choices=ComplianceResult.choices, db_index=True)
    color         = models.CharField(max_length=20, blank=True, default="")
    reason        = models.TextField(blank=True, default="")
    relevant_text = models.TextField(blank=True, default="")
    ai_added_text = models.TextField(blank=True, default="")   # ai_recommendation

    # Structured arrays (JSONB on PostgreSQL)
    parties_obligated   = models.JSONField(default=list)
    missing_values      = models.JSONField(default=list)
    key_dates_durations = models.JSONField(default=list)

    binding_strength = models.CharField(max_length=20, choices=BindingStrength.choices,
                                        default=BindingStrength.VAGUE)
    ai_provider_used = models.CharField(max_length=50, blank=True, default="")
    analysis_attempt = models.IntegerField(default=1)   # 1 = first try, 2+ = retries

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "clause_results"
        indexes = [
            models.Index(fields=["result"]),
        ]


class JobJurisdiction(models.Model):
    id  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField(AnalysisJob, on_delete=models.CASCADE,
                               related_name="jurisdiction_info")

    jurisdiction    = models.CharField(max_length=500, default="Unknown")
    agreement_type  = models.CharField(max_length=500, blank=True, default="")
    applicable_laws = models.JSONField(default=list)
    checklist       = models.JSONField(default=list)   # [{"item": str, "required": bool}]

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "job_jurisdiction"


class ClauseConflict(models.Model):
    id  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="conflict_records")

    clause_a_title       = models.CharField(max_length=1000)
    clause_b_title       = models.CharField(max_length=1000)
    conflict_description = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "clause_conflicts"


class ReportType(models.TextChoices):
    PDF_REPORT       = "PDF_REPORT",       "PDF Report"
    PDF_SUMMARY      = "PDF_SUMMARY",      "PDF Summary"
    MARKDOWN_REPORT  = "MARKDOWN_REPORT",  "Markdown Report"
    MARKDOWN_SUMMARY = "MARKDOWN_SUMMARY", "Markdown Summary"
    DOCX_REPORT      = "DOCX_REPORT",      "DOCX Report"
    DOCX_SUMMARY     = "DOCX_SUMMARY",     "DOCX Summary"


class JobReport(models.Model):
    id  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="reports")

    report_type     = models.CharField(max_length=30, choices=ReportType.choices)
    storage_backend = models.CharField(max_length=10, default="r2")   # "r2" | "local"
    file_key        = models.CharField(max_length=1000)
    presigned_url   = models.TextField(blank=True, default="")
    expires_at      = models.DateTimeField(null=True, blank=True)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "job_reports"
        unique_together = [("job", "report_type")]
        indexes = [
            models.Index(fields=["job", "report_type"]),
        ]
