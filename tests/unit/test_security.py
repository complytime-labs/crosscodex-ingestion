"""
test_security.py — Unit tests for SSRF prevention, format validation, and hash checking.

Mocks DNS resolution to avoid real network requests.
"""

import hashlib
import http.server
import io
import socket
import struct
import threading
import time
import zipfile
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock, patch

import openpyxl
import pytest

from crosscodex_ingestion import security


class TestValidateUrl:
    """URL validation tests: scheme checking and IP blocking."""

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValueError, match=r"scheme.*not allowed"):
            security.validate_url("ftp://example.com/file.pdf")

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match=r"scheme.*not allowed"):
            security.validate_url("file:///etc/passwd")

    def test_rejects_gopher_scheme(self):
        with pytest.raises(ValueError, match=r"scheme.*not allowed"):
            security.validate_url("gopher://example.com/")

    def test_rejects_url_with_no_hostname(self):
        with pytest.raises(ValueError, match="no hostname"):
            security.validate_url("https://")

    @patch("socket.getaddrinfo")
    def test_rejects_loopback_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with pytest.raises(ValueError, match="not a public internet address"):
            security.validate_url("https://localhost/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_link_local_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.1.1", 443))]
        with pytest.raises(ValueError, match="not a public internet address"):
            security.validate_url("https://link-local.example/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_private_10_network(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]
        with pytest.raises(ValueError, match="not a public internet address"):
            security.validate_url("https://internal.example/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_private_192_network(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 443))]
        with pytest.raises(ValueError, match="not a public internet address"):
            security.validate_url("https://router.local/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_private_172_network(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.16.0.1", 443))]
        with pytest.raises(ValueError, match="not a public internet address"):
            security.validate_url("https://corp.local/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_carrier_grade_nat(self, mock_getaddrinfo):
        # RFC 6598: 100.64.0.0/10
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.1.1", 443))]
        with pytest.raises(ValueError, match="not a public internet address"):
            security.validate_url("https://cgnat.example/file.pdf")

    @patch("socket.getaddrinfo")
    def test_accepts_valid_public_https_url(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        # Should not raise
        security.validate_url("https://example.com/file.pdf")


class TestSniffFormat:
    """Format detection via byte sniffing, MIME type, and URL extension."""

    def test_detects_pdf_via_magic_bytes(self):
        content = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3"
        assert security.sniff_format(content, "", "") == "pdf"

    def test_detects_html_via_html_tag(self):
        content = b"<html><head><title>Test</title></head></html>"
        assert security.sniff_format(content, "", "") == "html"

    def test_detects_html_via_doctype(self):
        content = b"<!DOCTYPE html>\n<html>...</html>"
        assert security.sniff_format(content, "", "") == "html"

    def test_detects_docx_via_zip_magic_and_url(self):
        content = b"PK\x03\x04" + b"\x00" * 20
        url = "https://example.com/document.docx"
        assert security.sniff_format(content, "", url) == "docx"

    def test_falls_back_to_mime_type(self):
        content = b"some random bytes"
        mime_type = "application/pdf"
        assert security.sniff_format(content, mime_type, "") == "pdf"

    def test_falls_back_to_url_extension(self):
        content = b"some random bytes"
        url = "https://example.com/file.html"
        assert security.sniff_format(content, "", url) == "html"

    def test_raises_on_unknown_format(self):
        content = b"random unknown content"
        with pytest.raises(ValueError, match="Cannot determine file type"):
            security.sniff_format(content, "", "")

    def test_unknown_format_message_says_how_to_send_json_or_csv(self):
        with pytest.raises(ValueError, match="Cannot determine file type") as exc_info:
            security.sniff_format(b'{"a": 1}', "", "")

        assert "JSON, CSV and XLSX are read through a mapping" in str(exc_info.value)
        assert "ConvertDocumentRequest.mapping" in str(exc_info.value)


FIXTURES = Path(__file__).parent.parent / "fixtures"


def _xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    workbook.active.append(["ID", "name"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class TestSniffStructuredFormats:
    """Without a mapping, structured formats are recognised only by strong signals: ZIP contents, MIME, extension."""

    def test_docx_is_recognised_by_its_zip_contents_without_a_url(self):
        assert security.sniff_format((FIXTURES / "documents" / "sample.docx").read_bytes()) == "docx"

    def test_xlsx_is_recognised_by_its_zip_contents(self):
        assert security.sniff_format(_xlsx_bytes()) == "xlsx"

    def test_zip_contents_win_over_the_url_extension(self):
        docx = (FIXTURES / "documents" / "sample.docx").read_bytes()

        assert security.sniff_format(docx, "", "https://example.org/renamed.xlsx") == "docx"

    def test_a_zip_prefix_is_classified_by_its_url(self):
        """fetch_url sniffs the first 512 bytes, where the ZIP directory is not."""
        assert security.sniff_format(_xlsx_bytes()[:512], "", "https://example.org/a.xlsx") == "xlsx"

    def test_zip64_locator_is_not_trusted_and_the_zip_is_not_opened(self):
        """zipfile takes the entry count from a ZIP64 record when a locator precedes the end record."""
        content = _xlsx_bytes()
        end = content.rfind(b"PK\x05\x06")
        forged = content[:end] + b"PK\x06\x07" + b"\0" * 16 + content[end:]

        with pytest.raises(ValueError, match="Cannot determine file type"):
            security.sniff_format(forged)

    def test_zip_with_too_many_entries_is_not_opened(self, monkeypatch):
        monkeypatch.setattr(security, "_MAX_SNIFF_ZIP_ENTRIES", 2)

        with pytest.raises(ValueError, match="Cannot determine file type"):
            security.sniff_format(_xlsx_bytes())

    @pytest.mark.parametrize(
        ("mime_type", "url", "expected"),
        [
            ("application/json", "", "json"),
            ("", "https://example.org/asvs.json", "json"),
            ("text/csv", "", "csv"),
            ("", "https://example.org/controls.csv", "csv"),
            ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "", "xlsx"),
        ],
    )
    def test_mime_type_or_extension(self, mime_type, url, expected):
        assert security.sniff_format(b"ID,name\n", mime_type, url) == expected

    def test_text_without_csv_hints_is_still_text(self):
        assert security.sniff_format(b"a,b,c\n1,2,3\n", "text/plain", "") == "txt"

    def test_json_bytes_labelled_text_are_text(self):
        """No guessing: a JSON-looking body is only JSON when the caller or server says so."""
        assert security.sniff_format(b'{"a": 1}', "text/plain", "") == "txt"


class TestConfirmStructuredFormat:
    """With a mapping, its format is expected and the bytes must confirm it."""

    @pytest.mark.parametrize("content", [b'{"a": 1}', b"  \n[1]", b"\xef\xbb\xbf {}"])
    def test_json(self, content):
        assert security.confirm_structured_format(content, "m", "json") == "json"

    def test_xlsx(self):
        assert security.confirm_structured_format(_xlsx_bytes(), "m", "xlsx") == "xlsx"

    @pytest.mark.parametrize("content", [b"ID,name\nA,B\n", b"\xef\xbb\xbfID;name\n"])
    def test_csv(self, content):
        assert security.confirm_structured_format(content, "m", "csv") == "csv"

    @pytest.mark.parametrize(
        ("expected", "content", "actual"),
        [
            ("xlsx", "docx", "docx"),
            ("xlsx", b'{"Requirements": []}', "json"),
            ("json", b"ID,name\n", "text"),
            ("json", b"%PDF-1.7\n", "pdf"),
            ("csv", b"ID\0name", "binary data"),
            ("csv", b"\xff\xfe", "binary data"),
            (
                "json",
                b"PK\x03\x04 not really a zip",
                "a ZIP archive that is truncated or corrupt: no end of central directory record (truncated, or not a "
                "ZIP archive)",
            ),
            ("csv", b'{"vulnerabilities": [{"cveID": "CVE-1"}]}', "json"),
            ("csv", b'\n  [\n {"a": 1}]', "json"),
        ],
    )
    def test_mismatch_names_the_mapping_and_both_formats(self, expected, content, actual):
        if content == "docx":
            content = (FIXTURES / "documents" / "sample.docx").read_bytes()

        with pytest.raises(ValueError, match=r"^Mapping 'my-map' reads") as exc_info:
            security.confirm_structured_format(content, "my-map", expected)

        assert str(exc_info.value) == f"Mapping 'my-map' reads {expected}, but the input is {actual}."


def _forged_zip(records: int) -> bytes:
    """*records* minimal central-directory records behind an end record that declares one entry."""
    directory = (b"PK\x01\x02" + b"\0" * 42) * records
    end = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 1, 1, len(directory), 4, 0)
    return b"PK\x03\x04" + directory + end


class TestHostileZipsInTheServiceProcess:
    """The service process never hands untrusted bytes to zipfile."""

    @pytest.mark.parametrize(
        "call",
        [
            security.sniff_format,
            lambda data: security.confirm_structured_format(data, "m", "xlsx"),
            lambda data: security.validate_format(data, "xlsx", {"xlsx"}),
        ],
    )
    def test_forged_directory_size_is_cheap(self, call):
        data = _forged_zip(500_000)

        started = time.monotonic()
        with pytest.raises(ValueError, match=r"Cannot determine|more than 10000 entries|mismatch"):
            call(data)

        assert time.monotonic() - started < 0.5

    def test_unsupported_zip_version_is_still_classified(self):
        data = bytearray(_xlsx_bytes())
        data[data.find(b"PK\x01\x02") + 6] = 0xFF

        assert security.sniff_format(bytes(data)) == "xlsx"

    def test_unsupported_zip_version_with_a_mapping_is_an_input_error(self):
        data = bytearray(b"PK\x03\x04" + b"\0" * 26)
        data[4] = 0xFF

        with pytest.raises(ValueError, match="reads xlsx, but the input is a ZIP archive that is truncated"):
            security.confirm_structured_format(bytes(data), "m", "xlsx")

    def test_truncated_workbook_says_so(self):
        data = _xlsx_bytes()

        with pytest.raises(ValueError, match=r"^Mapping 'm' reads xlsx") as exc_info:
            security.confirm_structured_format(data[: len(data) // 2], "m", "xlsx")

        assert str(exc_info.value) == (
            "Mapping 'm' reads xlsx, but the input is a ZIP archive that is truncated or corrupt: no end of central "
            "directory record (truncated, or not a ZIP archive)."
        )

    def test_zip_without_a_workbook_says_what_is_missing(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", b"x")

        with pytest.raises(ValueError, match=r"the input is a ZIP archive without xl/workbook\.xml"):
            security.confirm_structured_format(buffer.getvalue(), "m", "xlsx")

    def test_too_many_entries_is_not_called_corrupt(self):
        with pytest.raises(ValueError, match=r"^Mapping") as exc_info:
            security.confirm_structured_format(_forged_zip(20_000), "m", "xlsx")

        assert (
            str(exc_info.value)
            == "Mapping 'm' reads xlsx, but the input is a ZIP archive with more than 10000 entries."
        )

    def test_csv_whose_header_starts_with_a_bracket_is_still_csv(self):
        assert security.confirm_structured_format(b"[ID],name\nA,B\n", "m", "csv") == "csv"


class TestFetchWithExpectedFormat:
    """With a mapping, the full download is confirmed later, so fetch_url need not recognise the first bytes."""

    @patch("socket.getaddrinfo")
    @patch("requests.Session.get")
    def test_expected_format_names_the_temp_file_without_sniffing(self, mock_get, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        response = Mock(status_code=200, headers={"Content-Type": "application/octet-stream"})
        chunks = iter([b'{"Requirements": []}'])
        response.iter_content.side_effect = lambda **_: chunks
        mock_get.return_value = response

        with security.fetch_url("https://example.org/download/1234", expected_format="json") as (path, detected):
            assert detected == "json"
            assert path.endswith(".json")
            assert Path(path).read_bytes() == b'{"Requirements": []}'

    @patch("socket.getaddrinfo")
    @patch("requests.Session.get")
    def test_without_expected_format_unknown_bytes_still_fail(self, mock_get, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        response = Mock(status_code=200, headers={"Content-Type": "application/octet-stream"})
        chunks = iter([b'{"Requirements": []}'])
        response.iter_content.side_effect = lambda **_: chunks
        mock_get.return_value = response

        with pytest.raises(ValueError, match="Cannot determine file type"), security.fetch_url("https://example.org/x"):
            pass


class TestValidateFormat:
    """Format validation: allowlist checking and mismatch detection."""

    def test_raises_on_format_mismatch(self):
        # PDF magic bytes but declared as HTML
        content = b"%PDF-1.4\nfoo"
        with pytest.raises(ValueError, match="mismatch"):
            security.validate_format(content, "html", {"pdf", "html", "docx", "txt"})

    def test_raises_on_disallowed_format(self):
        # HTML content but only PDF allowed
        content = b"<html><body>test</body></html>"
        with pytest.raises(ValueError, match="not in allowed formats"):
            security.validate_format(content, "html", {"pdf"})

    def test_returns_detected_format_on_match(self):
        content = b"%PDF-1.4\nfoo"
        result = security.validate_format(content, "pdf", {"pdf", "html", "docx", "txt"})
        assert result == "pdf"


class TestFetchUrlRedirects:
    """Redirect handling tests: ensure each hop is re-validated."""

    @patch("socket.getaddrinfo")
    @patch("requests.Session.get")
    def test_rejects_redirect_to_loopback(self, mock_get, mock_getaddrinfo):
        """Verify redirect to loopback is caught during re-validation."""

        # First resolution: evil.com -> public IP
        # Second resolution: evil.com redirects to localhost -> loopback IP
        def getaddrinfo_side_effect(hostname, port):
            if hostname == "evil.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
            if hostname == "localhost":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
            raise socket.gaierror(f"Unknown host: {hostname}")

        mock_getaddrinfo.side_effect = getaddrinfo_side_effect

        # Mock redirect response: evil.com -> http://localhost/
        class MockRedirectResponse:
            status_code = 302
            headers: ClassVar[dict[str, str]] = {"Location": "http://localhost/"}

            def close(self):
                pass

        mock_get.return_value = MockRedirectResponse()

        # Should raise when re-validating the redirect target
        with pytest.raises(ValueError, match="not a public internet address"), security.fetch_url("http://evil.com/"):
            pass


class TestValidateContentHash:
    """SHA-256 hash validation."""

    def test_passes_when_hash_matches(self):
        content = b"hello world"
        expected_hash = hashlib.sha256(content).hexdigest()
        # Should not raise
        security.validate_content_hash(content, expected_hash)

    def test_raises_when_hash_mismatches(self):
        content = b"hello world"
        wrong_hash = hashlib.sha256(b"goodbye world").hexdigest()
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            security.validate_content_hash(content, wrong_hash)

    def test_does_nothing_when_declared_hash_is_empty(self):
        content = b"hello world"
        # Should not raise
        security.validate_content_hash(content, "")


class _CountingHandler(http.server.BaseHTTPRequestHandler):
    """Serves a tiny PDF and records every request so tests can prove none arrived."""

    requests_seen: list[str] = []  # noqa: RUF012

    def do_GET(self):
        type(self).requests_seen.append(self.path)
        body = b"%PDF-1.4 rebinding test"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    """An HTTP server on 127.0.0.1 (a blocked address) with a fresh request log."""
    handler = type("Handler", (_CountingHandler,), {"requests_seen": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, handler
    server.shutdown()
    server.server_close()


def _rebinding_resolver(first_ip: str, later_ip: str):
    """getaddrinfo stand-in: the first lookup (validation) answers first_ip, later lookups (connect) later_ip."""
    calls = {"n": 0}

    def resolver(host, port, *args, **kwargs):
        ip = first_ip if calls["n"] == 0 else later_ip
        calls["n"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]

    return resolver


class TestDnsRebinding:
    """validate_url and the actual connection must not be able to disagree about the address."""

    def test_connection_to_private_ip_after_public_validation_is_blocked(self, local_server):
        server, handler = local_server
        url = f"http://rebind.example:{server.server_address[1]}/doc.pdf"

        with (
            patch("socket.getaddrinfo", side_effect=_rebinding_resolver("93.184.216.34", "127.0.0.1")),
            pytest.raises(ValueError, match="not a public internet address"),
            security.fetch_url(url),
        ):
            pytest.fail("fetch_url must not yield for a rebound private address")

        assert handler.requests_seen == []  # no HTTP request reached the private server

    def test_connection_to_validated_address_still_works(self, local_server):
        """The pinning adapter must not break normal fetches (here the block list is disabled for loopback)."""
        server, handler = local_server
        url = f"http://ok.example:{server.server_address[1]}/doc.pdf"

        with (
            patch("socket.getaddrinfo", side_effect=_rebinding_resolver("127.0.0.1", "127.0.0.1")),
            patch.object(security, "_is_blocked_ip", return_value=False),
            security.fetch_url(url) as (path, fmt),
        ):
            assert fmt == "pdf"
            assert Path(path).read_bytes().startswith(b"%PDF-")

        assert handler.requests_seen == ["/doc.pdf"]

    def test_proxy_environment_does_not_bypass_the_peer_check(self, local_server, monkeypatch):
        """A proxy would connect to the target itself, so the peer check would never see it."""
        server, handler = local_server
        url = f"http://rebind.example:{server.server_address[1]}/doc.pdf"
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, "http://proxy.invalid:3128")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        with (
            patch("socket.getaddrinfo", side_effect=_rebinding_resolver("93.184.216.34", "127.0.0.1")),
            pytest.raises(ValueError, match="not a public internet address"),
            security.fetch_url(url),
        ):
            pytest.fail("fetch_url must not yield for a rebound private address")

        assert handler.requests_seen == []


class TestFetchErrorsDoNotMapTheInternalNetwork:
    """Error text reaches the client via GetDocument, so it must not reveal internal DNS or addressing (CWE-209)."""

    @patch("socket.getaddrinfo")
    def test_blocked_and_unresolvable_hosts_get_the_same_message_without_internal_detail(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]
        with pytest.raises(ValueError, match="not a public") as blocked:
            security.validate_url("https://db.internal/file.pdf")

        mock_getaddrinfo.side_effect = socket.gaierror(-2, "Name or service not known")
        with pytest.raises(ValueError, match="not a public") as unresolvable:
            security.validate_url("https://nonexistent.internal/file.pdf")

        assert str(blocked.value) == str(unresolvable.value)
        assert "10.1.2.3" not in str(blocked.value)
        assert "Name or service not known" not in str(unresolvable.value)
        assert "not a public internet address" in str(blocked.value)

    def test_peer_check_message_does_not_name_the_peer_address(self):
        sock = Mock(spec=socket.socket)
        sock.getpeername.return_value = ("10.9.8.7", 80)
        with pytest.raises(ValueError, match="not a public") as exc:
            security._check_peer_is_public(sock)
        sock.close.assert_called_once()
        assert "10.9.8.7" not in str(exc.value)
        assert "not a public internet address" in str(exc.value)
