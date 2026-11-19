# Security policy

For anyone who has found a vulnerability in the CrossCodex Ingestion Service. Back to the [README](README.md).

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting:
<https://github.com/complytime-labs/crosscodex-ingestion/security/advisories/new>.

Do not open a public issue for a vulnerability.

## Trust boundaries

What the service trusts and why is described in:

- [gRPC authentication](docs/usage/configuration.md#grpc-authentication): mutual TLS, and the `tenant_id` the
  service trusts from the caller.
- [Structured sources security model](docs/structured-sources.md#security-model): untrusted structured input
  and the sandboxed worker.
