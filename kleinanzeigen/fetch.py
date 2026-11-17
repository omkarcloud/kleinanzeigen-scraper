"""Kleinanzeigen transport: plain curl_cffi with browser-impersonated TLS.
No login, no cookies, no browser.

Three surfaces, all anonymous (validated 2026-10-05):

  app JSON API    api.kleinanzeigen.de/api/* — the Android app's own API
                  (the app's public client credential as HTTP Basic auth):
                    ads.json                  search (q, categoryId, locationId,
                                              distance, latitude/longitude,
                                              minPrice/maxPrice, adType, posterType,
                                              sortType, pictureRequired, buyNowOnly,
                                              shippable, includeTopAds, userIds,
                                              storeIds, attr[<name>]=…; page is
                                              0-based, size <= 100, window 10,000)
                    ads/<id>.json             one listing, full
                    v2/counters/ads/vip       view counts (adIds=a,b,…)
                    users/public/<id>/profile seller profile + badges
                    stores/<id>.json, stores.json?urlExtension=<slug>
                    categories.json           the whole category tree
                    attributes/metadata/<category id>.json   filters of a category
                    locations.json?q= | ?latitude=&longitude=, locations/<id>.json,
                    locations/top-locations.json
  site            www.kleinanzeigen.de/s-anzeige/<id> — only for the "Das könnte
                  dich auch interessieren" block (the API has no similar-ads call)
  autocomplete    autocomplete.kleinanzeigen.de — the search box's Algolia index
                  (public search key from the page's data-algolia-* attributes)

Egress: Kleinanzeigen bans whole IP ranges (HTTP 403, JSON body "IP-Bereich
vorübergehend gesperrt") — every non-German / datacenter range tried was
banned on all three hosts, while German residential exits answer 200. So
every session runs through ONE sticky config.KLEINANZEIGEN_PROXY_COUNTRY
exit shared by the process, rotated after KLEINANZEIGEN_REQUESTS_PER_EXIT
requests, on a ban, and on a transport error.

Since 2026-11-11 every call is tried ONCE through the gateway exit first
(config.KLEINANZEIGEN_GATEWAY_EXIT = "gateway:de", Frankfurt; _gateway_once):
the gateway's ranges are not banned — 181 of 181 requests on the three hosts
answered there, a 6-wide burst of 147 included (numbers in the config
block). A fresh gateway address per request and no cookies kept. A ban, a 5xx
or a transport error there falls through to the residential exit above,
unchanged; a run of failures pauses the gateway (_gateway_result).

Trap: the search API sometimes answers 200 with numFound 0 for a query that
has results (seen in streaks on one exit). search() callers pass
`retry_empty=True`, which repeats an empty answer once on a fresh exit.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  KleinanzeigenUpstreamError  transport failure / 5xx        — retried
  KleinanzeigenBlocked        IP-range ban on every exit tried
  KleinanzeigenBadRequest     upstream rejected the params    — never retried
  KleinanzeigenNotFound       unknown listing / seller / store / location
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
import identity_pool
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import debug, fanout, sessions

API = "https://api.kleinanzeigen.de/api"
SITE = "https://www.kleinanzeigen.de"
SUGGEST_URL = "https://autocomplete.kleinanzeigen.de/1/indexes/{index}/query"
IMPERSONATE = "chrome"
TIMEOUT = 30
FANOUT_WORKERS = 10

# The Android app's client credential (android:TaR60pEttY) — identical in
# every install, it identifies the app, not a user.
API_HEADERS = {
    "authorization": "Basic YW5kcm9pZDpUYVI2MHBFdHRZ",
    "user-agent": "okhttp/4.10.0",
    "accept": "application/json",
    "accept-language": egress.site_accept_language("kleinanzeigen"),
}
HTML_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": egress.site_accept_language("kleinanzeigen"),
}
# Search-box Algolia index; refreshed from the homepage when the key is refused.
SUGGEST_DEFAULTS = {"index": "ebayk_prod_suggest", "app_id": "8YS7J0Y0H2",
                    "api_key": "a29d2bf6a595867b238e885289dddadf"}


class KleinanzeigenUpstreamError(UpstreamError):
    """Transport failure or 5xx — retryable."""


class KleinanzeigenBlocked(KleinanzeigenUpstreamError, Blocked):
    """IP-range ban that survived fresh exits."""


class KleinanzeigenBadRequest(BadRequest):
    """Upstream rejected the params — never retried."""


class KleinanzeigenNotFound(NotFound):
    """Unknown listing / seller / store / location — never retried."""


# ---- egress + sessions ---------------------------------------------------------------
# ONE sticky exit for the whole process; rotate_egress() bumps the version so
# each worker thread rebuilds its curl session on the new exit.
_egress_lock = threading.Lock()
_egress = {"proxy": None, "ready": False, "version": 0, "requests": 0}
_local = threading.local()
# The gateway exit in front of that exit (config.KLEINANZEIGEN_GATEWAY_EXIT,
# 2026-11-11): each call is tried there ONCE, from a fresh gateway address and
# with no cookies; a ban, a transport error or a 5xx there sends the rest of
# the call to the sticky residential exit, exactly as before the gateway.
# KLEINANZEIGEN_GATEWAY_FAILURES failures in a row (process-wide: the site
# bans whole ranges) pause it for KLEINANZEIGEN_GATEWAY_PAUSE seconds, so a
# banned gateway range costs a few wasted requests, not one per call.
_new_session = egress.new_session       # the egress MODULE's: egress() below rebinds the name
_gateway = {"failures": 0, "paused_until": 0.0}


def egress():
    """(proxy URL | None, version); counts the request against the exit's budget."""
    with _egress_lock:
        if _egress["ready"] and _egress["requests"] >= config.KLEINANZEIGEN_REQUESTS_PER_EXIT:
            _egress["ready"] = False
        if not _egress["ready"]:
            _egress["proxy"] = config.kleinanzeigen_proxy()
            _egress["ready"] = True
            _egress["version"] += 1
            _egress["requests"] = 0
        _egress["requests"] += 1
        return _egress["proxy"], _egress["version"]


