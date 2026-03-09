from rest_framework import serializers


class PolicyAnalyzerSerializer(serializers.Serializer):
    # Document input — base64 or presigned URL
    document_base64        = serializers.CharField(required=False, allow_blank=True, default="")
    document_presigned_url = serializers.CharField(required=False, allow_blank=True, default="")
    document_filename      = serializers.CharField(required=False, allow_blank=True, default="document")

    # Policy inputs
    policy_type = serializers.CharField(required=False, allow_blank=True, default="")
    policy_text = serializers.CharField(required=True)

    # Optional metadata
    agreement_type    = serializers.CharField(required=False, allow_blank=True, default="")
    agreement_details = serializers.DictField(required=False, allow_null=True, default=None)
    parties           = serializers.DictField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if not attrs.get("document_base64") and not attrs.get("document_presigned_url"):
            raise serializers.ValidationError(
                "Either 'document_base64' or 'document_presigned_url' must be provided."
            )
        if not (attrs.get("policy_text") or "").strip():
            raise serializers.ValidationError("'policy_text' is required and cannot be blank.")
        return attrs
