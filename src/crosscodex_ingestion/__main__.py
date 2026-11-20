"""Entry point for CrossCodex Ingestion Service.

Run with: python -m crosscodex_ingestion
"""

import argparse
import asyncio
from collections.abc import Sequence

from crosscodex_ingestion import __version__
from crosscodex_ingestion.server import serve


def main(argv: Sequence[str] | None = None) -> None:
    """Parse the command line, then run the server until it is signalled to stop."""
    parser = argparse.ArgumentParser(
        prog="python -m crosscodex_ingestion",
        description="CrossCodex Ingestion Service: a gRPC service that converts documents to structured JSON.",
        epilog="All configuration is read from environment variables; see docs/usage/configuration.md.",
    )
    parser.add_argument("--version", action="version", version=f"crosscodex-ingestion {__version__}")
    parser.parse_args(argv)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
