"""
security.py — SSRF prevention, format validation, and hash verification.

Ported from OllamaCrosswalker url_fetcher.py and adapted for CrossCodex ingestion service.
Supports pdf, docx, html, txt formats.

Security measures:
  - Only http and https schemes are accepted.
  - The hostname is resolved to all its IP addresses before connecting; any address
    in a loopback, link-local, multicast, private, or other non-routable range is
    rejected (SSRF prevention).
  - Redirects are followed manually so each hop is re-validated before connecting.
  - Download size is capped.
  - A per-request timeout is enforced.
  - Content type is determined by byte-sniffing the response body first, then the
    Content-Type header, then the URL path extension.
  - The temp file is always deleted when the context manager exits.
  - Content hash verification (SHA-256) is provided for integrity checking.
"""

import contextlib
import hashlib
import ipaddress
import logging
import os
import socket
import tempfile
import urllib.parse
from collections.abc import Iterator

import requests

log = logging.getLogger(__name__)

# Private / non-routable ranges not covered by the stdlib ip_address properties.
# (is_loopback, is_link_local, is_multicast, is_private, is_unspecified cover the rest.)
_EXTRA_BLOCKED = [
    ipaddress.ip_network("100.64.0.0/10"),  # RFC 6598 carrier-grade NAT
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique-local
]

_ALLOWED_SCHEMES = {"https", "http"}
_REDIRECT_CODES = {301, 302, 303, 307, 308}

DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100 MB
DEFAULT_TIMEOUT = 30  # seconds per request
DEFAULT_MAX_REDIRECTS = 5


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if the IP is in a blocked/private range."""
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    if ip.is_private:
        return True
    return any(ip in net for net in _EXTRA_BLOCKED)


def validate_url(url: str) -> None:
    """Validate scheme and resolve host IP(s) against blocked ranges.

    Raises ValueError with a descriptive message on any violation.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ValueError(f"Malformed URL {url!r}: {exc}") from exc

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme {parsed.scheme!r} is not allowed; use https (or http).")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"URL has no hostname: {url!r}")

    if parsed.scheme == "http":
        log.warning("Fetching over unencrypted HTTP — use https where possible: %s", url)

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve hostname {hostname!r}: {exc}") from exc

    if not addr_infos:
        raise ValueError(f"Hostname {hostname!r} resolved to no addresses.")

    for _family, _type, _proto, _canon, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise ValueError(
                f"URL resolves to a blocked address ({ip}). "
                "Private, loopback, link-local, and multicast hosts are not allowed."
            )


_TYPE_SUFFIX = {
    "pdf": ".pdf",
    "html": ".html",
    "docx": ".docx",
    "txt": ".txt",
}

# Content-Type → detected type
_CT_MAP = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/plain": "txt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}

# URL extension → detected type
_EXT_MAP = {
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".docx": "docx",
    ".txt": "txt",
}


def sniff_format(content: bytes, mime_type: str = "", url: str = "") -> str:
    """Return a format string (e.g. 'pdf', 'html', 'docx', 'txt').

    Raises ValueError if the type cannot be determined.
    """
    # Byte sniffing is most reliable — check before headers or URL.
    if content.startswith(b"%PDF-"):
        return "pdf"

    # HTML byte sniffing
    stripped = content.lstrip()
    lower_stripped = stripped.lower()
    if lower_stripped.startswith((b"<html", b"<!doctype")):
        return "html"

    # ZIP-based Office formats: magic bytes PK\x03\x04 + URL extension
    if content[:4] == b"PK\x03\x04":
        url_path = urllib.parse.urlparse(url).path.lower()
        if url_path.endswith(".docx"):
            return "docx"
        # Generic ZIP — fall through to Content-Type / extension checks

    # Content-Type header
    ct = mime_type.lower().split(";")[0].strip()
    detected = _CT_MAP.get(ct)
    if detected:
        return detected

    # URL path extension (least reliable)
    url_path = urllib.parse.urlparse(url).path.lower()
    for ext, fmt in _EXT_MAP.items():
        if url_path.endswith(ext):
            return fmt

    raise ValueError(
        f"Cannot determine file type from {url!r}. "
        f"Content-Type was {ct!r} and the first bytes were {content[:16]!r}. "
        "Supported formats: PDF, HTML, DOCX, plain text."
    )


