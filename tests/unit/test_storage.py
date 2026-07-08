from unittest.mock import MagicMock, patch


def test_filesystem_upload_creates_file(tmp_path):
    from crosscodex_ingestion.storage import FilesystemStorage

    storage = FilesystemStorage(str(tmp_path))
    uri = storage.upload("tenant1", "doc123", "output.json", b'{"test": "data"}')

    expected_path = tmp_path / "tenant1" / "documents" / "doc123" / "output.json"
    assert expected_path.exists()
    assert expected_path.read_bytes() == b'{"test": "data"}'
    assert uri == f"file://{expected_path}"


def test_filesystem_upload_returns_uri(tmp_path):
    from crosscodex_ingestion.storage import FilesystemStorage

    storage = FilesystemStorage(str(tmp_path))
    uri = storage.upload("tenant1", "doc123", "output.json", b"test")

    assert uri.startswith("file://")
    assert "tenant1/documents/doc123/output.json" in uri


def test_filesystem_download_reads_data(tmp_path):
    from crosscodex_ingestion.storage import FilesystemStorage

    storage = FilesystemStorage(str(tmp_path))
    uri = storage.upload("tenant1", "doc123", "output.json", b'{"test": "data"}')

    downloaded = storage.download(uri)
    assert downloaded == b'{"test": "data"}'


def test_filesystem_exists_returns_true_for_uploaded(tmp_path):
    from crosscodex_ingestion.storage import FilesystemStorage

    storage = FilesystemStorage(str(tmp_path))
    uri = storage.upload("tenant1", "doc123", "output.json", b"test")

    assert storage.exists(uri) is True


def test_filesystem_exists_returns_false_for_missing(tmp_path):
    from crosscodex_ingestion.storage import FilesystemStorage

    storage = FilesystemStorage(str(tmp_path))
    missing_uri = f"file://{tmp_path}/tenant1/documents/doc123/missing.json"

    assert storage.exists(missing_uri) is False


def test_filesystem_tenant_isolation(tmp_path):
    from crosscodex_ingestion.storage import FilesystemStorage

    storage = FilesystemStorage(str(tmp_path))

    uri1 = storage.upload("tenant1", "doc123", "output.json", b"tenant1 data")
    uri2 = storage.upload("tenant2", "doc123", "output.json", b"tenant2 data")

    assert storage.download(uri1) == b"tenant1 data"
    assert storage.download(uri2) == b"tenant2 data"
    assert uri1 != uri2
    assert "tenant1" in uri1
    assert "tenant2" in uri2


@patch("crosscodex_ingestion.storage.boto3")
def test_s3_upload_calls_put_object(mock_boto3):
    from crosscodex_ingestion.storage import S3Storage

    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    storage = S3Storage(
        endpoint="https://s3.amazonaws.com",
        bucket="test-bucket",
        access_key="test-key",
        secret_key="test-secret",
        region="us-east-1",
    )

    uri = storage.upload("tenant1", "doc123", "output.json", b'{"test": "data"}')

    mock_client.put_object.assert_called_once_with(
        Bucket="test-bucket", Key="tenant1/documents/doc123/output.json", Body=b'{"test": "data"}'
    )
    assert uri == "s3://test-bucket/tenant1/documents/doc123/output.json"


@patch("crosscodex_ingestion.storage.boto3")
def test_s3_download_calls_get_object(mock_boto3):
    from crosscodex_ingestion.storage import S3Storage

    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client
    mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"test": "data"}')}

    storage = S3Storage(
        endpoint="https://s3.amazonaws.com",
        bucket="test-bucket",
        access_key="test-key",
        secret_key="test-secret",
        region="us-east-1",
    )

    data = storage.download("s3://test-bucket/tenant1/documents/doc123/output.json")

    mock_client.get_object.assert_called_once_with(Bucket="test-bucket", Key="tenant1/documents/doc123/output.json")
    assert data == b'{"test": "data"}'


@patch("crosscodex_ingestion.storage.boto3")
def test_s3_exists_returns_true_when_found(mock_boto3):
    from crosscodex_ingestion.storage import S3Storage

    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client
    mock_client.head_object.return_value = {}

    storage = S3Storage(
        endpoint="https://s3.amazonaws.com",
        bucket="test-bucket",
        access_key="test-key",
        secret_key="test-secret",
        region="us-east-1",
    )

    exists = storage.exists("s3://test-bucket/tenant1/documents/doc123/output.json")

    assert exists is True
    mock_client.head_object.assert_called_once_with(Bucket="test-bucket", Key="tenant1/documents/doc123/output.json")


@patch("crosscodex_ingestion.storage.boto3")
def test_s3_exists_returns_false_when_not_found(mock_boto3):
    from botocore.exceptions import ClientError

    from crosscodex_ingestion.storage import S3Storage

    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client
    mock_client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

    storage = S3Storage(
        endpoint="https://s3.amazonaws.com",
        bucket="test-bucket",
        access_key="test-key",
        secret_key="test-secret",
        region="us-east-1",
    )

    exists = storage.exists("s3://test-bucket/tenant1/documents/doc123/output.json")

    assert exists is False


def test_create_storage_returns_filesystem():
    from crosscodex_ingestion.config import IngestionSettings
    from crosscodex_ingestion.storage import FilesystemStorage, create_storage

    settings = IngestionSettings(storage_backend="filesystem", storage_path="/tmp/test")
    storage = create_storage(settings)

    assert isinstance(storage, FilesystemStorage)


def test_create_storage_returns_s3():
    from crosscodex_ingestion.config import IngestionSettings
    from crosscodex_ingestion.storage import S3Storage, create_storage

    settings = IngestionSettings(
        storage_backend="s3",
        s3_endpoint="https://s3.amazonaws.com",
        s3_bucket="test-bucket",
        s3_access_key="test-key",
        s3_secret_key="test-secret",
        s3_region="us-east-1",
    )

    with patch("crosscodex_ingestion.storage.boto3"):
        storage = create_storage(settings)

    assert isinstance(storage, S3Storage)
