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
the pod's egress ever answers 403/429 the sessions move one fallback step
down for config.CRAIGSLIST_BLOCK_COOLDOWN seconds: since 2026-11-11 first
to the gateway (config.CRAIGSLIST_FALLBACK_EXIT = "gateway",
exit_gateway.py: a fresh gateway address on nearly every request, none refused;
the numbers are in config.py's Craigslist block), and only when that is
refused too to a sticky residential exit
(config.CRAIGSLIST_FALLBACK_PROXY_COUNTRY). A call the gateway keeps failing
makes its LAST retry on the residential exit, for that call only. When the
cooldown ends the next call is back on the server's own IP. The same shape
as the reed / soundcloud / woolworths transports. The gateway step is for
the three API hosts only (GATEWAY_HOSTS: sapi, rapi, reference): it holds
one route per host, so the two per-city surfaces (<hostname>.craigslist.org
/suggest and /<category>/<id>.html, ~700 hosts) skip it and ride the
residential exit during a cooldown, as they always did (_city_session).

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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures
from urllib.parse import urlencode, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import debug, fanout, ladder, retry, sessions

SITE = "https://www.craigslist.org"
SAPI = "https://sapi.craigslist.org/web/v8"
RAPI = "https://rapi.craigslist.org/web/v8"
REFERENCE = "https://reference.craigslist.org"
# The only hosts that may ride the gateway exit (one route per host there)
GATEWAY_HOSTS = frozenset(urlsplit(base).hostname for base in (SAPI, RAPI, REFERENCE))
IMPERSONATE = "chrome"

TIMEOUT = 45
FANOUT_WORKERS = 6

# The locale pair every API call carries. Labels come back in English; the
# data (listings, counts) is the same for any pair.
LOCALE = {"cc": "US", "lang": "en"}

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": egress.site_accept_language("craigslist"),
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
# One session per worker thread (egress.new_session: curl_cffi, or the gateway
# exit's look-alike). Direct egress by default; after a block the sessions
# move down the fallback steps (config.craigslist_fallback_exits(): the gateway
# exit, then a sticky residential exit) until `_fallback_until`, then go
# back to direct. The gateway carries the three API hosts only
# (GATEWAY_HOSTS); a per-city host skips that step (_city_session).
_local = threading.local()
_fallback_until = 0.0
_fallback_step = 1              # which fallback exit serves while the cooldown runs (1 = the first)
_fallback_lock = threading.Lock()


def _gateway_step(step):
    """True when fallback step `step` is the gateway exit with another exit
    configured behind it."""
    exits = config.craigslist_fallback_exits()
    return 0 < step < len(exits) and bool(config.gateway_exit(exits[step - 1]))


def _step():
    """The fallback step a session built NOW rides: 0 = the configured exit
    (the server's own IP), 1, 2 = config.craigslist_fallback_proxy(step)
    while the cooldown runs; one step past the gateway for the last retry of
    a call (_retrying)."""
    return ladder.current_step(_fallback_step, _fallback_until, _local, _gateway_step)


def _proxy_for_new_session(step=None):
    forced = config.craigslist_proxy()
    if forced:
        return forced
    step = _step() if step is None else step
    return config.craigslist_fallback_proxy(step) if step else None


def _session():
    sess = getattr(_local, "session", None)
    step = _step()
    if sess is not None and getattr(sess, "_craigslist_step", 0) != step:
        # 2026-11-11: a session follows its step. Before, a thread that took
        # the fallback exit during a cooldown stayed on it until that failed.
        _drop_session()
        sess = None
    if sess is None:
        # None (direct), a proxy URL, or the gateway exit ("gateway")
        exit = _proxy_for_new_session(step)
        sess = egress.new_session(exit, impersonate=IMPERSONATE)
        sess._craigslist_step = step
        sess._craigslist_gateway = bool(config.gateway_exit(exit))
        _local.session = sess
    return sess


def _city_session():
    """The session for a per-city host (<hostname>.craigslist.org: ~700 of
    them), which must never ride the gateway exit: it holds one route per
    host, ~149 for every scraper together. That is the thread's own session,
    unless it is a gateway one: then a second session on the exit behind
    the gateway (the residential one, what these calls rode during a
    cooldown before 2026-11-11), or on the server's own IP when nothing is
    behind it or CRAIGSLIST_PROXY_COUNTRY itself names the gateway."""
    sess = _session()
    if not getattr(sess, "_craigslist_gateway", False):
        return sess
    step = sess._craigslist_step
    city = getattr(_local, "city_session", None)
    if city is not None and getattr(city, "_craigslist_step", 0) != step:
        _close(city)
        city = None
    if city is None:
        behind = step + 1 if _gateway_step(step) and not config.craigslist_proxy() else 0
        city = egress.new_session(config.craigslist_fallback_proxy(behind) if behind else None,
                                  impersonate=IMPERSONATE)
        city._craigslist_step = step
        _local.city_session = city
    return city


def _session_for(url):
    """The thread's session for `url`: the API hosts may ride the gateway,
    every other host (a city's own) never does."""
    return _session() if urlsplit(url).hostname in GATEWAY_HOSTS else _city_session()


def _close(sess):
    sessions.close_quietly(sess)


def _drop_session():
    sess, city = getattr(_local, "session", None), getattr(_local, "city_session", None)
    _local.session = _local.city_session = None
    _close(sess)
    _close(city)


def _note_block(sess=None):
    """The exit session `sess` (default: this thread's own) rides was
    refused: new sessions move one fallback step down for a cooldown (own
    IP -> the gateway exit -> residential; on the last step they stay there,
    each on a fresh exit). No-op when no fallback is configured."""
    global _fallback_until, _fallback_step
    exits = config.craigslist_fallback_exits()
    if not exits:
        return
    if sess is not None and sess is getattr(_local, "city_session", None):
        # a city host refused the exit BEHIND the gateway: its retry takes a
        # fresh one; the gateway keeps serving the API hosts
        return
    own = getattr(getattr(_local, "session", None), "_craigslist_step", _step())
    if getattr(_local, "past_gateway", False) and own > (_fallback_step if time.time() < _fallback_until else 0):
        # the exit borrowed behind the gateway for this call's last retry
        # (_retrying) was refused: no verdict on the gateway, the step stays
        return
    step = min(own + 1, len(exits))
    with _fallback_lock:
        if time.time() >= _fallback_until or step > _fallback_step:
            _fallback_step = step
        _fallback_until = time.time() + config.CRAIGSLIST_BLOCK_COOLDOWN
    if len(exits) > 1:      # how often each step is refused is the number to watch
        print(f"craigslist: exit refused, sessions on fallback step {_fallback_step} "
              f"({exits[_fallback_step - 1]}) for {config.CRAIGSLIST_BLOCK_COOLDOWN} s")


def dump_debug(name, text):
    debug.dump("CRAIGSLIST_DEBUG_DIR", name, text, encoding="utf-8")


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


def _classify(resp, label, sess=None):
    status = resp.status_code
    if status == 404:
        raise CraigslistNotFound(_error_message(resp) or f"{label} not found")
    if status in (403, 429):
        dump_debug("blocked", resp.text)
        _note_block(sess)
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
    """Run fn() under the shared retry policy, each retry on a fresh session
    / exit. While the sessions are on the gateway exit step the LAST retry of
    a call rides the next step (the residential exit), for that call only:
    a gateway failure then costs latency, not the call."""
    return retry.run_flagged(fn, config, CraigslistUpstreamError, lambda e: _drop_session(), _local)


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
        sess = _session_for(full_url)
        try:
            resp = sess.get(full_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        except Exception as e:
            raise CraigslistUpstreamError(f"request failed: {type(e).__name__}: {e}")
        _classify(resp, label, sess)
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
        sess = _session_for(url)
        try:
            resp = sess.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
        except Exception as e:
            raise CraigslistUpstreamError(f"request failed: {type(e).__name__}: {e}")
        if resp.status_code in (301, 302, 303, 307, 308):
            return resp.headers.get("location")
        _classify(resp, label, sess)
        return None

    return _retrying(once)


# ---- fan-out ------------------------------------------------------------------------

# Long-lived pools (2026-10-23): their threads keep their thread-local curl
# sessions — and the sticky exits behind them — between requests. The
# per-call executor they replace opened fresh sessions/exits for every
# fan-out and threw them away when the call returned. Two levels: a fan-out
# running inside a fan-out goes to the second pool, so it neither waits on
# the workers it occupies (deadlock) nor loses its parallelism; a third
# level runs inline.
_pools = (ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="craigslist-fanout-0"),
          ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="craigslist-fanout-1"))


def _windowed(fns, workers):
    """Run `fns` on the pool for this nesting level, at most `workers` at a
    time; results align with `fns`. The first failure (in `fns` order)
    propagates once the callables already started have finished."""
    return fanout.windowed(_pools, "craigslist-fanout", fns, workers)


def run_parallel(fns):
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    return _windowed(fns, FANOUT_WORKERS)


if __name__ == "__main__":
    # Smoke test: python craigslist/fetch.py [query]
    term = sys.argv[1] if len(sys.argv) > 1 else "bike"
    data = sapi("/postings/search/full", {"batch": "3-0-360-0-0", "searchPath": "sss", "query": term})
    print(f"search '{term}' in newyork: {data.get('totalResultCount')} results, {len(data.get('items') or [])} in the first call")
    areas = get_json(REFERENCE + "/Areas")
    print("areas:", len(areas))
