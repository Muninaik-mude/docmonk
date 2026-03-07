from rest_framework import serializers


class ClauseSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    value = serializers.CharField()
    category = serializers.CharField(required=False, default="")


class AgreementDetailsSerializer(serializers.Serializer):
    agreement_date = serializers.CharField(required=False, default="")
    city = serializers.CharField(required=False, default="")
    state = serializers.CharField(required=False, default="")


class LandlordSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, default="")
    address = serializers.CharField(required=False, default="")
    contact = serializers.CharField(required=False, default="")


class TenantSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, default="")
    company_name = serializers.CharField(required=False, default="")
    authorized_signatory = serializers.CharField(required=False, default="")
    address = serializers.CharField(required=False, default="")
    contact = serializers.CharField(required=False, default="")


class PartiesSerializer(serializers.Serializer):
    landlord = LandlordSerializer(required=False)
    tenant = TenantSerializer(required=False)


class PropertySerializer(serializers.Serializer):
    type = serializers.CharField(required=False, default="")
    area_sqft = serializers.IntegerField(required=False, allow_null=True)
    address = serializers.CharField(required=False, default="")


MAX_CLAUSES = 100
MAX_FILE_SIZE_MB = 100
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024  # 104,857,600 bytes

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".md", ".txt")


class ClauseAnalyzerSerializer(serializers.Serializer):
    document_presigned_url = serializers.URLField(required=False)
    pdf_presigned_url = serializers.URLField(required=False)  # backward compat
    document_base64 = serializers.CharField(required=False)
    document_filename = serializers.CharField(required=False, default="document")
    agreement_type = serializers.CharField(required=False, default="")
    agreement_details = AgreementDetailsSerializer(required=False)
    parties = PartiesSerializer(required=False)
    property = PropertySerializer(required=False)
    clauses = ClauseSerializer(many=True)
    context = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)

    def validate(self, attrs):
        doc_url = attrs.get("document_presigned_url") or attrs.get("pdf_presigned_url")
        doc_b64 = attrs.get("document_base64")
        if not doc_url and not doc_b64:
            raise serializers.ValidationError(
                "Either 'document_presigned_url', 'pdf_presigned_url', or 'document_base64' is required."
            )
        if doc_url:
            attrs["document_presigned_url"] = doc_url
        return attrs

    def validate_clauses(self, value):
        if not value:
            raise serializers.ValidationError("At least one clause is required.")
        if len(value) > MAX_CLAUSES:
            raise serializers.ValidationError(
                f"Too many clauses. Maximum allowed is {MAX_CLAUSES}, you sent {len(value)}."
            )
        return value