def rotate_egress(seen_version=None):
    """Pick a new exit on the next request. With `seen_version`, only if no
    other thread rotated since that request started (one ban must not set
    off a rotation storm across the worker threads)."""
    with _egress_lock:
        if seen_version is None or _egress["version"] == seen_version:
            _egress["ready"] = False


def _session():
    proxy, version = egress()
    sess = getattr(_local, "session", None)
    if sess is not None and getattr(sess, "_ka_egress", None) != version:
        _drop_session()
        sess = None
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess._ka_egress = version
        _local.session = sess
    return sess, version


def _drop_session():
    sessions.drop(_local)


def _gateway_exit():
    """'gateway:de' when a call may try the gateway exit now; None when no
    gateway is configured or it is paused after a run of failures."""
    exit = config.kleinanzeigen_gateway_exit()
    if not exit:
        return None
    with _egress_lock:
        if time.monotonic() < _gateway["paused_until"]:
            return None
    return exit


def _gateway_result(ok, why=""):
    """Count one gateway answer: a usable one clears the run of failures,
    the KLEINANZEIGEN_GATEWAY_FAILURES-th failure in a row pauses it."""
    with _egress_lock:
        if ok:
            _gateway["failures"] = 0
            return
        _gateway["failures"] += 1
        if _gateway["failures"] < config.KLEINANZEIGEN_GATEWAY_FAILURES:
            return
        _gateway["failures"] = 0
        _gateway["paused_until"] = time.monotonic() + config.KLEINANZEIGEN_GATEWAY_PAUSE
    print(f"kleinanzeigen: {config.KLEINANZEIGEN_GATEWAY_FAILURES} failures in a row on the gateway exit "
          f"(last: {why}); residential exit only for {config.KLEINANZEIGEN_GATEWAY_PAUSE} s")


