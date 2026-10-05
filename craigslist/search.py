"""/craigslist/*/search and the search helpers (filters, suggestions,
counts).

One search call upstream answers the first 360 results in full, so pages
1-3 (120 per page) cost a single request. Deeper pages follow the site's
own two-step: the `full` call with batch size 0 returns every matching
digest (id, date, category, price, place — up to 10,000) plus a `cacheId`,
and the `batch` call fills in titles / images / tags for one 360-result
chunk. The digest list is memoised in-process for a few minutes so paging
through a result set costs one request per page after the first.

Results past the local matches are Craigslist's "more from nearby areas"
rows (`is_nearby_result`); `local_count` is how many matched in the
searched location itself.
"""
import threading
import time

from craigslist import parsers as P
from craigslist import refdata, refs, schemas
from craigslist.fetch import CraigslistBadRequest, get_json, sapi

DEFAULT_RADIUS = 30
DIGEST_TTL = 240
DIGEST_MEMO_SIZE = 48
# Any real site id works as the batch prefix of a postal / coordinate
# search: the API re-homes the search on the site that owns the point.
GEO_PREFIX = 1

# Public param -> upstream param, for filters whose value passes through
# (numbers, text, or ids already mapped by the schema).
VALUE_PARAMS = {
    "min_price": "min_price", "max_price": "max_price",
    "seller_type": "purveyor",
    "condition": "condition",
    "make_model": "auto_make_model",
    "min_year": "min_auto_year", "max_year": "max_auto_year",
    "min_odometer": "min_auto_miles", "max_odometer": "max_auto_miles",
    "min_monthly_payment": "min_monthly_payment", "max_monthly_payment": "max_monthly_payment",
    "transmission": "auto_transmission",
    "fuel_type": "auto_fuel_type",
    "drivetrain": "auto_drivetrain",
    "body_type": "auto_bodytype",
    "title_status": "auto_title_status",
    "paint_color": "auto_paint",
    "cylinders": "auto_cylinders",
    "min_engine_cc": "min_engine_displacement_cc", "max_engine_cc": "max_engine_displacement_cc",
    "motorcycle_type": "motorcycle_type",
    "motor_type": "motorcycle_motor_type",
    "language": "language",
    "boat_type": "boat_type",
    "propulsion_type": "boat_propulsion_type",
    "rv_type": "rv_type",
    "atv_type": "sna_type",
    "min_bedrooms": "min_bedrooms", "max_bedrooms": "max_bedrooms",
    "min_bathrooms": "min_bathrooms", "max_bathrooms": "max_bathrooms",
    "min_area": "minSqft", "max_area": "maxSqft",
    "housing_type": "housing_type",
    "laundry": "laundry",
    "parking": "parking",
    "rent_period": "rent_period",
    "employment_type": "employment_type",
    "education_level": "education_level_completed",
    "lost_or_found": "lost_and_found_type",
    "date": "sale_date",
    "open_house_date": "sale_date",
}
# Public flag -> upstream checkbox (sent as 1 when true).
FLAG_PARAMS = {
    "has_image": "hasPic",
    "posted_today": "postedToday",
    "hide_duplicates": "bundleDuplicates",
    "show_duplicates": "showDuplicates",
    "is_free": "free",
    "delivery_available": "delivery_available",
    "cryptocurrency_accepted": "crypto_currency_ok",
    "is_street_legal": "motorcycle_street_legal",
    "cats_allowed": "pets_cat",
    "dogs_allowed": "pets_dog",
    "is_furnished": "is_furnished",
    "no_smoking": "no_smoking",
    "wheelchair_accessible": "wheelchaccess",
    "air_conditioning": "airconditioning",
    "ev_charging": "ev_charging",
    "private_room": "private_room",
    "private_bath": "private_bath",
    "no_broker_fee": "broker_fee",
    "no_application_fee": "application_fee",
    "is_remote": "is_telecommuting",
    "is_internship": "is_internship",
    "is_nonprofit": "is_nonprofit",
}
# Year / length filters are spelled per vehicle category upstream.
_YEAR_PARAM = {"boo": "year_manufactured", "tra": "year_manufactured"}
_LENGTH_PARAM = {"boo": "boat_length_overall", "rva": "rv_length"}


# ---- upstream params -----------------------------------------------------------------

