"""kleinanzeigen/fetch.py's search-box Algolia config on identity_pool: no
network, the homepage read is faked. The pool's own rules live in
tests/test_identity_pool.py. Plus the gateway exit tier in front of the
residential exit (config.KLEINANZEIGEN_GATEWAY_EXIT): both exits are faked.

    python -m pytest kleinanzeigen/test_fetch.py -q
"""
import threading
import time

import pytest

import config
from kleinanzeigen import fetch
from scraper_utils.testing import hosted_only

ROTATED = {"index": "ebayk_prod_suggest_v2", "app_id": "NEWAPP1234", "api_key": "newkey0123456789"}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


@pytest.fixture
def suggest_cfg(monkeypatch):
    """The module's pool with a counting fake homepage read."""
    pool = fetch._suggest
    pool.clear()
    pool._last_mint.clear()
    pool._mint_times.clear()
    reads = []

    def read_home(key, hint):
        time.sleep(0.05)                     # let the wave pile up behind one mint
        reads.append(key)
        return dict(ROTATED)

    monkeypatch.setattr(pool, "_mint_fn", read_home)
    pool.reads = reads
    yield pool
    pool.clear()
    pool._last_mint.clear()
    pool._mint_times.clear()


def test_a_4xx_wave_costs_one_homepage_read_and_none_inside_the_interval(suggest_cfg, monkeypatch):
    accepted = {ROTATED["api_key"]}

    def fake_request(url, params=None, *, headers=None, method="GET", body=None, label=None, missing=None):
        if headers["x-algolia-api-key"] not in accepted:
            raise fetch.KleinanzeigenBadRequest(f"{label} (HTTP 400)")
        return FakeResponse({"hits": [{"query": body["query"]}], "index": url})

    monkeypatch.setattr(fetch, "request", fake_request)
    assert fetch._suggest.get().value == fetch.SUGGEST_DEFAULTS, "seeded"

    results, errors = [], []

    def worker():
        try:
            results.append(fetch.suggest("fahrrad", 5))
        except Exception as e:                # pragma: no cover - the assert below reports it
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors and len(results) == 8
    assert all(r["hits"][0]["query"] == "fahrrad" and ROTATED["index"] in r["index"] for r in results)
    assert suggest_cfg.reads == [""], "a 4xx wave across threads: ONE homepage read"

    # the rotated config is refused too: inside the interval the same config
    # comes back and the call fails as it always did - no read
    accepted.clear()
    with pytest.raises(fetch.KleinanzeigenUpstreamError, match="refused"):
        fetch.suggest("fahrrad", 5)
    assert suggest_cfg.reads == [""]
    assert fetch._suggest.get().value == ROTATED


# ---- the gateway exit tier (2026-11-11) -------------------------------------------------

class GatewayResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self.body = body if body is not None else {}
        self.text = "{}"

    def json(self):
        return self.body


class FakeGatewaySession:
    """What egress.new_session("gateway:de") returns, as far as fetch uses it."""

    def __init__(self, answers, calls):
        self.answers, self.calls = answers, calls
        self.cookies = self
        self.cleared = 0
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs.get("params")))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    post = get

    def clear(self):
        self.cleared += 1

    def close(self):
        self.closed = True


@pytest.fixture
def exits(monkeypatch):
    """The gateway and the residential exit, both faked: `exits.gateway`
    is the queue of gateway answers (a response or an exception),
    `exits.gateway_calls` / `exits.residential_calls` what each one got."""
    class Exits:
        pass

    exits = Exits()
    exits.gateway, exits.gateway_calls, exits.residential_calls = [], [], []
    exits.sessions, exits.built, exits.residential = [], [], []

    def new_session(exit, impersonate=None):
        exits.built.append(exit)
        exits.sessions.append(FakeGatewaySession(exits.gateway, exits.gateway_calls))
        return exits.sessions[-1]

    def once(method, url, params, headers, body):
        fetch._local.via_gateway = False
        exits.residential_calls.append((method, url, params))
        return exits.residential.pop(0) if exits.residential else GatewayResponse(200, {"from": "residential"})

    monkeypatch.setattr(fetch, "_new_session", new_session)
    monkeypatch.setattr(fetch, "_once", once)
    monkeypatch.setattr(config, "KLEINANZEIGEN_GATEWAY_EXIT", "gateway:de")
    monkeypatch.setattr(config, "KLEINANZEIGEN_GATEWAY_FAILURES", 3)
    monkeypatch.setattr(config, "KLEINANZEIGEN_GATEWAY_PAUSE", 300)
    fetch._gateway.update(failures=0, paused_until=0.0)
    fetch._local.gateway = None
    yield exits
    fetch._gateway.update(failures=0, paused_until=0.0)
    fetch._local.gateway = None


@hosted_only
def test_the_knob_off_never_touches_the_gateway(exits, monkeypatch):
    for value in (None, "", "de", "datacenter"):
        monkeypatch.setattr(config, "KLEINANZEIGEN_GATEWAY_EXIT", value)
        assert fetch.request("https://api.kleinanzeigen.de/api/ads.json").json() == {"from": "residential"}
    assert exits.built == [] and len(exits.residential_calls) == 4


