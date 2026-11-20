"""Transport security for the gRPC port: mutual TLS credentials and client allowlist.

The service is internal; its callers are CrossCodex services acting for many tenants, so a
client certificate identifies the calling *service*, never the tenant. Tenant isolation is
the caller's responsibility.
"""

import logging
from collections.abc import Awaitable, Callable, Collection
from pathlib import Path

import grpc

from crosscodex_ingestion.config import IngestionSettings

logger = logging.getLogger(__name__)

# auth_context() keys populated by gRPC from the verified client certificate.
_TRANSPORT_SECURITY_KEY = "transport_security_type"
_IDENTITY_KEYS = ("x509_common_name", "x509_subject_alternative_name")


def _read(path: str, setting: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"Cannot read {setting} ({path}): {exc.strerror or exc}") from exc


def client_identity(context: grpc.aio.ServicerContext, allowed: Collection[str] = ()) -> str | None:
    """Return the client certificate identity the service acts on, or None without one.

    Identities are tried CN first, then SANs in certificate order. With ALLOWED (the
    GRPC_ALLOWED_CLIENTS set) the result is the first one on the allowlist, which is the identity
    ClientAllowlistInterceptor authorizes; None means no identity is allowlisted. Without an allowlist
    it is the first identity. None also covers a plaintext port and a client with no certificate.
    """
    identities = _identities(context)
    if allowed:
        return next((identity for identity in identities if identity in allowed), None)
    return identities[0] if identities else None


def _identities(context: grpc.aio.ServicerContext) -> list[str]:
    """Identities named by the client certificate, common name first; empty without TLS."""
    auth = context.auth_context()
    if auth.get(_TRANSPORT_SECURITY_KEY) != [b"ssl"]:
        return []
    return [value.decode("utf-8", "replace") for key in _IDENTITY_KEYS for value in auth.get(key, [])]


def build_server_credentials(settings: IngestionSettings) -> grpc.ServerCredentials | None:
    """Build mutual-TLS server credentials, or None when GRPC_TLS_MODE=off.

    Raises:
        ValueError: If mutual TLS is configured without usable cert, key and CA files, or if
            an allowlist is set while TLS is off (it could not be enforced).
    """
    if settings.grpc_tls_mode == "off":
        if settings.grpc_allowed_clients_set:
            raise ValueError(
                "GRPC_ALLOWED_CLIENTS cannot be enforced with GRPC_TLS_MODE=off; "
                "use GRPC_TLS_MODE=mutual or unset GRPC_ALLOWED_CLIENTS"
            )
        logger.warning(
            "GRPC_TLS_MODE=off: the gRPC port is plaintext and unauthenticated; any caller can "
            "convert documents and read any tenant's data (development and tests only)"
        )
        return None

    missing = [
        name
        for name, value in (
            ("GRPC_TLS_CERT", settings.grpc_tls_cert),
            ("GRPC_TLS_KEY", settings.grpc_tls_key),
            ("GRPC_TLS_CA", settings.grpc_tls_ca),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"GRPC_TLS_MODE=mutual requires {', '.join(missing)}: provision a server certificate, "
            "its key and the CA that signs client certificates, or set GRPC_TLS_MODE=off "
            "(development only)"
        )

    return grpc.ssl_server_credentials(
        [(_read(settings.grpc_tls_key, "GRPC_TLS_KEY"), _read(settings.grpc_tls_cert, "GRPC_TLS_CERT"))],
        root_certificates=_read(settings.grpc_tls_ca, "GRPC_TLS_CA"),
        require_client_auth=True,
    )


class ClientAllowlistInterceptor(grpc.aio.ServerInterceptor):  # type: ignore[misc]
    """Reject RPCs whose verified client certificate identity is not allowlisted.

    The TLS layer has already checked the certificate chain against GRPC_TLS_CA; this checks
    which service the certificate names. Rejections carry no detail beyond the status code; the
    offending identities are logged server-side.
    """

    def __init__(self, allowed_clients: set[str]):
        if not allowed_clients:
            # client_identity treats an empty allowlist as "no allowlist" and admits everyone.
            raise ValueError("ClientAllowlistInterceptor requires a non-empty allowlist")
        self._allowed = frozenset(allowed_clients)

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        if handler is None:
            return handler
        if handler.request_streaming or handler.response_streaming or handler.unary_unary is None:
            # The service defines only unary RPCs; refuse to wrap (and so expose) anything else.
            raise NotImplementedError("ClientAllowlistInterceptor supports unary-unary handlers only")

        inner = handler.unary_unary

        async def checked(request: object, context: grpc.aio.ServicerContext) -> object:
            await self._authorize(context)
            return await inner(request, context)

        return grpc.unary_unary_rpc_method_handler(
            checked,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    async def _authorize(self, context: grpc.aio.ServicerContext) -> None:
        identities = _identities(context)
        if not identities:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "client authentication required")
        if client_identity(context, self._allowed) is None:
            logger.warning(
                "Rejected gRPC client not in GRPC_ALLOWED_CLIENTS", extra={"client_identities": sorted(set(identities))}
            )
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, "permission denied")
