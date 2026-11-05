"""
test_security.py — Unit tests for SSRF prevention, format validation, and hash checking.

Mocks DNS resolution to avoid real network requests.
"""

import hashlib
import http.server
import socket
from typing import ClassVar
import threading
from unittest.mock import patch

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
        with pytest.raises(ValueError, match="blocked address"):
            security.validate_url("https://localhost/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_link_local_ipv4(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.1.1", 443))]
        with pytest.raises(ValueError, match="blocked address"):
            security.validate_url("https://link-local.example/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_private_10_network(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]
        with pytest.raises(ValueError, match="blocked address"):
            security.validate_url("https://internal.example/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_private_192_network(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 443))]
        with pytest.raises(ValueError, match="blocked address"):
            security.validate_url("https://router.local/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_private_172_network(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.16.0.1", 443))]
        with pytest.raises(ValueError, match="blocked address"):
            security.validate_url("https://corp.local/file.pdf")

    @patch("socket.getaddrinfo")
    def test_rejects_carrier_grade_nat(self, mock_getaddrinfo):
        # RFC 6598: 100.64.0.0/10
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.1.1", 443))]
        with pytest.raises(ValueError, match="blocked address"):
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
        with pytest.raises(ValueError, match="blocked address"), security.fetch_url("http://evil.com/"):
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

    def do_GET(self):  # noqa: N802
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

        with patch("socket.getaddrinfo", side_effect=_rebinding_resolver("93.184.216.34", "127.0.0.1")):
            with pytest.raises(ValueError, match="blocked address"):
                with security.fetch_url(url):
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
            assert open(path, "rb").read().startswith(b"%PDF-")

        assert handler.requests_seen == ["/doc.pdf"]

    def test_proxy_environment_does_not_bypass_the_peer_check(self, local_server, monkeypatch):
        """A proxy would connect to the target itself, so the peer check would never see it."""
        server, handler = local_server
        url = f"http://rebind.example:{server.server_address[1]}/doc.pdf"
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, "http://proxy.invalid:3128")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        with patch("socket.getaddrinfo", side_effect=_rebinding_resolver("93.184.216.34", "127.0.0.1")):
            with pytest.raises(ValueError, match="blocked address"):
                with security.fetch_url(url):
                    pytest.fail("fetch_url must not yield for a rebound private address")

        assert handler.requests_seen == []
