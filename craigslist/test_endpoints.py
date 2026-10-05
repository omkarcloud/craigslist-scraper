"""Live endpoint smoke tests: one call per /craigslist/* route against a
running service, with example values proven to return data (2026-10-05).
The listing tooling reads each route's FIRST call from this file's AST as
its working example, so the values stay literals.

Skipped unless CRAIGSLIST_BASE points at a running service:

    ONLY_SCRAPER=craigslist python run.py
    CRAIGSLIST_BASE=http://127.0.0.1:6002 python -m pytest craigslist/test_endpoints.py -q
"""
import os

import pytest

BASE = os.environ.get("CRAIGSLIST_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set CRAIGSLIST_BASE to run live endpoint tests")


def call(path, status=200, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=180)
    assert resp.status_code == status, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


def _a_listing(**params):
    return call("/craigslist/search", location="newyork", **params)["listings"][0]


def test_search():
    body = call("/craigslist/search", location="newyork", query="laptop")
    assert body["count"] > 100 and body["per_page"] == 120 and len(body["listings"]) == 120
    assert body["next"] and "page=2" in body["next"] and body["previous"] is None
    first = body["listings"][0]
    assert first["id"] and first["post_id"] and first["title"] and first["link"].endswith(first["id"])
    assert body["location"]["site"] == "newyork" and body["location"]["currency"] == "USD"
    deep = call("/craigslist/search", location="newyork", query="laptop", page=5)
    assert deep["current_page"] == 5 and deep["listings"] and deep["listings"][0]["title"]
    assert not {l["id"] for l in deep["listings"]} & {l["id"] for l in body["listings"]}
    postal = call("/craigslist/search", location="94103", radius=5, query="bike", sort="distance")
    assert postal["location"]["postal_code"] == "94103" and postal["sort"] == "distance"
    distances = [l["location"]["distance"] for l in postal["listings"] if l["location"]["distance"] is not None]
    assert distances and distances[0] <= distances[-1]
    coords = call("/craigslist/search", location="51.5,-0.12", radius=10)
    assert coords["location"]["country"] == "GB" and coords["location"]["currency"] == "GBP"
    borough = call("/craigslist/search", location="https://newyork.craigslist.org", subarea="brk", query="sofa")
    assert borough["location"]["name"] == "brooklyn"
    small = call("/craigslist/search", location="swva", query="bike")
    assert any(l["is_nearby_result"] for l in small["listings"]) and small["local_count"] < small["count"]
    call("/craigslist/search", status=400, location="00000")
    call("/craigslist/search", status=400, location="newyork", subarea="zzz")
    call("/craigslist/search", status=400, location="atlantis")
    call("/craigslist/search", status=400, query="laptop")


def test_for_sale():
    body = call("/craigslist/for-sale/search", location="newyork", query="iphone", min_price=100, max_price=400,
                condition="new,like_new", seller_type="owner", sort="price_low")
    prices = [l["price"] for l in body["listings"] if not l["is_nearby_result"]]
    assert prices and min(prices) >= 100 and max(prices) <= 400 and prices == sorted(prices)
    assert all(l["category"]["section"] == "for_sale" for l in body["listings"])
    call("/craigslist/for-sale/search", status=400, location="newyork", category="apa")


def test_vehicles():
    body = call("/craigslist/vehicles/search", location="sfbay", make_model="honda civic", min_year=2015,
                max_odometer=90000, transmission="automatic", title_status="clean")
    assert body["category"]["code"] == "cta" and body["listings"]
    assert any(l["details"]["odometer"] for l in body["listings"])
    bikes = call("/craigslist/vehicles/search", location="losangeles", vehicle_type="motorcycles", min_engine_cc=600)
    assert bikes["category"]["code"] == "mca" and bikes["listings"]
    boats = call("/craigslist/vehicles/search", location="miami", vehicle_type="boats", min_length=20)
    assert boats["category"]["code"] == "boo"
    call("/craigslist/vehicles/search", status=400, location="miami", min_length=20)


def test_housing():
    body = call("/craigslist/housing/search", location="chicago", category="apa", min_bedrooms=2, max_price=2500,
                cats_allowed="true", laundry="in_unit,in_building")
    local = [l for l in body["listings"] if not l["is_nearby_result"]]
    assert local and all(l["price"] <= 2500 for l in local if l["price"] is not None)
    assert all(l["details"]["bedrooms"] >= 2 for l in local if l["details"]["bedrooms"] is not None)
    upcoming = call("/craigslist/housing/search", location="newyork", category="rea", sort="upcoming")
    assert any(l["details"]["open_house_dates"] for l in upcoming["listings"])


