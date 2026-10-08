"""``python -m pfa.api`` / ``pfa-api`` — run the service with uvicorn.

One process, one model: the weights are ~2 GB in memory, so scale with replicas behind a
load balancer, not with uvicorn workers (``PFA_WORKERS`` exists for machines with the RAM
and is 1 by default). Inference concurrency inside a process is the engine's gate
(``PFA_EMBED_CONCURRENCY``), not the number of server threads.
"""

from __future__ import annotations

import os
import sys

import uvicorn


def main() -> int:
    uvicorn.run(
        "pfa.api.app:create_app",
        factory=True,
        host=os.environ.get("PFA_HOST", "127.0.0.1"),
        port=int(os.environ.get("PFA_PORT", "8000")),
        reload=os.environ.get("PFA_RELOAD", "") == "1",
        workers=int(os.environ.get("PFA_WORKERS", "1")),
        log_level=os.environ.get("PFA_LOG_LEVEL", "info"),
        log_config=None,  # the app configures logging (RFC 5424 / JSON, one stream); uvicorn's loggers write through it
        access_log=os.environ.get("PFA_ACCESS_LOG", "0") == "1",  # replaced by the app's request line; 1 to compare
        # Behind a reverse proxy, trust X-Forwarded-* only from the proxy: PFA_FORWARDED_ALLOW_IPS=10.0.0.5
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("PFA_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        timeout_graceful_shutdown=int(os.environ.get("PFA_GRACEFUL_SHUTDOWN", "30")),  # let in-flight encodes finish on SIGTERM
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
