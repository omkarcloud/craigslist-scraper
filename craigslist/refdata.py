"""Reference tables shared by the /craigslist/* endpoint modules.

  * sites ("areas"): the ~700 Craigslist sites with their subareas, from
    reference.craigslist.org/Areas — memoised in-process for a day (a site
    is added a few times a decade).
  * categories: craigslist/categories.json, the section -> category tree the
    site ships in its search bundle (8 sections, 134 categories; a for-sale
    category is an owner + dealer pair with its own two leaf codes). Static:
    codes are part of every listing URL and never change; a new category
    simply decodes as code-less until the file is refreshed.
"""
import json
import math
import os
import threading
import time

from craigslist import refs
from craigslist.fetch import REFERENCE, get_json

AREAS_TTL = 24 * 3600

_lock = threading.Lock()
_memo = {}


def memo(key, ttl, build):
    now = time.time()
    with _lock:
        hit = _memo.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = build()
    with _lock:
        _memo[key] = (now + ttl, value)
    return value


# ---- categories (static) -----------------------------------------------------------

# Public section key -> the site's section code ("hub" category).
SECTIONS = {
    "for_sale": "sss",
    "housing": "hhh",
    "jobs": "jjj",
    "gigs": "ggg",
    "services": "bbb",
    "community": "ccc",
    "events": "eee",
    "resumes": "rrr",
}
_SECTION_KEY = {code: key for key, code in SECTIONS.items()}


def _load_categories():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    by_code, by_id, tree = {}, {}, []
    for section in raw.get("sections") or []:
        key = _SECTION_KEY.get(section.get("code"))
        if not key:
            continue
        hub = {"code": section["code"], "name": section.get("name"), "section": key,
               "id": None, "parent": None, "kind": "section"}
        by_code[hub["code"]] = hub
        rows = []
        for cat in section.get("categories") or []:
            row = {"code": cat["code"], "name": cat.get("name"),
                   "section": key, "id": cat.get("id"), "parent": None, "kind": "category"}
            by_code[row["code"]] = row
            if row["id"]:
                by_id[row["id"]] = row
            children = []
            for seller in ("owner", "dealer"):
                child = cat.get(seller)
                if not child:
                    continue
                leaf = {"code": child["code"], "name": child.get("name"), "section": key,
                        "id": child.get("id"), "parent": row["code"], "kind": seller}
                by_code[leaf["code"]] = leaf
                if leaf["id"]:
                    by_id[leaf["id"]] = leaf
                children.append(leaf)
            rows.append((row, children))
        tree.append((hub, rows))
    return by_code, by_id, tree


_BY_CODE, _BY_ID, _TREE = _load_categories()


def category(code):
    """Category row for a code ('cta', 'cto', 'sss') or None."""
    return _BY_CODE.get(code) if isinstance(code, str) else None


def category_by_id(category_id):
    """Leaf category row for the numeric id search results carry, or None."""
    return _BY_ID.get(category_id)


def category_ref(row):
    """The public {code, name, section} reference of a category row."""
    if not row:
        return None
    return {"code": row["code"], "name": row["name"], "section": row["section"]}


def resolve_category(value, section=None):
    """A category code ('cta') or its exact name ('cars & trucks') -> the
    code, optionally required to belong to `section` (public key). Raises
    ValueError with a user-facing message."""
    raw = " ".join(str(value or "").split()).lower()
    if not raw:
        raise ValueError("category is required")
    row = _BY_CODE.get(raw)
    if row is None:
        matches = [r for r in _BY_CODE.values() if (r.get("name") or "").lower() == raw
                   and (section is None or r["section"] == section)]
        row = matches[0] if matches else None
    if row is None:
        raise ValueError(f"unknown category '{value}' — see /craigslist/categories for the codes")
    if section is not None and row["section"] != section:
        raise ValueError(f"category '{row['code']}' ({row['name']}) belongs to the "
                         f"{row['section'].replace('_', ' ')} section — see /craigslist/categories")
    return row["code"]


def category_tree(section=None):
    """[{code, name, section, categories: [{id, code, name, by_owner, by_dealer}]}]."""
    out = []
    for hub, rows in _TREE:
        if section and hub["section"] != section:
            continue
        cats = []
        for row, children in rows:
            entry = {"id": row["id"], "code": row["code"], "name": row["name"], "by_owner": None, "by_dealer": None}
            for child in children:
                entry["by_owner" if child["kind"] == "owner" else "by_dealer"] = {
                    "id": child["id"], "code": child["code"], "name": child["name"]}
            cats.append(entry)
        out.append({"section": hub["section"], "code": hub["code"], "name": hub["name"],
                    "category_count": len(cats), "categories": cats})
    return out


# ---- sites ("areas") ---------------------------------------------------------------

def _norm(value):
    return " ".join(str(value or "").lower().replace(",", " ").replace(".", " ").split())


