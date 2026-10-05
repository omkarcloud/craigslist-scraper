"""Offline parser / resolver / schema tests over captured payloads
(craigslist/fixtures, 2026-10-05). No network: the live site table is
replaced by a two-row stub.

    python -m pytest craigslist/test_parsers.py -q
"""
import json
import os

import pytest

os.environ.setdefault("ONLY_SCRAPER", "craigslist")

from craigslist import parsers as P          # noqa: E402
from craigslist import refdata, refs, schemas, search   # noqa: E402
from schema_fields import load_query         # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

SITES = [
    {"id": 3, "hostname": "newyork", "abbreviation": "nyc", "name": "new york city", "short_name": "new york",
     "region": "NY", "country": "US", "latitude": 40.714199, "longitude": -74.006401,
     "timezone": "America/New_York", "link": "https://newyork.craigslist.org",
     "subareas": [{"id": 1, "code": "mnh", "name": "manhattan"}, {"id": 2, "code": "brk", "name": "brooklyn"}]},
    {"id": 9, "hostname": "portland", "abbreviation": "pdx", "name": "portland, OR", "short_name": "portland",
     "region": "OR", "country": "US", "latitude": 45.5236, "longitude": -122.675,
     "timezone": "America/Los_Angeles", "link": "https://portland.craigslist.org", "subareas": []},
    {"id": 282, "hostname": "maine", "abbreviation": "mne", "name": "maine", "short_name": "maine",
     "region": "ME", "country": "US", "latitude": 43.6614, "longitude": -70.2558,
     "timezone": "America/New_York", "link": "https://maine.craigslist.org", "subareas": []},
]


@pytest.fixture(autouse=True)
def _stub_sites(monkeypatch):
    table = {
        "list": SITES,
        "by_id": {r["id"]: r for r in SITES},
        "by_hostname": {r["hostname"]: r for r in SITES},
        "by_abbreviation": {r["abbreviation"]: r for r in SITES},
        "by_name": {refdata._norm(r["name"]): r for r in SITES},
    }
    monkeypatch.setattr(refdata, "sites", lambda: table)


