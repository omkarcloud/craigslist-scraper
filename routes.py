"""The 19 Craigslist endpoints. Every path is served with and without the
`/craigslist` prefix, so code generated against the hosted API (paths like
/search) runs unchanged against this server.

Each route validates its query with the marshmallow schema in
craigslist/schemas.py (one param per input: `location` is a Craigslist site,
a link on it, a postal code or latitude,longitude; `listing` is a listing ID
or any craigslist.org listing link) and calls the function that returns the
JSON. Search routes answer with the same flat block as the hosted API:
count / per_page / current_page / total_pages / next / previous."""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from craigslist import listings, locations, search
from craigslist import schemas as S
from schema_fields import load_query
from scraper_errors import BadRequest, NotFound


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The request query as unicode strings (bottle 0.12's .get() hands back
    latin-1 decoded bytes, so a UTF-8 "Montréal" would arrive as "MontrÃ©al")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page_link(path, params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "")}
    query["page"] = page
    host = request.headers.get("Host") or "localhost"
    return f"{request.urlparts.scheme}://{host}{path}?{urlencode(query, doseq=True)}"


def paginate(result, path, params):
    """Lift the scraper's `pagination` block into the flat hosted-API shape."""
    pagination = result.pop("pagination", None) or {}
    page = _as_int(pagination.get("page")) or _as_int(params.get("page")) or 1
    total_pages = max(_as_int(pagination.get("total_pages")), 0)
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(path, params, page + 1 if page < total_pages else None),
        "previous": _page_link(path, params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(label, schema, fn, paginated):
    """Validate, run, and map errors: bad params -> 400, no data -> 404,
    blocked / transport failure -> 500."""
    raw = query_dict()
    data, error = load_query(schema, raw)
    if error:
        return json_response(error, 400)
    try:
        result = fn(**data)
    except ValueError as e:                # a site, subarea or postal code Craigslist does not have
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:                # upstream rejected the params
        return json_response({"error": f"craigslist rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:                 # retries exhausted / blocked
        return json_response({"error": f"craigslist {label} failed: {e}"}, 500)
    if paginated:
        result = paginate(result, request.path, raw)
    return json_response(result)


# public path -> (schema, function, paginated), in the documented order
ENDPOINTS = [
    ("/listings/details", S.ListingSchema, listings.details, False),
    ("/search", S.SearchSchema, search.search, True),
    ("/vehicles/search", S.VehicleSearchSchema, search.vehicles, True),
    ("/housing/search", S.HousingSearchSchema, search.housing, True),
    ("/for-sale/search", S.ForSaleSearchSchema, search.for_sale, True),
    ("/jobs/search", S.JobSearchSchema, search.jobs, True),
    ("/gigs/search", S.GigSearchSchema, search.gigs, True),
    ("/services/search", S.ServiceSearchSchema, search.services, True),
    ("/events/search", S.EventSearchSchema, search.events, True),
    ("/community/search", S.CommunitySearchSchema, search.community, True),
    ("/resumes/search", S.ResumeSearchSchema, search.resumes, True),
    ("/listings/batch", S.ListingBatchSchema, listings.batch, False),
    ("/search/counts", S.CountsSchema, search.counts, False),
    ("/search/suggestions", S.SuggestionsSchema, search.suggestions, False),
    ("/search/filters", S.FiltersSchema, search.filters, False),
    ("/locations/search", S.LocationSearchSchema, locations.search, False),
    ("/locations/nearby", S.NearbyLocationsSchema, locations.nearby, False),
    ("/locations", S.LocationsSchema, locations.locations, False),
    ("/categories", S.CategoriesSchema, locations.categories, False),
]


def mount(path, schema, fn, paginated):
    """Serve a handler at /path and /craigslist/path."""
    def handler():
        return call(path.strip("/"), schema, fn, paginated)
    handler.__name__ = "craigslist_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/craigslist" + path, method="GET")(handler)


for _path, _schema, _fn, _paginated in ENDPOINTS:
    mount(_path, _schema, _fn, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, _, _, _ in ENDPOINTS]})