def test_jobs_gigs_services():
    jobs = call("/craigslist/jobs/search", location="seattle", query="driver", employment_type="full_time")
    assert jobs["listings"] and all(l["category"]["section"] == "jobs" for l in jobs["listings"])
    assert any(l["details"]["compensation"] for l in jobs["listings"])
    gigs = call("/craigslist/gigs/search", location="austin", is_paid="true")
    assert gigs["listings"] and gigs["category"]["code"] == "ggg"
    services = call("/craigslist/services/search", location="boston", query="plumber")
    assert services["listings"] and services["listings"][0]["details"] is None


def test_community_events_resumes():
    community = call("/craigslist/community/search", location="denver", category="laf", lost_or_found="lost")
    assert community["listings"] and community["category"]["code"] == "laf"
    events = call("/craigslist/events/search", location="newyork", event_type="music")
    assert events["listings"] and events["listings"][0]["details"]["start_date"]
    resumes = call("/craigslist/resumes/search", location="newyork", education_level="bachelors,masters")
    assert resumes["listings"] and resumes["category"]["code"] == "rrr"


def test_search_helpers():
    filters = call("/craigslist/search/filters", category="cta", location="newyork")
    assert filters["search_route"] == "/craigslist/vehicles/search" and filters["subareas"]
    params = {f["param"] for f in filters["filters"]}
    assert {"transmission", "min_year", "make_model", "has_image"} <= params
    assert all(f["is_supported"] for f in filters["filters"])
    assert call("/craigslist/search/filters", category="apa")["search_route"] == "/craigslist/housing/search"
    assert "laptop" in call("/craigslist/search/suggestions", query="lap")["suggestions"]
    models = call("/craigslist/search/suggestions", query="hon", type="make_model", location="sfbay")
    assert any("honda" in s for s in models["suggestions"])
    counts = call("/craigslist/search/counts", query="bike", location="newyork")
    assert counts["total_count"] > 100 and counts["sections"][0]["categories"][0]["count"] > 0
    call("/craigslist/search/suggestions", status=400, query="hon", type="make_model")


def test_listings():
    found = _a_listing(category="cta")
    body = call("/craigslist/listings/details", listing=found["id"])["listing"]
    assert body["id"] == found["id"] and body["post_id"] == found["post_id"] and body["title"]
    assert body["category"]["section"] == "for_sale" and body["location"]["site"] == "newyork"
    assert isinstance(body["attributes"], dict) and body["posted_at"].endswith("Z")
    by_link = call("/craigslist/listings/details", listing=found["link"])["listing"]
    assert by_link["post_id"] == found["post_id"]
    short = f"https://newyork.craigslist.org/{body['category']['code']}/{body['post_id']}.html"
    assert call("/craigslist/listings/details", listing=short)["listing"]["id"] == found["id"]
    batch = call("/craigslist/listings/batch", listings=f"{found['id']},aaaaaaaaaaaaaaaaaaaaaa")
    assert batch["count"] == 1 and batch["requested_count"] == 2 and len(batch["not_found"]) == 1
    call("/craigslist/listings/details", status=404, listing="aaaaaaaaaaaaaaaaaaaaaa")
    call("/craigslist/listings/details", status=400, listing="7978137819")
    call("/craigslist/listings/details", status=400, listing="https://example.com/x")


def test_locations_and_categories():
    everything = call("/craigslist/locations")
    assert everything["count"] > 600
    ontario = call("/craigslist/locations", country="CA", region="ON")
    assert ontario["count"] >= 10 and all(l["country"] == "CA" for l in ontario["locations"])
    found = call("/craigslist/locations/search", query="new york")
    assert found["locations"][0]["site"] == "newyork" and found["locations"][0]["subareas"]
    near = call("/craigslist/locations/nearby", location="34.05,-118.24", limit=3)
    assert near["locations"][0]["site"] == "losangeles" and near["place"]["postal_code"]
    assert call("/craigslist/locations/nearby", location="newyork", limit=3)["locations"][0]["site"] != "newyork"
    tree = call("/craigslist/categories")
    assert tree["section_count"] == 8 and tree["category_count"] > 120
    assert call("/craigslist/categories", section="housing")["sections"][0]["code"] == "hhh"
    call("/craigslist/locations/nearby", status=400, location="94103")