def _upstream_filters(category, filters):
    """Validated public filter kwargs -> the site's query params."""
    out = {}
    for name, value in filters.items():
        if name == "is_paid" and value is not None:
            out["is_paid"] = "yes" if value else "no"     # the one flag whose false is a filter
            continue
        if value is None or value is False or value == []:
            continue
        if name in ("min_year", "max_year") and category in _YEAR_PARAM:
            out[f"{name[:3]}_{_YEAR_PARAM[category]}"] = value
        elif name in ("min_length", "max_length"):
            target = _LENGTH_PARAM.get(category)
            if target is None:
                raise ValueError(f"{name} applies to vehicle_type=boats or rvs")
            out[f"{name[:3]}_{target}"] = value
        elif name in VALUE_PARAMS:
            out[VALUE_PARAMS[name]] = _plain(value)
        elif name in FLAG_PARAMS:
            out[FLAG_PARAMS[name]] = 1
        elif name == "title_only":
            out["srchType"] = "T"
        elif name == "availability" and isinstance(value, list):
            for flag in value:                 # resumes: one checkbox per slot
                out[flag] = 1
        elif name == "availability":
            out["availabilityMode"] = value    # housing: one select
        elif name == "event_type":
            for flag in value:
                out[flag] = 1
    return out


def _plain(value):
    """Whole floats travel as ints (min_price=500, not 500.0)."""
    if isinstance(value, float) and value == int(value):
        return int(value)
    return value


def _place(location, subarea, radius):
    """A resolved `location` spec -> (batch prefix, path prefix, geo params,
    site row or None)."""
    kind = location.get("kind")
    if kind == "area":
        site = refdata.resolve_site(location["value"])
        path = ""
        if subarea:
            refdata.resolve_subarea(site, subarea)
            path = subarea + "/"
        return site["id"], path, {}, site
    geo = {"search_distance": radius or DEFAULT_RADIUS}
    if kind == "postal":
        geo["postal"] = location["value"]
    else:
        geo["lat"], geo["lon"] = location["latitude"], location["longitude"]
    return GEO_PREFIX, "", geo, None


def _check_place(location, data):
    """A postal code Craigslist does not know (or a point no site covers)
    silently falls back to the prefix site: surface it as a 400 instead of
    another city's results."""
    resolved = data.get("location") if isinstance(data.get("location"), dict) else {}
    if location.get("kind") == "postal":
        if not P.text(resolved.get("postal")):
            raise ValueError(f"Craigslist does not recognise the postal code '{location['value']}'")
    elif location.get("kind") == "coordinates":
        # A covered point is echoed back (rounded to 4 decimals); the
        # fallback answers the prefix site's own centre instead.
        lat, lon = P.to_coordinate(resolved.get("lat")), P.to_coordinate(resolved.get("lon"))
        if (lat is None or lon is None or abs(lat - location["latitude"]) > 0.01
                or abs(lon - location["longitude"]) > 0.01):
            raise ValueError(f"no Craigslist site covers {location['latitude']},{location['longitude']}")


# ---- digest memo ---------------------------------------------------------------------

_digest_lock = threading.Lock()
_digests = {}


def _memo_key(prefix, path, params):
    return (prefix, path, tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in params.items())))