def validate_format(
    content: bytes,
    declared_format: str,
    allowed_formats: set[str],
    mime_type: str = "",
    url: str = "",
) -> str:
    """Validate format against allowlist and detect actual format.

    Returns the detected format string.
    Raises ValueError if format is not allowed or mismatches declared format.
    """
    detected = sniff_format(content, mime_type, url)

    if detected not in allowed_formats:
        raise ValueError(f"Detected format {detected!r} is not in allowed formats: {sorted(allowed_formats)}")

    if detected != declared_format:
        raise ValueError(f"Format mismatch: detected {detected!r} but declared as {declared_format!r}")

    return detected


def validate_content_hash(content: bytes, declared_sha256: str) -> None:
    """Verify SHA-256 hash of content.

    Raises ValueError on mismatch. Does nothing if declared_sha256 is empty.
    """
    if not declared_sha256:
        return

    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != declared_sha256.lower():
        raise ValueError(f"SHA-256 mismatch: expected {declared_sha256!r}, got {actual_hash!r}")


@contextlib.contextmanager
def fetch_url(
    url: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: int = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> Iterator[tuple[str, str]]:
    """Download *url* to a temporary file; yield ``(local_path, detected_type)``.

    *detected_type* is a format string such as ``'pdf'``, ``'html'``, ``'docx'``, ``'txt'``.
    The temp file is deleted when the context manager exits, whether or not an exception occurred.

    Raises ``ValueError`` for:
    - blocked/private hosts (SSRF prevention)
    - disallowed URL schemes
    - HTTP errors
    - responses that exceed *max_bytes*
    - too many redirects
    - unrecognised content type
    - empty response body
    """
    validate_url(url)

    session = requests.Session()
    resp = None
    tmp_path = None

    try:
        current_url = url
        redirects = 0

        while True:
            resp = session.get(
                current_url,
                allow_redirects=False,
                stream=True,
                timeout=timeout,
            )

            if resp.status_code in _REDIRECT_CODES:
                if redirects >= max_redirects:
                    raise ValueError(f"Too many redirects (>{max_redirects}) fetching {url!r}.")
                location = resp.headers.get("Location", "")
                if not location:
                    raise ValueError(f"Redirect response from {current_url!r} has no Location header.")
                next_url = urllib.parse.urljoin(current_url, location)
                validate_url(next_url)  # re-validate every redirect hop
                current_url = next_url
                redirects += 1
                resp.close()
                resp = None
                continue

            break  # non-redirect response

        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code} fetching {url!r}.")

        cl_header = resp.headers.get("Content-Length")
        if cl_header:
            try:
                cl = int(cl_header)
                if cl > max_bytes:
                    raise ValueError(
                        f"Content-Length ({cl // (1024 * 1024)} MB) exceeds the "
                        f"{max_bytes // (1024 * 1024)} MB download limit."
                    )
            except ValueError as exc:
                if "exceeds" in str(exc):
                    raise
                # Ignore unparseable Content-Length headers

        # Read first chunk for content-type sniffing
        first_bytes = b""
        for chunk in resp.iter_content(chunk_size=512):
            first_bytes = chunk
            break

        if not first_bytes:
            raise ValueError(f"Empty response body from {url!r}.")

        detected_type = sniff_format(
            first_bytes,
            resp.headers.get("Content-Type", ""),
            current_url,
        )
        suffix = _TYPE_SUFFIX.get(detected_type, f".{detected_type}")

        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_path = tmp.name
        bytes_written = 0

        try:
            tmp.write(first_bytes)
            bytes_written = len(first_bytes)

            for chunk in resp.iter_content(chunk_size=65536):
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise ValueError(f"Download exceeded the {max_bytes // (1024 * 1024)} MB limit.")
                tmp.write(chunk)

            tmp.close()
        except Exception:
            try:
                tmp.close()
            except Exception:  # noqa: S110
                # Cleanup best-effort during error path
                pass
            raise

        log.info(
            "Downloaded %d KB (%s) from %s",
            bytes_written // 1024,
            detected_type,
            url,
        )
        yield tmp_path, detected_type

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:  # noqa: S110
                # Cleanup best-effort
                pass
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: S110
                pass
        session.close()