def _drop_gateway_session():
    sess = getattr(_local, "gateway", None)
    _local.gateway = None
    sessions.close_quietly(sess)


def _gateway_once(method, url, params, headers, body, exit):
    """ONE try through the gateway exit -> the response (any status below
    500 that is not a ban), or None when the gateway failed, the address
    was banned / rate limited or the site answered 5xx: the caller goes on
    with the residential exit."""
    _local.via_gateway = True
    sess = getattr(_local, "gateway", None)
    try:
        if sess is None:
            sess = _local.gateway = _new_session(exit, impersonate=IMPERSONATE)
        if method == "POST":
            resp = sess.post(url, params=params, headers=headers, json=body, timeout=TIMEOUT)
        else:
            resp = sess.get(url, params=params, headers=headers, timeout=TIMEOUT)
        # every request leaves from another gateway address: Akamai's bm_sz /
        # _abck from the previous address must not travel with the next one
        sess.cookies.clear()
    except Exception as e:
        _drop_gateway_session()
        why = f"{type(e).__name__}: {e}"
    else:
        if 300 <= resp.status_code < 400:
            # a redirect the gateway does not follow (another site, http://, too
            # many hops): not an answer and not a gateway failure; the
            # residential exit's curl follows it
            return None
        if not looks_banned(resp) and resp.status_code < 500:
            _gateway_result(True)
            return resp
        dump_debug("gateway_banned" if looks_banned(resp) else "gateway_error", resp.text)
        why = f"HTTP {resp.status_code}"
    print(f"kleinanzeigen: gateway exit ({exit}) failed on {url}, residential exit next: {why}"[:300])
    _gateway_result(False, why)
    return None


def dump_debug(name, text):
    """Write a raw response to $KLEINANZEIGEN_DEBUG_DIR/<name>.txt."""
    debug.dump("KLEINANZEIGEN_DEBUG_DIR", name, text, encoding="utf-8")


def looks_banned(resp):
    """The IP-range ban (403 JSON "IP-Bereich vorübergehend gesperrt"), a
    rate limit, or the site's Akamai 403 page."""
    return resp.status_code in (403, 429)


# ---- requests ---------------------------------------------------------------------

def _once(method, url, params, headers, body):
    _local.via_gateway = False
    sess, version = _session()
    try:
        if method == "POST":
            resp = sess.post(url, params=params, headers=headers, json=body, timeout=TIMEOUT)
        else:
            resp = sess.get(url, params=params, headers=headers, timeout=TIMEOUT)
    except Exception as e:
        _drop_session()
        rotate_egress(version)              # a dead / flaky exit: move on
        raise KleinanzeigenUpstreamError(f"request failed: {type(e).__name__}: {e}")
    if looks_banned(resp):
        dump_debug("banned", resp.text)
        _drop_session()
        rotate_egress(version)
        return None
    return resp