def _digest(prefix, path, params, sort_id):
    """Every matching digest row + the cacheId for batch calls."""
    key = _memo_key(prefix, path, params)
    now = time.time()
    with _digest_lock:
        hit = _digests.get(key)
        if hit and hit[0] > now:
            return hit[1]
    query = dict(params)
    query["batch"] = f"{prefix}-0-0-{sort_id}-0"
    query["searchPath"] = path
    data = sapi("/postings/search/full", query, label="search digest")
    with _digest_lock:
        if len(_digests) >= DIGEST_MEMO_SIZE:
            for stale in sorted(_digests, key=lambda k: _digests[k][0])[: DIGEST_MEMO_SIZE // 4]:
                _digests.pop(stale, None)
        _digests[key] = (now + DIGEST_TTL, data)
    return data


def _forget_digest(prefix, path, params):
    with _digest_lock:
        _digests.pop(_memo_key(prefix, path, params), None)


# ---- the search ------------------------------------------------------------------------

def _center(location, data, sort_token):
    """(lat, lon, unit) to measure distances from: only for radius searches
    and sort=distance, like the site."""
    resolved = data.get("location") if isinstance(data.get("location"), dict) else {}
    if location.get("kind") == "area" and sort_token != "dist":
        return None
    lat, lon = P.to_coordinate(resolved.get("lat")), P.to_coordinate(resolved.get("lon"))
    if lat is None or lon is None:
        return None
    meta = (data.get("areas") or {}).get(str(resolved.get("areaId"))) or {}
    return lat, lon, meta.get("distanceUnits") or "mi"


def _first_page_rows(data):
    decode = data.get("decode") if isinstance(data.get("decode"), dict) else {}
    rows = [r for r in (P.decode_full_row(row, decode) for row in data.get("items") or []) if r]
    return rows


def _deep_rows(prefix, path, params, data, offset):
    """One page of a digest list, detailed by the chunk's batch call."""
    decode = data.get("decode") if isinstance(data.get("decode"), dict) else {}
    digests = data.get("items") or []
    page_rows = [r for r in (P.decode_digest(row, decode) for row in digests[offset: offset + P.PER_PAGE]) if r]
    if not page_rows:
        return []
    start = offset // P.CHUNK * P.CHUNK
    sort_id = P.SORT_BATCH_IDS.get(data.get("detailsOrder"), 1)
    batch = "-".join(str(x) for x in (prefix, start, P.CHUNK, sort_id, data.get("bundleDups") or 0,
                                      data.get("maxPostedTs"), data.get("cacheTs")))
    details = P.decode_batch(sapi("/postings/search/batch", {"batch": batch, "cacheId": data.get("cacheId")},
                                  label="search batch"), decode.get("minDate"))
    for row in page_rows:
        row.update(details.get(row["post_id"]) or {})
    return page_rows


def _search(section, default_category, *, location, query=None, category=None, subarea=None, radius=None,
            sort=None, page=1, **filters):
    category = category or default_category
    prefix, path_prefix, geo, _site = _place(location, subarea, radius)
    path = path_prefix + category
    params = _upstream_filters(category, filters)
    params.update(geo)
    if query:
        params["query"] = query
    sort_token = P.SORTS.get(sort) if sort else None
    if sort_token:
        params["sort"] = sort_token
    sort_id = P.SORT_BATCH_IDS.get(sort_token, 0)
    offset = (page - 1) * P.PER_PAGE

    if offset < P.CHUNK:
        first = dict(params)
        first["batch"] = f"{prefix}-0-{P.CHUNK}-{sort_id}-0"
        first["searchPath"] = path
        data = sapi("/postings/search/full", first, label="search")
        _check_place(location, data)
        rows = _first_page_rows(data)
        local_count = P.to_int(data.get("totalResultCount")) or 0
        total = len(rows) if len(rows) < P.CHUNK else min(max(local_count, len(rows)), P.MAX_RESULTS)
        nearby_id = P.to_int(data.get("firstNearbyResultId"))
        nearby_from = next((i for i, r in enumerate(rows) if r["post_id"] == nearby_id), None) if nearby_id else None
        page_rows = rows[offset: offset + P.PER_PAGE]
    else:
        data = _digest(prefix, path, params, sort_id)
        _check_place(location, data)
        try:
            page_rows = _deep_rows(prefix, path, params, data, offset)
        except CraigslistBadRequest:
            # The cacheId expired (or the result set moved): one fresh digest.
            _forget_digest(prefix, path, params)
            data = _digest(prefix, path, params, sort_id)
            page_rows = _deep_rows(prefix, path, params, data, offset)
        local_count = P.to_int(data.get("totalResultCount")) or 0
        digests = data.get("items") or []
        total = len(digests)
        nearby_id = P.to_int(data.get("firstNearbyResultId"))
        min_id = (data.get("decode") or {}).get("minPostingId")
        nearby_from = None
        if nearby_id and isinstance(min_id, int):
            nearby_from = next((i for i, row in enumerate(digests)
                                if isinstance(row, list) and row and row[0] == nearby_id - min_id), None)

    areas = data.get("areas") if isinstance(data.get("areas"), dict) else {}
    center = _center(location, data, data.get("resultsOrder"))
    listings = [
        P.listing_summary(row, areas=areas, center=center,
                          nearby_flag=nearby_from is not None and offset + index >= nearby_from)
        for index, row in enumerate(page_rows)
    ]
    applied_category = refdata.category(P.text(data.get("categoryAbbr")) or category) or refdata.category(category)
    return {
        "pagination": {
            "page": page,
            "items_per_page": P.PER_PAGE,
            "total_pages": -(-total // P.PER_PAGE),
            "total_count": total,
        },
        "local_count": local_count,
        "query": query,
        "sort": P.SORT_NAMES.get(data.get("resultsOrder")),
        "category": refdata.category_ref(applied_category),
        "location": P.search_location(data.get("location"), areas),
        "listings": listings,
    }


# One public function per route: the response cache keys on the name.

def search(**kwargs):
    """Any category (default: everything for sale)."""
    return _search(None, "sss", **kwargs)


def for_sale(**kwargs):
    return _search("for_sale", "sss", **kwargs)


def vehicles(**kwargs):
    return _search("for_sale", "cta", **kwargs)


def housing(**kwargs):
    return _search("housing", "hhh", **kwargs)


def jobs(**kwargs):
    return _search("jobs", "jjj", **kwargs)


def gigs(**kwargs):
    return _search("gigs", "ggg", **kwargs)


def services(**kwargs):
    return _search("services", "bbb", **kwargs)


def community(**kwargs):
    return _search("community", "ccc", **kwargs)


def events(**kwargs):
    return _search("events", "eee", **kwargs)


def resumes(**kwargs):
    return _search("resumes", "rrr", **kwargs)


# ---- filters -----------------------------------------------------------------------------

def _filter_index():
    """{upstream filter name: {"param": public param, "values": {upstream
    id: public value}}} read off the request schemas, so the filters route
    can never drift from what the search routes accept."""
    index = {}
    for upstream in FLAG_PARAMS.values():
        index[upstream] = {"param": next(k for k, v in FLAG_PARAMS.items() if v == upstream), "values": {}}
    index["srchType"] = {"param": "title_only", "values": {}}
    index["is_paid"] = {"param": "is_paid", "values": {"yes": "true", "no": "false"}}
    index["availabilityMode"] = {"param": "availability", "values": {}}
    index["sale_date"] = {"param": "date", "values": {}}
    for public, upstream in VALUE_PARAMS.items():
        index.setdefault(upstream, {"param": public, "values": {}})
    for target in ("year_manufactured", "rv_length", "boat_length_overall"):
        public = "year" if target == "year_manufactured" else "length"
        index[f"min_{target}"] = {"param": f"min_{public}", "values": {}}
        index[f"max_{target}"] = {"param": f"max_{public}", "values": {}}
    for schema_cls in (schemas.ForSaleSearchSchema, schemas.VehicleSearchSchema, schemas.HousingSearchSchema,
                       schemas.JobSearchSchema, schemas.CommunitySearchSchema, schemas.ResumeSearchSchema,
                       schemas.EventSearchSchema):
        for public, field in schema_cls().fields.items():
            value_map = getattr(field, "value_map", None)
            if not value_map:
                continue
            if public in ("event_type",) or (public == "availability" and schema_cls is schemas.ResumeSearchSchema):
                for value, flag in value_map.items():     # one upstream checkbox per value
                    index[flag] = {"param": public, "values": {}, "value": value}
                continue
            upstream = VALUE_PARAMS.get(public) or ("availabilityMode" if public == "availability" else None)
            if upstream:
                index.setdefault(upstream, {"param": public, "values": {}})
                index[upstream]["values"].update({str(v): k for k, v in value_map.items()})
    return index


_FILTER_INDEX = None


def filter_index():
    global _FILTER_INDEX
    if _FILTER_INDEX is None:
        _FILTER_INDEX = _filter_index()
    return _FILTER_INDEX


def filters(category="sss", location=None):
    """The filters Craigslist offers for one category (they differ per
    category: cars have a drivetrain, apartments a laundry type) and the
    /craigslist/* param + accepted values for each."""
    site = refdata.resolve_site(location["value"]) if location and location.get("kind") == "area" else None
    if location and site is None:
        raise ValueError("location must be a Craigslist site (newyork) for filters")
    prefix = site["id"] if site else GEO_PREFIX
    data = sapi("/postings/search/full", {"batch": f"{prefix}-0-{P.CHUNK}-0-0", "searchPath": category},
                label="search filters")
    raw = data.get("filters") if isinstance(data.get("filters"), list) else []
    sort_options = []
    subareas = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        if row.get("name") == "sort":
            sort_options = [P.SORT_NAMES[o.get("value")] for o in row.get("options") or []
                            if isinstance(o, dict) and o.get("value") in P.SORT_NAMES]
        elif row.get("name") == "subarea" and site:
            subareas = [{"code": P.text(o.get("value")), "name": P.text(o.get("label"))}
                        for o in row.get("options") or [] if isinstance(o, dict) and o.get("value")]
    applied = refdata.category(P.text(data.get("categoryAbbr")) or category) or refdata.category(category)
    section = (applied or {}).get("section")
    return {
        "category": refdata.category_ref(applied),
        "search_route": _route_for(category, section),
        "location": P.search_location(data.get("location"), data.get("areas")) if site else None,
        "sort_options": sort_options or ["newest", "oldest"],
        "subareas": subareas,
        "filters": P.filter_definitions(raw, filter_index()),
    }


_VEHICLE_CATEGORIES = {"cta", "cto", "ctd", "mca", "mcy", "mcd", "boo", "boa", "bod", "rva", "rvs", "rvd",
                       "tra", "tro", "trb", "sna", "snw", "snd", "hva", "hvo", "hvd", "ava", "avo", "avd",
                       "pta", "pts", "ptd", "wta", "wto", "wtd", "mpa", "mpo", "mpd", "bpa", "bpo", "bpd"}


def _route_for(category, section):
    """Which search route exposes this category's filters."""
    if category in _VEHICLE_CATEGORIES:
        return "/craigslist/vehicles/search"
    return {
        "for_sale": "/craigslist/for-sale/search", "housing": "/craigslist/housing/search",
        "jobs": "/craigslist/jobs/search", "gigs": "/craigslist/gigs/search",
        "services": "/craigslist/services/search", "community": "/craigslist/community/search",
        "events": "/craigslist/events/search", "resumes": "/craigslist/resumes/search",
    }.get(section, "/craigslist/search")


# ---- suggestions -------------------------------------------------------------------------

def suggestions(query, type="search", category=None, location=None):
    """Search-box autocomplete. With a `location` the suggestions are that
    site's (and narrow to a `category`; type=make_model completes vehicle
    makes and models); without one they are Craigslist-wide."""
    site = None
    if location:
        site = refdata.resolve_site(location["value"])
        raw = get_json(f"{refs.site_link(site['hostname'])}/suggest",
                       {"v": 12, "type": type, "cat": category or ("cta" if type == "makemodel" else "sss"),
                        "term": query}, label="suggest")
        items = raw if isinstance(raw, list) else []
    else:
        items = sapi("/suggest/search", {"query": query}, label="suggest").get("items") or []
    values = []
    for item in items:
        value = P.text(item) if isinstance(item, str) else None
        if value and value not in values:
            values.append(value)
    return {
        "query": query,
        "type": "make_model" if type == "makemodel" else "search",
        "site": site["hostname"] if site else None,
        "category": refdata.category_ref(refdata.category(category)) if category else None,
        "count": len(values),
        "suggestions": values,
    }


# ---- counts ------------------------------------------------------------------------------

def counts(query, location, subarea=None, radius=None):
    """How many listings match `query` in every section and category of one
    location — where to search before searching."""
    params = {"query": query}
    site = None
    if location.get("kind") == "area":
        site = refdata.resolve_site(location["value"])
        params["areaId"] = site["id"]
        if subarea:
            refdata.resolve_subarea(site, subarea)
            params["subarea"] = subarea
    else:
        params["areaId"] = GEO_PREFIX
        params["search_distance"] = radius or DEFAULT_RADIUS
        if location.get("kind") == "postal":
            params["postal"] = location["value"]
        else:
            params["lat"], params["lon"] = location["latitude"], location["longitude"]
        # The count call falls back to the prefix site without saying so:
        # the search call names the place it resolved.
        probe = {k: v for k, v in params.items() if k != "areaId"}
        probe["batch"] = f"{GEO_PREFIX}-0-{P.CHUNK}-0-0"
        probe["searchPath"] = "sss"
        _check_place(location, sapi("/postings/search/full", probe, label="search"))
    data = sapi("/categories/count", params, label="category counts")
    sections = P.category_counts(data.get("items"))
    return {
        "query": query,
        "site": site["hostname"] if site else None,
        "subarea": subarea,
        "total_count": sum(s["count"] for s in sections),
        "sections": sections,
    }
