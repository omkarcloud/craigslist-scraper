"""Configuration for the Craigslist Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT              port the API listens on (default 8000)
    CRAIGSLIST_PROXY  proxy URL, e.g. http://user:pass@host:port (default:
                      none — direct). Craigslist's JSON APIs answer direct
                      requests from a home or office connection without any
                      rate limit we could find (48 calls in 8 seconds, all
                      200). Craigslist does refuse many datacenter IP ranges:
                      if you run this on a cloud server and see HTTP 403, set
                      a residential proxy here and every request goes through
                      it.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

CRAIGSLIST_PROXY = os.environ.get("CRAIGSLIST_PROXY") or None
CRAIGSLIST_BLOCK_COOLDOWN = 900   # seconds new sessions keep using the proxy after a block


def craigslist_proxy():
    return os.environ.get("CRAIGSLIST_PROXY") or None


def craigslist_fallback_proxy():
    return os.environ.get("CRAIGSLIST_PROXY") or None
