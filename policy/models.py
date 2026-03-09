import uuid
from django.db import models


class PolicyAnalysisJob(models.Model):
    STATUS_CHOICES = [
        ("pending",     "Pending"),
        ("in_progress", "In Progress"),
        ("completed",   "Completed"),
        ("failed",      "Failed"),
    ]

    job_id     = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Document info
    document_filename = models.CharField(max_length=500, blank=True, default="")
    full_text         = models.TextField(blank=True, default="")  # extracted text cache

    # Policy analysis inputs
    policy_type = models.CharField(max_length=500, blank=True, default="")
    policy_text = models.TextField(blank=True, default="")

    # Optional metadata (agreement info)
    agreement_type    = models.CharField(max_length=500, blank=True, default="")
    agreement_details = models.JSONField(null=True, blank=True)
    parties           = models.JSONField(null=True, blank=True)

    # AI output
    policy_result = models.JSONField(null=True, blank=True)

    # Error info
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "policy_analysis_jobs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"PolicyJob {self.job_id} [{self.status}]"
