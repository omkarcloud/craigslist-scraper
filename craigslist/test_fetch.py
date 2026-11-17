"""Craigslist transport egress (2026-11-11): calls leave from the server's own IP;
after a block the sessions step to the gateway exit
(config.CRAIGSLIST_FALLBACK_EXIT), and only when that is refused too — or for the
last retry of a call the gateway keeps failing — to the residential exit
(config.CRAIGSLIST_FALLBACK_PROXY_COUNTRY). Only the three API hosts
ride the gateway; a city's own host (<city>.craigslist.org) skips that step.
Offline, no network.

    python -m pytest craigslist/test_fetch.py -q
"""
import json
import os
import sys

from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from craigslist import fetch as F  # noqa: E402
from scraper_utils.testing import hosted_only

RESI = "http://resi-us:1"
CITY = "https://newyork.craigslist.org"
SEARCH = json.dumps({"apiVersion": 8, "data": {"items": ["bike"]}, "errors": []})
HOSTS = []            # (exit, host) of every request sent


class Resp:
    def __init__(self, status, text):
        self.status_code, self.text, self.headers = status, text, {}

    def json(self):
        return json.loads(self.text)


class Session:
    """What egress.new_session() hands out, answering per exit from `plan`
    (exit -> a status, an exception to raise, or a list of those)."""

    def __init__(self, exit, plan, seen):
        self.exit, self.plan, self.seen, self.hosts = exit, plan, seen, HOSTS

    def _answer(self, url, ok):
        self.seen.append(self.exit)
        self.hosts.append((self.exit, urlsplit(url).hostname))
        answer = self.plan.get(self.exit, 200)
        answer = answer.pop(0) if isinstance(answer, list) else answer
        if isinstance(answer, Exception):
            raise answer
        return Resp(answer, ok if answer == 200 else "<html>refused</html>")

    def get(self, url, allow_redirects=True, **kwargs):
        if urlsplit(url).hostname in F.GATEWAY_HOSTS:
            return self._answer(url, SEARCH)
        resp = self._answer(url, '["bike rack"]')
        if not allow_redirects and resp.status_code == 200:
            resp.status_code, resp.headers = 301, {"location": "https://www.craigslist.org/view/d/x/abcdefghijklmnopqrstuv"}
        return resp

    def close(self):
        pass


@pytest.fixture
def ladder(monkeypatch):
    """(plan, seen): the exits' answers and the exit of every request sent."""
    plan, seen = {}, []
    del HOSTS[:]
    F._drop_session()
    monkeypatch.setattr(F, "_fallback_until", 0.0)
    monkeypatch.setattr(F, "_fallback_step", 1)
    monkeypatch.setattr(F.egress, "new_session", lambda exit=None, impersonate=None: Session(exit, plan, seen))
    monkeypatch.setattr(config, "residential_proxy", lambda c, states=None: f"http://resi-{c}:1")
    monkeypatch.setattr(config, "MAX_RETRIES", 3)
    monkeypatch.setattr(config, "RETRY_BACKOFF", 0)
    monkeypatch.setattr(config, "CRAIGSLIST_PROXY_COUNTRY", None)
    monkeypatch.setattr(config, "CRAIGSLIST_FALLBACK_EXIT", "gateway")
    monkeypatch.setattr(config, "CRAIGSLIST_FALLBACK_PROXY_COUNTRY", "us")
    yield plan, seen
    F._drop_session()


OK = {"items": ["bike"]}


def call():
    return F.sapi("/suggest/search", {"query": "bik"})


def city():
    return F.get_json(CITY + "/suggest", {"term": "bik"})


@hosted_only
def test_default_is_own_ip_then_the_gateway_then_residential():
    assert config.CRAIGSLIST_PROXY_COUNTRY is None
    assert config.CRAIGSLIST_FALLBACK_EXIT == "gateway"
    assert config.CRAIGSLIST_FALLBACK_PROXY_COUNTRY == "us"


@hosted_only
def test_fallback_steps(ladder, monkeypatch):
    assert config.craigslist_proxy() is None
    assert config.craigslist_fallback_exits() == ["gateway", "us"]
    assert [config.craigslist_fallback_proxy(step) for step in (1, 2, 3)] == ["gateway", RESI, RESI]
    monkeypatch.setattr(config, "CRAIGSLIST_FALLBACK_EXIT", None)           # the ladder before the gateway
    assert config.craigslist_fallback_exits() == ["us"] and config.craigslist_fallback_proxy() == RESI
    monkeypatch.setattr(config, "CRAIGSLIST_FALLBACK_PROXY_COUNTRY", None)
    assert config.craigslist_fallback_exits() == [] and config.craigslist_fallback_proxy() is None
    monkeypatch.setattr(config, "CRAIGSLIST_PROXY_COUNTRY", "gateway")
    assert config.craigslist_proxy() == "gateway"
    monkeypatch.setattr(config, "CRAIGSLIST_PROXY_COUNTRY", "gb")
    assert config.craigslist_proxy() == "http://resi-gb:1"


@hosted_only
def test_healthy_calls_stay_on_the_own_ip(ladder):
    plan, seen = ladder
    assert call() == OK and F.rapi("/locations", {"lat": 1, "lon": 2}) == OK and city() == ["bike rack"]
    assert seen == [None, None, None]


@hosted_only
def test_a_refused_own_ip_costs_gateway_requests_not_residential(ladder, monkeypatch):
    plan, seen = ladder
    plan[None] = 403
    assert call() == OK
    assert seen == [None, "gateway"]
    assert call() == OK and call() == OK                                     # the cooldown: the gateway
    assert seen == [None, "gateway", "gateway", "gateway"] and F._fallback_step == 1
    monkeypatch.setattr(F, "_fallback_until", 0.0)                      # cooldown over: the own IP again
    plan[None] = 200
    call()
    assert seen[-1] is None


