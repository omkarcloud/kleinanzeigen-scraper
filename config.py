"""Configuration for the Kleinanzeigen Scraper. Everything can be set with an
environment variable.

    PORT                 port the API listens on (default 8000)
    KLEINANZEIGEN_PROXY  proxy URL for every request, e.g.
                         http://user:pass@host:port (default: none — direct).
                         Kleinanzeigen only answers German IP addresses: from a
                         home or office connection in Germany the default works
                         as is. Anywhere else (and on most cloud servers) every
                         call answers HTTP 403 "IP-Bereich vorübergehend
                         gesperrt" — point this at a German residential proxy.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

KLEINANZEIGEN_PROXY = os.environ.get("KLEINANZEIGEN_PROXY") or None

# Names the kleinanzeigen/ package reads.
KLEINANZEIGEN_PROXY_COUNTRY = "de" if KLEINANZEIGEN_PROXY else None   # truthy = requests go through the proxy
KLEINANZEIGEN_REQUESTS_PER_EXIT = 300    # the session is rebuilt after this many requests
KLEINANZEIGEN_ROTATE_ATTEMPTS = 2        # extra tries on a 403 / 429 when a proxy is set


def kleinanzeigen_proxy():
    return os.environ.get("KLEINANZEIGEN_PROXY") or None
