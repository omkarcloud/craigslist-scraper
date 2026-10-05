"""/craigslist/locations* and /craigslist/categories — the reference data a
search needs: which Craigslist sites exist (with their subareas), which one
covers a point on the map, and the category codes of every section."""
from craigslist import parsers as P
from craigslist import refdata
from craigslist.fetch import CraigslistBadRequest, rapi


def locations(country=None, region=None):
    """Every Craigslist site, optionally narrowed to a country (US, CA, GB
    ...) and a region (state / province code)."""
    rows = refdata.sites()["list"]
    if country:
        rows = [r for r in rows if (r.get("country") or "").upper() == country]
    if region:
        rows = [r for r in rows if (r.get("region") or "").upper() == region]
    rows = sorted(rows, key=lambda r: (r.get("country") or "", r.get("region") or "", r.get("name") or ""))
    return {
        "count": len(rows),
        "country": country,
        "region": region,
        "locations": [P.site_public(r) for r in rows],
    }


def search(query, limit=10):
    """Find a Craigslist site by hostname, abbreviation, city or region."""
    rows = refdata.search_sites(query, limit=limit)
    return {"query": query, "count": len(rows), "locations": [P.site_public(r) for r in rows]}


def _place(latitude, longitude):
    """Craigslist's own reverse geocode of a point: city, postal code and
    the site + subarea that own it (None outside its coverage)."""
    try:
        items = rapi("/locations", {"lat": latitude, "lon": longitude}, label="locations").get("items") or []
    except CraigslistBadRequest:
        return None
    row = items[0] if items and isinstance(items[0], dict) else None
    if not row:
        return None
    hostname = P.text(row.get("url"))
    hostname = hostname.split(".")[0] if hostname else None
    site = refdata.site_by_id(row.get("areaId")) or {}
    subarea = next((s for s in site.get("subareas") or [] if s.get("id") == row.get("subareaId")), None)
    return {
        "city": P.text(row.get("city")),
        "region": P.text(row.get("region")),
        "country": P.text(row.get("country")),
        "postal_code": P.text(row.get("postal")),
        "latitude": P.to_coordinate(row.get("lat")),
        "longitude": P.to_coordinate(row.get("lon")),
        "site": hostname,
        "subarea": subarea["code"] if subarea else None,
        "subarea_name": subarea["name"] if subarea else None,
        "suggested_radius": P.to_number(row.get("radius")),
    }


def nearby(location, limit=10):
    """The Craigslist sites nearest a point (or nearest another site),
    closest first, plus what Craigslist calls that point."""
    if location.get("kind") == "coordinates":
        latitude, longitude = location["latitude"], location["longitude"]
        place = _place(latitude, longitude)
        skip = None
    else:
        origin = refdata.resolve_site(location["value"])
        latitude, longitude = origin["latitude"], origin["longitude"]
        place = None
        skip = origin["id"]
    ranked = [(row, km) for row, km in refdata.nearest_sites(latitude, longitude, limit + 1) if row["id"] != skip]
    ranked = ranked[:limit]
    return {
        "latitude": latitude,
        "longitude": longitude,
        "place": place,
        "count": len(ranked),
        "locations": [P.site_public(row, km) for row, km in ranked],
    }


def categories(section=None):
    """The category codes of every section (or one), with the by-owner /
    by-dealer codes of each for-sale category."""
    tree = refdata.category_tree(section)
    return {
        "section_count": len(tree),
        "category_count": sum(s["category_count"] for s in tree),
        "sections": tree,
    }