def request(url, params=None, *, headers=None, method="GET", body=None, label=None, missing=None):
    """One call -> the curl response (2xx). 404 raises KleinanzeigenNotFound,
    400 KleinanzeigenBadRequest; a ban rotates the exit (up to
    config.KLEINANZEIGEN_ROTATE_ATTEMPTS fresh exits); 5xx / transport
    errors retry with backoff. With config.KLEINANZEIGEN_GATEWAY_EXIT the
    gateway exit gets one try first; that try costs no attempt and no
    rotation, so a failure there leaves the residential path whole."""
    label = label or url
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    last = None
    bans = 0
    attempt = 0
    gateway = _gateway_exit()
    while attempt < config.MAX_RETRIES:
        try:
            if gateway:
                resp, gateway = _gateway_once(method, url, params, headers, body, gateway), None
                if resp is None:
                    continue                # the residential exit, as before the gateway
            else:
                resp = _once(method, url, params, headers, body)
        except KleinanzeigenUpstreamError as e:
            last = e
        else:
            if resp is None:
                bans += 1
                last = KleinanzeigenBlocked(f"IP range banned on {label}")
                if bans <= config.KLEINANZEIGEN_ROTATE_ATTEMPTS and config.KLEINANZEIGEN_PROXY_COUNTRY:
                    continue                # a fresh exit, not a retry
                raise last
            if resp.status_code == 404:
                raise KleinanzeigenNotFound(missing or f"{label}: not found on Kleinanzeigen")
            if resp.status_code == 400:
                raise KleinanzeigenBadRequest(f"{label} (HTTP 400)")
            if resp.status_code < 400:
                return resp
            dump_debug("error", resp.text)
            last = KleinanzeigenUpstreamError(f"HTTP {resp.status_code} on {label}")
        attempt += 1
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def api(path, params=None, *, label=None, missing=None):
    """GET api.kleinanzeigen.de/api/<path> -> parsed JSON."""
    resp = request(f"{API}/{path}", params, headers=API_HEADERS, label=label or path, missing=missing)
    try:
        return resp.json()
    except Exception:
        dump_debug("nonjson", resp.text)
        raise KleinanzeigenUpstreamError(f"{label or path} returned a non-JSON body")


def _found(data):
    """numFound of a raw ads.json answer (the JAXB wrapper keys vary)."""
    for key, value in (data or {}).items():
        if key.endswith("}ads") or key == "ads":
            node = value.get("value", value) if isinstance(value, dict) else {}
            paging = node.get("paging") or {}
            try:
                return int(paging.get("numFound") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def search(params, *, label="search", retry_empty=True):
    """GET ads.json. An empty answer is repeated once on a fresh exit: the
    API intermittently reports numFound 0 for queries that do have results."""
    data = api("ads.json", params, label=label)
    if retry_empty and _found(data) == 0:
        if not getattr(_local, "via_gateway", False):     # the gateway's next request is a fresh address anyway
            _drop_session()
            rotate_egress()
        try:
            again = api("ads.json", params, label=label)
        except (UpstreamError, BadRequest, NotFound):
            return data
        if _found(again):
            return again
    return data


def site_html(path, params=None, *, label=None, missing=None):
    """GET a www.kleinanzeigen.de page -> HTML text."""
    return request(SITE + path, params, headers=HTML_HEADERS, label=label or path, missing=missing).text


# ---- search-box suggestions (Algolia) ------------------------------------------------
# The {index, app_id, api_key} dict lives in a shared identity_pool seeded
# with SUGGEST_DEFAULTS (replaced the `_suggest` dict + lock on 2026-11-09):
# a 4xx wave across the 64 request threads costs ONE homepage read, the
# other threads get the winner's config, and a second refresh inside
# SUGGEST_REFRESH_INTERVAL returns the same config (the request then fails
# as it did before).
SUGGEST_REFRESH_INTERVAL = 30    # seconds between two homepage re-reads


class _NoSuggestConfig(Exception):
    """The homepage carries no complete Algolia config (not a transport error)."""


def _mint_suggest_config(key, hint):
    """The pool's mint: the Algolia index / app id / key from the homepage's
    data-algolia-* attributes. Transport errors propagate as before;
    _NoSuggestConfig when the page lacks one of the three."""
    import re
    markup = site_html("/", label="homepage")
    found = {}
    for name, attr in (("index", "data-algolia-index"), ("app_id", "data-algolia-app-id"),
                       ("api_key", "data-algolia-api-key")):
        m = re.search(attr + r'="([^"]+)"', markup)
        if m:
            found[name] = m.group(1)
    if len(found) != 3:
        raise _NoSuggestConfig()
    return found


_suggest = identity_pool.Pool("kleinanzeigen-suggest", _mint_suggest_config,
                              min_interval=SUGGEST_REFRESH_INTERVAL, seed={"": dict(SUGGEST_DEFAULTS)})


def _refresh_suggest_config(used):
    """`used` (the pool identity whose config just got a 4xx) is stale: ask
    the pool for its replacement. True when a different config is now
    current (retry with it); False when it is unchanged (throttled, or the
    page carries the same values / none). Transport errors propagate."""
    try:
        fresh = _suggest.get(replace=used)
    except _NoSuggestConfig:
        return False
    return fresh.value != used.value


def suggest(query, limit):
    """Search-box completions -> the Algolia answer (dict with `hits`)."""
    for attempt in (1, 2):
        used = _suggest.get()
        cfg = used.value
        headers = {"x-algolia-application-id": cfg["app_id"], "x-algolia-api-key": cfg["api_key"],
                   "content-type": "application/json", "origin": SITE, "referer": SITE + "/"}
        try:
            resp = request(SUGGEST_URL.format(index=cfg["index"]), headers=headers, method="POST",
                           body={"query": query, "hitsPerPage": limit}, label="suggestions")
            return resp.json()
        except (KleinanzeigenBadRequest, KleinanzeigenNotFound):
            # a rotated key / index answers 4xx: re-read it from the page once
            if attempt == 2 or not _refresh_suggest_config(used):
                raise KleinanzeigenUpstreamError("suggestions: the search-box index refused the request")
        except ValueError:
            raise KleinanzeigenUpstreamError("suggestions returned a non-JSON body")


# ---- parallel fan-out ---------------------------------------------------------------

# Long-lived pools (2026-10-23): their threads keep their thread-local curl
# sessions — and the sticky exits behind them — between requests. The
# per-call executor they replace opened fresh sessions/exits for every
# fan-out and threw them away when the call returned. Two levels: a fan-out
# running inside a fan-out goes to the second pool, so it neither waits on
# the workers it occupies (deadlock) nor loses its parallelism; a third
# level runs inline.
_pools = (ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="kleinanzeigen-fanout-0"),
          ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="kleinanzeigen-fanout-1"))