@hosted_only
def test_a_refused_gateway_steps_on_to_residential_and_stays(ladder):
    plan, seen = ladder
    plan.update({None: 429, "gateway": 403})
    assert call() == OK
    assert seen == [None, "gateway", RESI]
    call()
    assert seen == [None, "gateway", RESI, RESI] and F._fallback_step == 2
    plan[RESI] = [403, 200]                                             # refused on the last step: a fresh exit of that step
    call()
    assert seen[-2:] == [RESI, RESI] and F._fallback_step == 2


@hosted_only
def test_last_retry_of_a_call_the_gateway_keeps_failing_rides_residential(ladder):
    plan, seen = ladder
    plan[None] = 403
    call()                                                      # the sessions are on the gateway step now
    del seen[:]
    plan["gateway"] = [RuntimeError("GatewayError: gateway 504"), 502, 200]
    assert call() == OK
    assert seen == ["gateway", "gateway", RESI]
    assert call() == OK                                     # for that call only: no cooldown on the paid exit
    assert seen == ["gateway", "gateway", RESI, "gateway"] and F._fallback_step == 1
    plan.update({"gateway": [RuntimeError("GatewayError: gateway 504"), 502, 200], RESI: 403})
    with pytest.raises(F.CraigslistBlocked):
        call()                                                  # the borrowed exit refused: no verdict on the gateway
    assert F._fallback_step == 1 and call() == OK and seen[-1] == "gateway"


@hosted_only
def test_without_the_gateway_step_a_refusal_goes_straight_to_residential(ladder, monkeypatch):
    plan, seen = ladder
    monkeypatch.setattr(config, "CRAIGSLIST_FALLBACK_EXIT", None)
    plan.update({None: 403, RESI: [RuntimeError("reset"), 200]})
    assert call() == OK
    assert seen == [None, RESI, RESI]                                   # the ladder it always had
    monkeypatch.setattr(config, "CRAIGSLIST_FALLBACK_PROXY_COUNTRY", None)
    monkeypatch.setattr(F, "_fallback_until", 0.0)
    F._drop_session()
    del seen[:]
    plan[None] = [403, 200]
    assert call() == OK                                     # no fallback at all: just a rebuilt session
    assert seen == [None, None] and F._step() == 0


@hosted_only
def test_a_pinned_exit_still_carries_every_call(ladder, monkeypatch):
    plan, seen = ladder
    monkeypatch.setattr(config, "CRAIGSLIST_PROXY_COUNTRY", "gateway")           # how the gateway was measured
    plan["gateway"] = [403, 200]
    assert call() == OK and seen == ["gateway", "gateway"]
    F._drop_session()
    del seen[:]
    monkeypatch.setattr(config, "CRAIGSLIST_PROXY_COUNTRY", "gb")
    plan["http://resi-gb:1"] = [403, 200]
    assert call() == OK and seen == ["http://resi-gb:1", "http://resi-gb:1"]


def test_only_the_api_hosts_may_ride_the_gateway():
    assert F.GATEWAY_HOSTS == {"sapi.craigslist.org", "rapi.craigslist.org", "reference.craigslist.org"}


@hosted_only
def test_a_city_host_never_rides_the_gateway(ladder, monkeypatch):
    plan, seen = ladder
    plan[None] = 403
    call()                                                              # the sessions are on the gateway step now
    del HOSTS[:]
    assert call() == OK and city() == ["bike rack"] and call() == OK
    assert F.redirect_target(CITY + "/cto/7978137819.html").endswith("/abcdefghijklmnopqrstuv")
    assert HOSTS == [("gateway", "sapi.craigslist.org"), (RESI, "newyork.craigslist.org"), ("gateway", "sapi.craigslist.org"),
                     (RESI, "newyork.craigslist.org")]                  # the exit behind the gateway, as before
    # that exit refused by the city host: a fresh one for the retry, the API hosts stay on the gateway
    del HOSTS[:]
    plan[RESI] = [403, 200]
    assert city() == ["bike rack"] and call() == OK
    assert HOSTS == [(RESI, "newyork.craigslist.org"), (RESI, "newyork.craigslist.org"), ("gateway", "sapi.craigslist.org")]
    assert F._fallback_step == 1
    monkeypatch.setattr(config, "CRAIGSLIST_FALLBACK_PROXY_COUNTRY", None)   # nothing behind the gateway: the own IP
    F._drop_session()
    del HOSTS[:]
    plan[None] = 200
    assert call() == OK and city() == ["bike rack"]
    assert HOSTS == [("gateway", "sapi.craigslist.org"), (None, "newyork.craigslist.org")]
    monkeypatch.setattr(config, "CRAIGSLIST_PROXY_COUNTRY", "gateway")      # pinned to the gateway: still not the city hosts
    monkeypatch.setattr(F, "_fallback_until", 0.0)
    F._drop_session()
    del HOSTS[:]
    assert call() == OK and city() == ["bike rack"]
    assert HOSTS == [("gateway", "sapi.craigslist.org"), (None, "newyork.craigslist.org")]


@hosted_only
def test_a_city_host_refusing_the_own_ip_moves_the_sessions_one_step(ladder):
    plan, seen = ladder
    plan[None] = 403
    assert city() == ["bike rack"]
    assert HOSTS == [(None, "newyork.craigslist.org"), (RESI, "newyork.craigslist.org")] and F._fallback_step == 1
    assert call() == OK and HOSTS[-1] == ("gateway", "sapi.craigslist.org")