def _site_row(raw):
    if not isinstance(raw, dict) or raw.get("AreaID") is None or not raw.get("Hostname"):
        return None
    hostname = str(raw["Hostname"]).lower()
    subareas = []
    for sub in raw.get("SubAreas") or []:
        if isinstance(sub, dict) and sub.get("Abbreviation"):
            subareas.append({
                "id": sub.get("SubAreaID"),
                "code": str(sub["Abbreviation"]).lower(),
                "name": sub.get("Description") or sub.get("ShortDescription") or None,
            })
    return {
        "id": raw["AreaID"],
        "hostname": hostname,
        "abbreviation": (raw.get("Abbreviation") or "").lower() or None,
        "name": raw.get("Description") or raw.get("ShortDescription") or hostname,
        "short_name": raw.get("ShortDescription") or None,
        "region": raw.get("Region") or None,
        "country": raw.get("Country") or None,
        "latitude": raw.get("Latitude"),
        "longitude": raw.get("Longitude"),
        "timezone": raw.get("Timezone") or None,
        "link": refs.site_link(hostname),
        "subareas": subareas,
    }


def sites():
    """{"list": [site rows], "by_id", "by_hostname", "by_abbreviation", "by_name"}."""
    def build():
        raw = get_json(REFERENCE + "/Areas", label="reference Areas")
        rows = [r for r in (_site_row(x) for x in (raw if isinstance(raw, list) else [])) if r]
        if not rows:
            raise RuntimeError("Craigslist site table came back empty")
        by_name = {}
        for row in rows:
            for name in (row["name"], row["short_name"]):
                if name:
                    by_name.setdefault(_norm(name), row)
        return {
            "list": rows,
            "by_id": {r["id"]: r for r in rows},
            "by_hostname": {r["hostname"]: r for r in rows},
            "by_abbreviation": {r["abbreviation"]: r for r in rows if r["abbreviation"]},
            "by_name": by_name,
        }
    return memo("sites", AREAS_TTL, build)


def site_by_id(area_id):
    try:
        return sites()["by_id"].get(area_id)
    except Exception:
        # A site name is decoration on a listing: never fail an endpoint over it.
        return None


def search_sites(query, limit=None):
    """Sites whose hostname / abbreviation / name / region match `query`,
    best first (exact, then prefix, then substring)."""
    needle = _norm(query)
    if not needle:
        return []
    compact = needle.replace(" ", "")
    ranked = []
    for row in sites()["list"]:
        names = [_norm(row["name"]), _norm(row["short_name"])]
        keys = [row["hostname"], row["abbreviation"] or ""] + names
        if needle in keys or compact == row["hostname"]:
            rank = 0
        elif any(k.startswith(needle) for k in keys if k) or row["hostname"].startswith(compact):
            rank = 1
        elif any(needle in k for k in names if k):
            rank = 2
        elif needle == _norm(row["region"]):
            rank = 3
        else:
            continue
        ranked.append((rank, row["id"], row))
    ranked.sort(key=lambda r: (r[0], r[1]))
    rows = [r[2] for r in ranked]
    return rows[:limit] if limit else rows


def resolve_site(value):
    """Hostname / abbreviation / name -> the site row. Raises ValueError
    (with the closest matches) when nothing or more than one site fits."""
    table = sites()
    key = _norm(value)
    row = (table["by_hostname"].get(key) or table["by_hostname"].get(key.replace(" ", ""))
           or table["by_abbreviation"].get(key) or table["by_name"].get(key))
    if row:
        return row
    matches = search_sites(value, limit=6)
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(f"{m['hostname']} ({m['name']})" for m in matches[:5])
        raise ValueError(f"location '{value}' matches several Craigslist sites: {names} — pass one hostname")
    raise ValueError(f"unknown Craigslist site '{value}' — see /craigslist/locations/search")


def resolve_subarea(site, code):
    """Validate a subarea code against a site row -> the subarea dict."""
    for sub in site.get("subareas") or []:
        if sub["code"] == code:
            return sub
    known = ", ".join(s["code"] for s in site.get("subareas") or [])
    if known:
        raise ValueError(f"'{code}' is not a subarea of {site['hostname']} — one of: {known}")
    raise ValueError(f"{site['hostname']} has no subareas")


def distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres."""
    try:
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dlat, dlon = p2 - p1, math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
        return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))
    except (TypeError, ValueError):
        return None


def nearest_sites(latitude, longitude, limit=10):
    """[(site row, distance_km)] nearest first."""
    ranked = []
    for row in sites()["list"]:
        d = distance_km(latitude, longitude, row["latitude"], row["longitude"])
        if d is not None:
            ranked.append((d, row["id"], row))
    ranked.sort(key=lambda r: (r[0], r[1]))
    return [(r[2], r[0]) for r in ranked[:limit]]
