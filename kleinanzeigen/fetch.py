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
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

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
    "accept-language": "de-DE",
}
HTML_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "de-DE,de;q=0.9",
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
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def dump_debug(name, text):
    """Write a raw response to $KLEINANZEIGEN_DEBUG_DIR/<name>.txt."""
    dbg = os.environ.get("KLEINANZEIGEN_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass


def looks_banned(resp):
    """The IP-range ban (403 JSON "IP-Bereich vorübergehend gesperrt"), a
    rate limit, or the site's Akamai 403 page."""
    return resp.status_code in (403, 429)


# ---- requests ---------------------------------------------------------------------

def _once(method, url, params, headers, body):
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
    errors retry with backoff."""
    label = label or url
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    last = None
    bans = 0
    attempt = 0
    while attempt < config.MAX_RETRIES:
        try:
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

_suggest = dict(SUGGEST_DEFAULTS)
_suggest_lock = threading.Lock()


def _refresh_suggest_config():
    """Re-read the Algolia index / app id / key from the homepage."""
    import re
    markup = site_html("/", label="homepage")
    found = {}
    for key, attr in (("index", "data-algolia-index"), ("app_id", "data-algolia-app-id"),
                      ("api_key", "data-algolia-api-key")):
        m = re.search(attr + r'="([^"]+)"', markup)
        if m:
            found[key] = m.group(1)
    if len(found) == 3:
        with _suggest_lock:
            _suggest.update(found)
        return True
    return False


def suggest(query, limit):
    """Search-box completions -> the Algolia answer (dict with `hits`)."""
    for attempt in (1, 2):
        with _suggest_lock:
            cfg = dict(_suggest)
        headers = {"x-algolia-application-id": cfg["app_id"], "x-algolia-api-key": cfg["api_key"],
                   "content-type": "application/json", "origin": SITE, "referer": SITE + "/"}
        try:
            resp = request(SUGGEST_URL.format(index=cfg["index"]), headers=headers, method="POST",
                           body={"query": query, "hitsPerPage": limit}, label="suggestions")
            return resp.json()
        except (KleinanzeigenBadRequest, KleinanzeigenNotFound):
            # a rotated key / index answers 4xx: re-read it from the page once
            if attempt == 2 or not _refresh_suggest_config():
                raise KleinanzeigenUpstreamError("suggestions: the search-box index refused the request")
        except ValueError:
            raise KleinanzeigenUpstreamError("suggestions returned a non-JSON body")


# ---- parallel fan-out ---------------------------------------------------------------

def run_parallel(fns, workers=FANOUT_WORKERS):
    """Run zero-arg callables in parallel; results align with `fns`.
    Exceptions propagate from the first failing call."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    with ThreadPoolExecutor(max_workers=min(workers, len(fns))) as ex:
        futures = [ex.submit(fn) for fn in fns]
        return [f.result() for f in futures]


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
