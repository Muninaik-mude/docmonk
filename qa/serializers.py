from rest_framework import serializers

# ── Change MAX_DOCS_PER_SESSION to 10 to enable multi-doc sessions ────────────
MAX_DOCS_PER_SESSION = 1
MAX_QUESTIONS        = 50


class QADocumentInputSerializer(serializers.Serializer):
    document_id       = serializers.CharField(max_length=500)
    s3_download_url   = serializers.URLField()
    document_filename = serializers.CharField(required=False, default="document", max_length=500)


class QASessionCreateSerializer(serializers.Serializer):
    user_id   = serializers.CharField(max_length=500)
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


class QARegenerateSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=3, max_length=1000)


class QAAskSerializer(serializers.Serializer):
    question = serializers.CharField(min_length=1, max_length=2000)
