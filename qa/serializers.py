from rest_framework import serializers

# ── Change MAX_DOCS_PER_SESSION to 10 to enable multi-doc sessions ────────────
MAX_DOCS_PER_SESSION = 1
MAX_QUESTIONS        = 20


class QADocumentInputSerializer(serializers.Serializer):
    document_id       = serializers.CharField()
    s3_download_url   = serializers.URLField()
    document_filename = serializers.CharField(required=False, default="document")


class QASessionCreateSerializer(serializers.Serializer):
    user_id   = serializers.CharField()
    documents = QADocumentInputSerializer(many=True)

    def validate_documents(self, value):
        if not value:
            raise serializers.ValidationError("At least one document is required.")
        if len(value) > MAX_DOCS_PER_SESSION:
            raise serializers.ValidationError(
                f"Maximum {MAX_DOCS_PER_SESSION} document(s) per session currently."
            )
        return value


class QARenameSessionSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=300)


class QAAskSerializer(serializers.Serializer):
    questions = serializers.ListField(
        child=serializers.CharField(min_length=1),
        min_length=1,
        max_length=MAX_QUESTIONS,
        error_messages={
            "min_length": "At least one question is required.",
            "max_length": f"Maximum {MAX_QUESTIONS} questions per request.",
        },
    )
