from rest_framework import serializers


class ClauseSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    content = serializers.CharField(required=False, default="")
    value = serializers.CharField(required=False, default="")
    category = serializers.CharField(required=False, default="")


MAX_CLAUSES = 100
MAX_PDF_SIZE_MB = 100
MAX_PDF_SIZE_BYTES = MAX_PDF_SIZE_MB * 1024 * 1024  # 104,857,600 bytes


class ClauseAnalyzerSerializer(serializers.Serializer):
    pdf_presigned_url = serializers.URLField()
    clauses = ClauseSerializer(many=True)
    report_format = serializers.ChoiceField(
        choices=["pdf", "markdown", "docx", "both"],
        default="pdf",
        required=False,
    )

    def validate_clauses(self, value):
        if not value:
            raise serializers.ValidationError("At least one clause is required.")
        if len(value) > MAX_CLAUSES:
            raise serializers.ValidationError(
                f"Too many clauses. Maximum allowed is {MAX_CLAUSES}, you sent {len(value)}."
            )
        return value