@hosted_only
def test_a_gateway_answer_is_the_answer_and_no_cookie_outlives_the_request(exits):
    exits.gateway += [GatewayResponse(200, {"from": "gateway"}), GatewayResponse(200, {"from": "gateway"})]
    assert fetch.request("https://api.kleinanzeigen.de/api/ads.json", {"q": "rad", "empty": ""}).json() == {"from": "gateway"}
    assert fetch.request("https://api.kleinanzeigen.de/api/ads.json").json() == {"from": "gateway"}
    assert exits.built == ["gateway:de"], "one gateway session per thread, reused"
    assert exits.gateway_calls[0] == ("GET", "https://api.kleinanzeigen.de/api/ads.json", {"q": "rad"})
    assert exits.sessions[0].cleared == 2, "Akamai's cookies are dropped after every request"
    assert exits.residential_calls == []


@hosted_only
def test_the_us_gateway_is_a_knob_value_too(exits, monkeypatch):
    monkeypatch.setattr(config, "KLEINANZEIGEN_GATEWAY_EXIT", "gateway")
    exits.gateway.append(GatewayResponse(200))
    fetch.request("https://api.kleinanzeigen.de/api/ads.json")
    assert exits.built == ["gateway"]


@hosted_only
@pytest.mark.parametrize("failure", [GatewayResponse(403), GatewayResponse(429), GatewayResponse(503),
                                     RuntimeError("gateway 504")])
def test_a_gateway_failure_falls_back_to_the_residential_exit_at_no_cost(exits, failure):
    """A ban, a rate limit, a 5xx or a transport error on the gateway: the
    call goes on with the residential exit, with every attempt and every
    rotation it had before the gateway existed."""
    exits.gateway.append(failure)
    # the residential path is then banned ROTATE_ATTEMPTS times and still answers
    exits.residential += [None] * config.KLEINANZEIGEN_ROTATE_ATTEMPTS
    resp = fetch.request("https://api.kleinanzeigen.de/api/ads.json")
    assert resp.json() == {"from": "residential"}
    assert len(exits.gateway_calls) == 1, "the gateway gets ONE try per call"
    assert len(exits.residential_calls) == config.KLEINANZEIGEN_ROTATE_ATTEMPTS + 1
    if isinstance(failure, Exception):
        assert exits.sessions[0].closed and fetch._local.gateway is None, "a failed gateway session is dropped"


@hosted_only
def test_not_found_and_bad_request_from_the_gateway_are_final(exits):
    exits.gateway += [GatewayResponse(404), GatewayResponse(400)]
    with pytest.raises(fetch.KleinanzeigenNotFound):
        fetch.request("https://api.kleinanzeigen.de/api/ads/1.json")
    with pytest.raises(fetch.KleinanzeigenBadRequest):
        fetch.request("https://api.kleinanzeigen.de/api/ads.json")
    assert exits.residential_calls == [] and fetch._gateway["failures"] == 0


@hosted_only
def test_a_redirect_the_gateway_did_not_follow_is_not_the_answer(exits):
    """The gateway hands back a 3xx it cannot follow (another site, http://):
    the residential exit (whose curl follows redirects) answers instead, and
    the gateway's failure run is untouched."""
    exits.gateway.append(GatewayResponse(302))
    resp = fetch.request("https://www.kleinanzeigen.de/s-anzeige/1")
    assert resp.json() == {"from": "residential"}
    assert len(exits.gateway_calls) == 1 and len(exits.residential_calls) == 1
    assert fetch._gateway["failures"] == 0 and fetch._gateway["paused_until"] == 0.0


@hosted_only
def test_failures_in_a_row_pause_the_gateway_and_a_success_clears_the_run(exits):
    url = "https://api.kleinanzeigen.de/api/ads.json"
    # two failures, a success, two failures: never three in a row -> no pause
    exits.gateway += [GatewayResponse(403), GatewayResponse(403), GatewayResponse(200),
                      GatewayResponse(403), GatewayResponse(403)]
    for _ in range(5):
        fetch.request(url)
    assert len(exits.gateway_calls) == 5 and fetch._gateway["paused_until"] == 0.0
    # the third in a row pauses it: the next calls go straight to the residential exit
    exits.gateway.append(GatewayResponse(403))
    fetch.request(url)
    assert 299 < fetch._gateway["paused_until"] - time.monotonic() <= 300
    before = len(exits.residential_calls)
    fetch.request(url)
    fetch.request(url)
    assert len(exits.gateway_calls) == 6 and len(exits.residential_calls) == before + 2
    # after the pause the gateway is tried again
    fetch._gateway["paused_until"] = time.monotonic() - 1
    exits.gateway.append(GatewayResponse(200, {"from": "gateway"}))
    assert fetch.request(url).json() == {"from": "gateway"}
    assert len(exits.gateway_calls) == 7


@hosted_only
def test_an_empty_search_on_the_gateway_repeats_there_without_rotating_the_residential_exit(exits, monkeypatch):
    rotations = []
    monkeypatch.setattr(fetch, "rotate_egress", lambda seen_version=None: rotations.append(seen_version))
    empty = {"ads": {"value": {"paging": {"numFound": "0"}}}}
    full = {"ads": {"value": {"paging": {"numFound": "7"}}}}
    exits.gateway += [GatewayResponse(200, empty), GatewayResponse(200, full)]
    assert fetch.search({"q": "fahrrad"}) == full
    assert len(exits.gateway_calls) == 2 and rotations == [] and exits.residential_calls == []
    # the same empty answer from the residential exit still rotates it, as before
    exits.gateway.append(GatewayResponse(403))
    exits.residential += [GatewayResponse(200, empty), GatewayResponse(200, full)]
    monkeypatch.setattr(config, "KLEINANZEIGEN_GATEWAY_FAILURES", 1)     # the ban pauses the gateway at once
    assert fetch.search({"q": "fahrrad"}) == full
    assert rotations == [None]
