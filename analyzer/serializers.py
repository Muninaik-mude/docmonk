import base64

from rest_framework import serializers


class ClauseSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    content = serializers.CharField(required=False, default="")
    value = serializers.CharField(required=False, default="")
    category = serializers.CharField(required=False, default="")


MAX_CLAUSES = 100
MAX_FILE_SIZE_MB = 100
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024  # 104,857,600 bytes

SUPPORTED_DOCUMENT_TYPES = ("pdf", "docx", "md", "txt")


class ClauseAnalyzerSerializer(serializers.Serializer):
    document_base64 = serializers.CharField()
    document_type = serializers.ChoiceField(
        choices=list(SUPPORTED_DOCUMENT_TYPES),
        default="md",
        required=False,
    )
    clauses = ClauseSerializer(many=True)
    report_format = serializers.ChoiceField(
        choices=["pdf", "markdown", "docx", "both"],
        default="markdown",
        required=False,
    )

    def validate_document_base64(self, value):
        try:
            decoded = base64.b64decode(value, validate=True)
        except Exception:
            raise serializers.ValidationError("document_base64 is not valid base64.")
        if len(decoded) > MAX_FILE_SIZE_BYTES:
            mb = len(decoded) / (1024 * 1024)
            raise serializers.ValidationError(
                f"Decoded file size {mb:.2f} MB exceeds the maximum allowed {MAX_FILE_SIZE_MB} MB."
            )
        return value

    def validate_clauses(self, value):
        if not value:
            raise serializers.ValidationError("At least one clause is required.")
        if len(value) > MAX_CLAUSES:
            raise serializers.ValidationError(
                f"Too many clauses. Maximum allowed is {MAX_CLAUSES}, you sent {len(value)}."
            )
        return value
