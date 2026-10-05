"""Craigslist transport: plain curl_cffi with browser-impersonated TLS. No
browser, no cookies, no token (validated 2026-10-05 from direct Indian
egress, ~150 requests across every surface, zero challenges).

The site's own JSON APIs (catalogued in its manifest.js `apiUrlCatalog`),
all open GETs that answer {"apiVersion", "data", "errors": [{"message"}]}:

  * SAPI  https://sapi.craigslist.org/web/v8
      /postings/search/full    the search: 360 full results in one call, or
                               (batch size 0) every matching digest (max
                               10,000) + the `cacheId` the batch call needs
      /postings/search/batch   titles / images / tags for one 360-result
                               chunk of a digest list (start and length must
                               be multiples of 360)
      /categories/count        result counts per category for a query
      /suggest/<type>          global search suggestions
  * RAPI  https://rapi.craigslist.org/web/v8
      /postings/<uuid>                                  one listing
      /postings/<hostname>/<subarea>/<category>/<id>    the same, by post id
      /locations?lat=&lon=                              reverse geocode
  * REFERENCE  https://reference.craigslist.org/Areas   the 707 sites
  * <hostname>.craigslist.org/suggest                   autocomplete within
                                                        one site + category
  * <hostname>.craigslist.org/<category>/<id>.html      301 -> the uuid link

Blocks: none seen. Craigslist is known to refuse datacenter ranges, so if
the pod's egress ever answers 403/429 a worker moves its session to a sticky
residential exit (config.craigslist_fallback_proxy()) for
config.CRAIGSLIST_BLOCK_COOLDOWN seconds — the kick transport's shape.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  CraigslistUpstreamError  transport failure / 5xx              — retryable
  CraigslistBlocked        403 / 429 / HTML where JSON expected — retryable on a new exit
  CraigslistBadRequest     upstream 400                         — never retried
  CraigslistNotFound       404 / missing listing                — never retried
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

SITE = "https://www.craigslist.org"
SAPI = "https://sapi.craigslist.org/web/v8"
RAPI = "https://rapi.craigslist.org/web/v8"
REFERENCE = "https://reference.craigslist.org"
IMPERSONATE = "chrome"

TIMEOUT = 45
FANOUT_WORKERS = 6

# The locale pair every API call carries. Labels come back in English; the
# data (listings, counts) is the same for any pair.
LOCALE = {"cc": "US", "lang": "en"}

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": SITE + "/",
    "origin": SITE,
}


class CraigslistUpstreamError(UpstreamError):
    """Transport failure or 5xx — retryable."""


class CraigslistBlocked(CraigslistUpstreamError, Blocked):
    """403 / 429 or an HTML body where JSON was expected — retryable."""


class CraigslistBadRequest(BadRequest):
    """Upstream 400 — the params were rejected. Never retried."""


class CraigslistNotFound(NotFound):
    """Listing / page does not exist — never retried."""


# ---- sessions ------------------------------------------------------------------
# One curl session per worker thread. Direct egress by default; after a block
# the thread's sessions go through a sticky residential exit for a cooldown.
_local = threading.local()
_fallback_until = 0.0
_fallback_lock = threading.Lock()


def _proxy_for_new_session():
    forced = config.craigslist_proxy()
    if forced:
        return forced
    if time.time() < _fallback_until:
        return config.craigslist_fallback_proxy()
    return None


def _session():
    sess = getattr(_local, "session", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = _proxy_for_new_session()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        _local.session = sess
    return sess


def _drop_session():
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def _note_block():
    """A 403/429 on direct egress: send new sessions through a residential
    exit for a while (no-op when no fallback country is configured)."""
    global _fallback_until
    if config.craigslist_fallback_proxy() is not None:
        with _fallback_lock:
            _fallback_until = time.time() + config.CRAIGSLIST_BLOCK_COOLDOWN


def dump_debug(name, text):
    dbg = os.environ.get("CRAIGSLIST_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass


# ---- requests --------------------------------------------------------------------

def _error_message(resp):
    """The first upstream error message of an API body, or None."""
    try:
        body = resp.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    for error in body.get("errors") or []:
        if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"].strip():
            return error["message"].strip()
    return None


def _classify(resp, label):
    status = resp.status_code
    if status == 404:
        raise CraigslistNotFound(_error_message(resp) or f"{label} not found")
    if status in (403, 429):
        dump_debug("blocked", resp.text)
        _note_block()
        raise CraigslistBlocked(f"HTTP {status} on {label}")
    if status == 400:
        raise CraigslistBadRequest(_error_message(resp) or f"upstream rejected {label}")
    if status >= 500 or status != 200:
        raise CraigslistUpstreamError(f"HTTP {status} on {label}")


def _json_of(resp, label):
    body = resp.text or ""
    if not body.lstrip().startswith(("{", "[")):
        dump_debug("nonjson", body)
        raise CraigslistBlocked(f"{label} returned HTML, not JSON")
    try:
        return resp.json()
    except Exception:
        dump_debug("badjson", body)
        raise CraigslistUpstreamError(f"could not parse JSON from {label}")


def _retrying(fn):
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except CraigslistUpstreamError as e:      # includes CraigslistBlocked
            last = e
            _drop_session()
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def _query_string(params):
    """None / "" / [] values are dropped; list values repeat the key
    (housing_type=1&housing_type=2), which is how the site sends a
    multi-select filter."""
    pairs = []
    for key, value in (params or {}).items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((key, item) for item in value)
        else:
            pairs.append((key, value))
    return urlencode(pairs)


def get_json(url, params=None, *, label=None):
    """GET one JSON document -> the parsed body."""
    label = label or url.split("craigslist.org", 1)[-1]
    query = _query_string(params)
    full_url = f"{url}?{query}" if query else url

    def once():
        sess = _session()
        try:
            resp = sess.get(full_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        except Exception as e:
            raise CraigslistUpstreamError(f"request failed: {type(e).__name__}: {e}")
        _classify(resp, label)
        return _json_of(resp, label)

    return _retrying(once)


def api(base, path, params=None, *, label=None):
    """GET one sapi / rapi endpoint -> its `data` member. The locale pair is
    added to every call."""
    query = dict(params or {})
    query.update(LOCALE)
    body = get_json(base + path, query, label=label or path)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise CraigslistUpstreamError(f"unexpected body from {label or path}")
    return data


def sapi(path, params=None, **kwargs):
    return api(SAPI, path, params, **kwargs)


def rapi(path, params=None, **kwargs):
    return api(RAPI, path, params, **kwargs)


def redirect_target(url, *, label=None):
    """GET `url` without following redirects -> its Location header (None
    when the page answers 200). A 404 raises CraigslistNotFound."""
    label = label or url.split("craigslist.org", 1)[-1]

    def once():
        sess = _session()
        try:
            resp = sess.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
        except Exception as e:
            raise CraigslistUpstreamError(f"request failed: {type(e).__name__}: {e}")
        if resp.status_code in (301, 302, 303, 307, 308):
            return resp.headers.get("location")
        _classify(resp, label)
        return None

    return _retrying(once)


# ---- fan-out ------------------------------------------------------------------------

def run_parallel(fns):
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    with ThreadPoolExecutor(max_workers=min(FANOUT_WORKERS, len(fns))) as ex:
        futures = [ex.submit(fn) for fn in fns]
        return [f.result() for f in futures]


if __name__ == "__main__":
    # Smoke test: python craigslist/fetch.py [query]
    term = sys.argv[1] if len(sys.argv) > 1 else "bike"
    data = sapi("/postings/search/full", {"batch": "3-0-360-0-0", "searchPath": "sss", "query": term})
    print(f"search '{term}' in newyork: {data.get('totalResultCount')} results, {len(data.get('items') or [])} in the first call")
    areas = get_json(REFERENCE + "/Areas")
    print("areas:", len(areas))
