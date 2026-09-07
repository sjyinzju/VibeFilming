"""Supported launch command: python -m services.flux_image (one worker, loopback)."""

import logging
import os

import uvicorn

from .app import create_app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app(
        placement=os.environ.get("FLUX_PLACEMENT", "cuda"),
        timeout_seconds=float(os.environ.get("FLUX_GENERATION_TIMEOUT_SECONDS", "600")),
    )
    uvicorn.run(app, host="127.0.0.1", port=9001, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()
