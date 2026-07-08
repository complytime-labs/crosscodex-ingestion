"""Entry point for CrossCodex Ingestion Service.

Run with: python -m crosscodex_ingestion
"""

import asyncio

from crosscodex_ingestion.server import serve

if __name__ == "__main__":
    asyncio.run(serve())
