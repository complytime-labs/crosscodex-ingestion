from abc import ABC, abstractmethod
from pathlib import Path
from typing import cast

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from crosscodex_ingestion.config import IngestionSettings


class StorageBackend(ABC):
    @abstractmethod
    def upload(self, tenant_id: str, document_id: str, filename: str, data: bytes) -> str:
        """Upload data, return URI string."""

    @abstractmethod
    def download(self, uri: str) -> bytes:
        """Download data by URI."""

    @abstractmethod
    def exists(self, uri: str) -> bool:
        """Check if URI exists."""


class FilesystemStorage(StorageBackend):
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def upload(self, tenant_id: str, document_id: str, filename: str, data: bytes) -> str:
        """Upload data to tenant-isolated filesystem path."""
        file_path = self.base_path / tenant_id / "documents" / document_id / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        return f"file://{file_path}"

    def download(self, uri: str) -> bytes:
        """Download data from filesystem URI."""
        if not uri.startswith("file://"):
            raise ValueError(f"Invalid filesystem URI: {uri}")
        file_path = Path(uri.removeprefix("file://"))
        return file_path.read_bytes()

    def exists(self, uri: str) -> bool:
        """Check if filesystem URI exists."""
        if not uri.startswith("file://"):
            raise ValueError(f"Invalid filesystem URI: {uri}")
        file_path = Path(uri.removeprefix("file://"))
        return file_path.exists()


class S3Storage(StorageBackend):
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, region: str):
        self.bucket = bucket
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def upload(self, tenant_id: str, document_id: str, filename: str, data: bytes) -> str:
        """Upload data to S3 with tenant-isolated key."""
        key = f"{tenant_id}/documents/{document_id}/{filename}"
        self.s3_client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return f"s3://{self.bucket}/{key}"

    def download(self, uri: str) -> bytes:
        """Download data from S3 URI."""
        if not uri.startswith("s3://"):
            raise ValueError(f"Invalid S3 URI: {uri}")
        parts = uri.removeprefix("s3://").split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid S3 URI format: {uri}")
        bucket, key = parts
        response = self.s3_client.get_object(Bucket=bucket, Key=key)
        return cast(bytes, response["Body"].read())

    def exists(self, uri: str) -> bool:
        """Check if S3 URI exists."""
        if not uri.startswith("s3://"):
            raise ValueError(f"Invalid S3 URI: {uri}")
        parts = uri.removeprefix("s3://").split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid S3 URI format: {uri}")
        bucket, key = parts
        try:
            self.s3_client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise


def create_storage(settings: IngestionSettings) -> StorageBackend:
    """Factory: returns FilesystemStorage or S3Storage based on settings."""
    if settings.storage_backend == "filesystem":
        return FilesystemStorage(settings.storage_path)
    elif settings.storage_backend == "s3":
        return S3Storage(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        )
    else:
        raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")
