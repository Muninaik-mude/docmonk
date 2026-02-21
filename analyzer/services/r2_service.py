import logging
import uuid

import boto3
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def download_pdf_from_presigned_url(presigned_url: str) -> bytes:
    """Download PDF bytes from a presigned S3/R2 URL."""
    response = requests.get(presigned_url, timeout=30)
    response.raise_for_status()

    content_type = response.headers.get('Content-Type', '')
    if 'pdf' not in content_type and not response.content[:5] == b'%PDF-':
        raise ValueError("Downloaded content does not appear to be a PDF")

    return response.content


def _get_r2_client():
    """Create a boto3 S3 client configured for Cloudflare R2."""
    return boto3.client(
        's3',
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name='auto',
    )


def upload_pdf_to_r2(pdf_bytes: bytes, filename: str = None) -> str:
    """Upload annotated PDF bytes to R2 bucket under 'analyzed/' prefix."""
    if not filename:
        filename = f"result_{uuid.uuid4()}.pdf"
    object_key = f"analyzed/{filename}"

    client = _get_r2_client()
    client.put_object(
        Bucket=settings.R2_BUCKET_NAME,
        Key=object_key,
        Body=pdf_bytes,
        ContentType='application/pdf',
    )
    logger.info("Uploaded annotated PDF to R2: %s", object_key)
    return object_key


def generate_presigned_download_url(object_key: str, expiry_seconds: int = 3600) -> str:
    """Generate a presigned GET URL for the uploaded PDF."""
    client = _get_r2_client()
    url = client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': settings.R2_BUCKET_NAME,
            'Key': object_key,
        },
        ExpiresIn=expiry_seconds,
    )
    return url


def upload_file_to_r2(file_bytes: bytes, filename: str, prefix: str = "reports", content_type: str = "application/octet-stream") -> str:
    """Upload any file bytes to R2 bucket under a given prefix."""
    object_key = f"{prefix}/{filename}"
    client = _get_r2_client()
    client.put_object(
        Bucket=settings.R2_BUCKET_NAME,
        Key=object_key,
        Body=file_bytes,
        ContentType=content_type,
    )
    logger.info("Uploaded file to R2: %s", object_key)
    return object_key


def save_pdf_locally(pdf_bytes: bytes, filename: str = None) -> str:
    """Fallback: save annotated PDF locally when R2 is not configured."""
    if not filename:
        filename = f"result_{uuid.uuid4()}.pdf"
    output_path = settings.ANNOTATED_PDF_DIR / filename
    output_path.write_bytes(pdf_bytes)
    logger.info("Saved annotated PDF locally: %s", output_path)
    return str(output_path)


def save_file_locally(file_bytes: bytes, filename: str) -> str:
    """Fallback: save any file locally when R2 is not configured."""
    output_path = settings.ANNOTATED_PDF_DIR / filename
    output_path.write_bytes(file_bytes)
    logger.info("Saved file locally: %s", output_path)
    return str(output_path)


def is_r2_configured() -> bool:
    """Check if R2 credentials are properly configured."""
    return all([
        settings.R2_ENDPOINT_URL,
        settings.R2_ACCESS_KEY_ID,
        settings.R2_SECRET_ACCESS_KEY,
        settings.R2_BUCKET_NAME,
    ])
