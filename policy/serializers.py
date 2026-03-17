from rest_framework import serializers


class PolicyRuleExtractSerializer(serializers.Serializer):
    """Validates the request for POST /v1/policy/extract-rules."""

    # Document input — base64 or presigned URL
    document_base64        = serializers.CharField(required=False, allow_blank=True, default="")
    document_presigned_url = serializers.CharField(required=False, allow_blank=True, default="")
    document_filename      = serializers.CharField(required=False, allow_blank=True, default="document")

    # Policy classification (used to guide extraction)
    policy_type = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if not attrs.get("document_base64") and not attrs.get("document_presigned_url"):
            raise serializers.ValidationError(
                "Either 'document_base64' or 'document_presigned_url' must be provided."
            )
        return attrs


class PolicyAnalyzerSerializer(serializers.Serializer):
    """Validates the request for POST /v1/policy/analyze."""

    # Document input — base64 or presigned URL
    document_base64        = serializers.CharField(required=False, allow_blank=True, default="")
    document_presigned_url = serializers.CharField(required=False, allow_blank=True, default="")
    document_filename      = serializers.CharField(required=False, allow_blank=True, default="document")

    # Policy input — structured rules (preferred) or raw text (legacy)
    policy_type = serializers.CharField(required=False, allow_blank=True, default="")
    rules       = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
    )
    policy_text = serializers.CharField(required=False, allow_blank=True, default="")

    # Optional metadata
    agreement_type    = serializers.CharField(required=False, allow_blank=True, default="")
    agreement_details = serializers.DictField(required=False, allow_null=True, default=None)
    parties           = serializers.DictField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if not attrs.get("document_base64") and not attrs.get("document_presigned_url"):
            raise serializers.ValidationError(
                "Either 'document_base64' or 'document_presigned_url' must be provided."
            )
        has_rules = bool(attrs.get("rules"))
        has_policy_text = bool((attrs.get("policy_text") or "").strip())
        if not has_rules and not has_policy_text:
            raise serializers.ValidationError(
                "Either 'rules' (structured rule objects) or 'policy_text' must be provided."
            )
        return attrs
