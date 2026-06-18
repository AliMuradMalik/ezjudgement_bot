"""Uvicorn entry point.

For local dev:
    python run.py

For production, prefer the CLI with multiple workers:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers
"""

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.environment == "development",
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