def _windowed(fns, workers):
    """Run `fns` on the pool for this nesting level, at most `workers` at a
    time; results align with `fns`. The first failure (in `fns` order)
    propagates once the callables already started have finished."""
    return fanout.windowed(_pools, "kleinanzeigen-fanout", fns, workers)


def run_parallel(fns, workers=FANOUT_WORKERS):
    """Run zero-arg callables in parallel; results align with `fns`.
    Exceptions propagate from the first failing call."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    return _windowed(fns, workers)


def run_parallel_quiet(fns, workers=FANOUT_WORKERS):
    """run_parallel where a failing call yields None instead of raising
    (optional enrichment: view counts, seller profile, similar listings)."""
    def safe(fn):
        def call():
            try:
                return fn()
            except (UpstreamError, NotFound, BadRequest, ValueError):
                return None
        return call
    return run_parallel([safe(fn) for fn in fns], workers)


# ---- memo -----------------------------------------------------------------------------

class Memo:
    """Small TTL memo for reference data (category tree, filter metadata,
    resolved locations / store slugs)."""

    def __init__(self, ttl, max_size=5000):
        self.ttl, self.max_size = ttl, max_size
        self.lock = threading.Lock()
        self.data = {}

    def get(self, key):
        with self.lock:
            hit = self.data.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        return None

    def put(self, key, value):
        with self.lock:
            if len(self.data) >= self.max_size:
                self.data.clear()
            self.data[key] = (time.time() + self.ttl, value)
        return value


if __name__ == "__main__":
    # Smoke test: python kleinanzeigen/fetch.py [api path]
    target = sys.argv[1] if len(sys.argv) > 1 else "ads.json?q=fahrrad&size=1"
    path, _, qs = target.partition("?")
    print(json.dumps(api(path, dict(p.split("=", 1) for p in qs.split("&") if "=" in p)),
                     ensure_ascii=False)[:800])