def load(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def rows_of(name):
    data = load(name)
    decode = data["decode"]
    return data, [P.decode_full_row(row, decode) for row in data["items"]]


# ---- search rows -------------------------------------------------------------------

def test_vehicle_rows():
    data, rows = rows_of("search_cta")
    assert all(rows)
    listing = P.listing_summary(rows[0], areas=data["areas"])
    assert listing["id"] and listing["post_id"] > 7_000_000_000
    assert listing["link"].startswith("https://www.craigslist.org/view/d/")
    assert listing["link"].endswith(listing["id"])
    assert listing["currency"] == "USD" and isinstance(listing["price"], int)
    assert listing["posted_at"].endswith("Z") and listing["posted_at"][:2] == "20"
    assert listing["category"]["section"] == "for_sale" and listing["category"]["code"] in ("cto", "ctd")
    assert set(listing["details"]) == {"odometer", "monthly_payment", "sale_dates"}
    assert listing["location"]["site"] == "newyork" and listing["location"]["latitude"]
    assert listing["image_count"] == len(listing["images"])
    if listing["images"]:
        assert listing["images"][0].endswith("_1200x900.jpg")
        assert listing["thumbnail_link"].endswith("_300x300.jpg")
    assert any(P.listing_summary(r, areas=data["areas"])["details"]["odometer"] for r in rows)


def test_housing_rows():
    data, rows = rows_of("search_apa")
    listings = [P.listing_summary(r, areas=data["areas"]) for r in rows]
    assert all(l["category"]["section"] == "housing" for l in listings)
    assert any(l["details"]["bedrooms"] is not None for l in listings)
    for l in listings:
        assert set(l["details"]) == {"bedrooms", "area", "area_unit", "open_house_dates"}
        if l["details"]["area"] is not None:
            assert l["details"]["area_unit"] == "sqft"


def test_open_house_dates():
    data, rows = rows_of("search_rea")
    dates = [d for r in rows for d in P.listing_summary(r, areas=data["areas"])["details"]["open_house_dates"]]
    assert dates and all(len(d) == 10 and d.startswith("20") for d in dates)


def test_job_rows():
    data, rows = rows_of("search_jjj")
    listings = [P.listing_summary(r, areas=data["areas"]) for r in rows]
    assert all(l["price"] is None and l["currency"] is None for l in listings)
    assert any(l["details"]["compensation"] for l in listings)
    assert all(set(l["details"]) == {"job_title", "company_name", "compensation"} for l in listings)


def test_event_rows():
    data, rows = rows_of("search_eee")
    listings = [P.listing_summary(r, areas=data["areas"]) for r in rows]
    assert all(l["details"]["start_date"] <= l["details"]["end_date"] for l in listings)


def test_nearby_rows_and_distance():
    data = load("search_nearby")
    decode = data["decode"]
    rows = [P.decode_full_row(row, decode) for row in data["items"]]
    nearby_id = data["firstNearbyResultId"]
    index = next(i for i, r in enumerate(rows) if r["post_id"] == nearby_id)
    assert index == 2
    sites = [r["location"]["hostname"] for r in rows]
    assert sites[0] == "swva" and sites[-1] != "swva"
    listing = P.listing_summary(rows[-1], areas=data["areas"], center=(36.8928, -82.0843, "mi"), nearby_flag=True)
    assert listing["is_nearby_result"] is True
    assert listing["location"]["distance"] > 0 and listing["location"]["distance_unit"] == "mi"


def test_digest_plus_batch():
    digest, batch = load("digest_cta"), load("batch_cta")
    decode = digest["decode"]
    rows = [P.decode_digest(row, decode) for row in digest["items"]]
    details = P.decode_batch(batch, decode.get("minDate"))
    assert len(details) == len(rows) == 6
    for row in rows:
        assert "title" not in row
        row.update(details[row["post_id"]])
        listing = P.listing_summary(row, areas=digest["areas"])
        assert listing["title"] and listing["id"] and listing["link"]


def test_rows_survive_garbage():
    assert P.decode_full_row(None, {}) is None
    assert P.decode_full_row([1, 2], {"minPostingId": 5}) is None
    assert P.decode_full_row(["x", 0, 1, -1, "0:0~a~b"], {"minPostingId": 5}) is None
    row = P.decode_full_row([1, 2, 99999, -1, "9:9:9~x~y", 0, [4], [5], [13], "title", -3, [99, "?"]],
                            {"minPostingId": 10, "minPostedDate": 1790000000})
    listing = P.listing_summary(row)
    assert listing["post_id"] == 11 and listing["title"] == "title"
    assert listing["price"] is None and listing["id"] is None and listing["link"] is None
    assert listing["category"] == {"id": 99999, "code": None, "name": None, "section": None}
    assert listing["details"] is None and listing["images"] == []
    assert P.decode_batch(None) == {} and P.decode_batch({"batch": [[1], "x", None]}) == {}
    assert P.search_location(None) is None


# ---- listing details -----------------------------------------------------------------

def test_vehicle_listing():
    listing = P.listing_details(load("posting_cta"))
    assert listing["id"] and listing["post_id"] and listing["title"]
    assert listing["link"].startswith("https://www.craigslist.org/view/")
    assert listing["currency"] == "USD"
    assert listing["category"]["code"] in ("cto", "ctd") and listing["category"]["section"] == "for_sale"
    attrs = listing["attributes"]
    assert isinstance(attrs.get("year"), int) and attrs.get("make_model")
    assert "auto_year" not in attrs and "forSearch" not in json.dumps(listing)
    assert listing["location"]["site"] == "newyork" and listing["location"]["country"] == "US"
    assert listing["description"] is None or "<" not in listing["description"]
    assert listing["images"] == [] or listing["images"][0].startswith("https://images.craigslist.org/")


def test_housing_listing():
    listing = P.listing_details(load("posting_apa"))
    attrs = listing["attributes"]
    assert listing["category"]["section"] == "housing"
    assert isinstance(attrs.get("bedrooms"), int)
    for key, value in attrs.items():
        if key.startswith(("is_", "has_", "are_")):
            assert isinstance(value, bool)


def test_job_and_event_listing():
    job = P.listing_details(load("posting_jjj"))
    assert job["price"] is None and job["currency"] is None and job["category"]["section"] == "jobs"
    event = P.listing_details(load("posting_eee"))
    assert event["attributes"]["start_date"] <= event["attributes"]["end_date"]


def test_listing_survives_garbage():
    assert P.listing_details(None) is None
    listing = P.listing_details({})
    assert listing["id"] is None and listing["attributes"] == {} and listing["images"] == []
    assert listing["location"]["latitude"] is None and listing["has_contact_info"] is False
    listing = P.listing_details({"attributes": "x", "location": 5, "images": None, "price": "12", "body": 7,
                                 "notices": [None, {"text": " sold "}], "streetAddress": 0, "updatedDate": "x"})
    assert listing["price"] is None and listing["notices"] == ["sold"] and listing["updated_at"] is None


def test_attributes():
    attrs = P.attributes([
        {"postingAttributeKey": "area", "value": "1250 ft²", "specialType": "area"},
        {"postingAttributeKey": "pets_cat", "value": "✓", "specialType": ""},
        {"postingAttributeKey": "private_bath", "value": "no private bath", "specialType": "value"},
        {"postingAttributeKey": "private_room", "value": "private room", "specialType": "value"},
        {"postingAttributeKey": "date_range", "value": "2026-10-31,2026-11-13", "specialType": "dateRange"},
        {"postingAttributeKey": "dates", "value": "2026-09-24,bad,2026-10-06", "specialType": "dateList"},
        {"postingAttributeKey": "auto_miles", "value": "154,000", "specialType": "key_value"},
        {"postingAttributeKey": "bathrooms", "value": "shared", "specialType": "bathrooms"},
        {"postingAttributeKey": "brand_new_key", "value": " x ", "specialType": "key_value"},
        {"value": "no key"}, None,
    ])
    assert attrs["area"] == 1250 and attrs["area_unit"] == "sqft"
    assert attrs["are_cats_allowed"] is True
    assert attrs["has_private_bath"] is False and attrs["has_private_room"] is True
    assert attrs["start_date"] == "2026-10-31" and attrs["end_date"] == "2026-11-13"
    assert attrs["dates"] == ["2026-09-24", "2026-10-06"]
    assert attrs["odometer"] == 154000 and attrs["bathrooms"] == "shared"
    assert attrs["brand_new_key"] == "x"


def test_html_to_text():
    body = 'Runs great <br>\n<br>\nCall <showcontactinfo postingid="1" title="x"></showcontactinfo>\n<br>\n<b>A&amp;B</b>'
    assert P.html_to_text(body) == "Runs great\n\nCall\nA&B"
    assert P.html_to_text("") is None and P.html_to_text(None) is None


def test_counts():
    sections = P.category_counts(load("counts")["items"])
    assert sections[0]["count"] >= sections[-1]["count"]
    sale = next(s for s in sections if s["code"] == "sss")
    assert sale["section"] == "for_sale" and sale["categories"][0]["count"] >= sale["categories"][-1]["count"]
    assert P.category_counts(None) == []


def test_filter_definitions():
    rows = P.filter_definitions(load("search_cta")["filters"], search.filter_index())
    by_param = {r["param"]: r for r in rows}
    assert not [r for r in rows if not r["is_supported"]]
    assert [o["value"] for o in by_param["transmission"]["options"]] == ["manual", "automatic", "other"]
    assert by_param["min_year"]["type"] == "number" and by_param["has_image"]["type"] == "boolean"
    # every option the filters route reports is a value the search schema accepts
    fields = schemas.VehicleSearchSchema().fields
    for row in rows:
        allowed = getattr(fields.get(row["param"]), "value_map", None)
        if allowed:
            assert {o["value"] for o in row["options"]} <= set(allowed), row["param"]
    assert P.filter_definitions(None, {}) == []


# ---- refs ----------------------------------------------------------------------------

def test_resolve_location():
    assert refs.resolve_location("NewYork") == {"kind": "area", "value": "newyork"}
    assert refs.resolve_location(" new   york ") == {"kind": "area", "value": "new york"}
    assert refs.resolve_location("https://sfbay.craigslist.org/search/sss?query=x") == {"kind": "area", "value": "sfbay"}
    assert refs.resolve_location("https://www.craigslist.org/search/area/newyork?cat=sss") == {"kind": "area", "value": "newyork"}
    assert refs.resolve_location("94103") == {"kind": "postal", "value": "94103"}
    assert refs.resolve_location("m5v 2t6") == {"kind": "postal", "value": "M5V2T6"}
    assert refs.resolve_location("40.7128, -74.0060") == {"kind": "coordinates", "latitude": 40.7128, "longitude": -74.006}
    for bad in ("", "https://example.com", "https://www.craigslist.org/about", "95.0,10", "9!"):
        with pytest.raises(ValueError):
            refs.resolve_location(bad)


def test_resolve_listing():
    assert refs.resolve_listing("btYtLiemfZzTX5bosg3gfp") == {"uuid": "btYtLiemfZzTX5bosg3gfp"}
    assert refs.resolve_listing("https://www.craigslist.org/view/d/hollis-2011-buick/btYtLiemfZzTX5bosg3gfp?x=1") == {"uuid": "btYtLiemfZzTX5bosg3gfp"}
    assert refs.resolve_listing("https://www.craigslist.org/view/btYtLiemfZzTX5bosg3gfp") == {"uuid": "btYtLiemfZzTX5bosg3gfp"}
    # paid / dealer posts carry base64url ids
    assert refs.resolve_listing("NNmhMYu-8RGFAJs7atcCJw") == {"uuid": "NNmhMYu-8RGFAJs7atcCJw"}
    assert refs.resolve_listing("https://www.craigslist.org/view/d/x-y/-of_T5W-8RGz7J-jPlCPEg") == {"uuid": "-of_T5W-8RGz7J-jPlCPEg"}
    assert refs.resolve_listing("https://newyork.craigslist.org/que/cto/d/hollis-2011-buick/7978137819.html") == {
        "hostname": "newyork", "subarea": "que", "category": "cto", "post_id": 7978137819}
    assert refs.resolve_listing("newyork.craigslist.org/cto/7978137819.html") == {
        "hostname": "newyork", "subarea": None, "category": "cto", "post_id": 7978137819}
    for bad in ("", "7978137819", "abc", "https://example.com/cto/7978137819.html",
                "https://newyork.craigslist.org/search/sss", "https://newyork.craigslist.org/7978137819.html"):
        with pytest.raises(ValueError):
            refs.resolve_listing(bad)
    assert len(refs.resolve_listings("btYtLiemfZzTX5bosg3gfp, btYtLiemfZzTX5bosg3gfp,1RqhUHbcUXQbvrUiTUo8Fb")) == 2
    with pytest.raises(ValueError):
        refs.resolve_listings(",".join(["btYtLiemfZzTX5bosg3gf%s" % c for c in "abcdefghijklmnopqrstu"]))


def test_image_and_view_links():
    assert refs.image_link("3:00C0C_7py8tcpPuyA_0CI0t2") == "https://images.craigslist.org/00C0C_7py8tcpPuyA_0CI0t2_1200x900.jpg"
    assert refs.image_link("0:00C0C_x_0CI0t2").endswith("_600x450.jpg")
    assert refs.image_link(None) is None and refs.image_link("3:") is None
    assert refs.view_link("a-b", "UUID") == "https://www.craigslist.org/view/d/a-b/UUID"
    assert refs.view_link(None, "UUID") == "https://www.craigslist.org/view/UUID" and refs.view_link("a", None) is None


# ---- refdata -------------------------------------------------------------------------

def test_categories():
    assert refdata.category("cto")["parent"] == "cta" and refdata.category("cto")["section"] == "for_sale"
    assert refdata.category_by_id(145)["code"] == "cto"
    assert refdata.resolve_category("CTA") == "cta"
    assert refdata.resolve_category("cars & trucks", "for_sale") == "cta"
    with pytest.raises(ValueError):
        refdata.resolve_category("apa", "jobs")
    with pytest.raises(ValueError):
        refdata.resolve_category("zzz")
    tree = refdata.category_tree()
    assert len(tree) == 8 and sum(s["category_count"] for s in tree) > 120
    sale = refdata.category_tree("for_sale")[0]
    cars = next(c for c in sale["categories"] if c["code"] == "cta")
    assert cars["by_owner"]["code"] == "cto" and cars["by_dealer"]["code"] == "ctd"


def test_resolve_site():
    assert refdata.resolve_site("newyork")["id"] == 3
    assert refdata.resolve_site("nyc")["id"] == 3
    assert refdata.resolve_site("new york")["id"] == 3
    assert refdata.resolve_site("New York City")["id"] == 3
    assert refdata.resolve_site("port")["id"] == 9          # one prefix match
    with pytest.raises(ValueError):
        refdata.resolve_site("atlantis")
    with pytest.raises(ValueError):
        refdata.resolve_subarea(refdata.resolve_site("newyork"), "zzz")
    assert refdata.resolve_subarea(refdata.resolve_site("newyork"), "brk")["name"] == "brooklyn"
    assert refdata.nearest_sites(40.7, -74.0, limit=2)[0][0]["hostname"] == "newyork"


# ---- schemas -------------------------------------------------------------------------

def test_search_schema():
    data, error = load_query(schemas.VehicleSearchSchema, {
        "location": "newyork", "vehicle_type": "motorcycles", "transmission": "Manual, automatic",
        "min_year": "2015", "has_image": "true", "page": "2"})
    assert error is None
    assert data["category"] == "mca" and "vehicle_type" not in data
    assert data["transmission"] == ["1", "2"] and data["min_year"] == 2015 and data["has_image"] is True
    params = search._upstream_filters(data["category"], {k: v for k, v in data.items()
                                                         if k not in ("location", "category", "page", "query",
                                                                      "subarea", "radius", "sort")})
    assert params == {"auto_transmission": ["1", "2"], "min_auto_year": 2015, "hasPic": 1}

    for bad in ({"query": "x"},                                             # location is required
                {"location": "newyork", "radius": "5"},                      # radius needs a point
                {"location": "94103", "subarea": "brk"},                     # subarea needs a site
                {"location": "newyork", "min_year": "2020", "max_year": "2010"},
                {"location": "newyork", "sort": "relevance"},                # relevance needs a query
                {"location": "newyork", "transmission": "cvt"},
                {"location": "newyork", "unknown": "1"},
                {"location": "newyork", "page": "85"}):
        assert load_query(schemas.VehicleSearchSchema, bad)[1] is not None, bad
    assert load_query(schemas.JobSearchSchema, {"location": "newyork", "category": "apa"})[1] is not None
    data, error = load_query(schemas.EventSearchSchema, {"location": "40.71,-74.0", "event_type": "music,free"})
    assert error is None and data["event_type"] == ["event_music", "event_free"]
    assert search._upstream_filters("eee", {"event_type": data["event_type"]}) == {"event_music": 1, "event_free": 1}
    assert search._upstream_filters("boo", {"min_year": 2010, "min_length": 20.0}) == {
        "min_year_manufactured": 2010, "min_boat_length_overall": 20}
    assert search._upstream_filters("tra", {"max_year": 2020}) == {"max_year_manufactured": 2020}
    assert search._upstream_filters("ggg", {"is_paid": False}) == {"is_paid": "no"}
    # a point no site covers comes back re-homed on the prefix site's centre
    point = {"kind": "coordinates", "latitude": 40.7128, "longitude": -74.006}
    search._check_place(point, {"location": {"lat": 40.7128, "lon": -74.006}})
    with pytest.raises(ValueError):
        search._check_place(dict(point, latitude=0.0, longitude=0.0), {"location": {"lat": 37.5, "lon": -122.25}})
    with pytest.raises(ValueError):
        search._upstream_filters("cta", {"min_length": 20})


def test_listing_and_helper_schemas():
    assert load_query(schemas.ListingSchema, {"listing": "7978137819"})[1] is not None
    assert load_query(schemas.ListingSchema, {})[1] is not None
    assert load_query(schemas.ListingBatchSchema, {"listings": "btYtLiemfZzTX5bosg3gfp"})[0]["listings"] == [
        {"uuid": "btYtLiemfZzTX5bosg3gfp"}]
    assert load_query(schemas.SuggestionsSchema, {"query": "hon", "type": "make_model"})[1] is not None
    data, error = load_query(schemas.SuggestionsSchema, {"query": "hon", "type": "make_model", "location": "sfbay"})
    assert error is None and data["type"] == "makemodel"
    assert load_query(schemas.NearbyLocationsSchema, {"location": "94103"})[1] is not None
    assert load_query(schemas.LocationsSchema, {"country": "us"})[0]["country"] == "US"
    assert load_query(schemas.CategoriesSchema, {"section": "pets"})[1] is not None
