"""REST API entrypoint.

Deliberately minimal: health only. Admin/dashboard routes are a later step; this
exists so the service can be deployed, probed, and kept separate from the
latency-sensitive voice worker.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Stedden API", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "api"}
