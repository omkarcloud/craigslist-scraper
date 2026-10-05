"""Craigslist reference parsing: ONE param per input that auto-detects its
forms (tripadvisor QueryOrIdField convention — never a sibling `url`/`id`
pair), plus the link builders.

  location   newyork | nyc | "new york" (a Craigslist site: hostname,
                 three-letter abbreviation or name)
             | https://newyork.craigslist.org/... (any link on that site)
             | 94103 | "M5V 2T6" (a postal code -> radius search around it)
             | "40.7128,-74.0060" (latitude,longitude -> radius search)
  listing    btYtLiemfZzTX5bosg3gfp (the listing `id` from any search)
             | https://www.craigslist.org/view/d/<slug>/<id>
             | https://newyork.craigslist.org/que/cto/d/<slug>/7978137819.html
             | https://newyork.craigslist.org/cto/7978137819.html

Everything here is pure string work; the lookups against the live site
tables (does this site exist, does it have that subarea) are refdata.py's.
"""
import re
from urllib.parse import urlparse

SITE = "https://www.craigslist.org"
IMAGE_HOST = "https://images.craigslist.org"

_COORDS_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
# Listing ids are 22 chars: base58 for most posts, base64url (with - and _)
# for paid / dealer posts ("NNmhMYu-8RGFAJs7atcCJw").
_UUID_RE = re.compile(r"^[A-Za-z0-9_\-]{20,24}$")
_POST_ID_RE = re.compile(r"^\d{8,12}$")
_CODE_RE = re.compile(r"^[a-z]{3}$")
_HOST_RE = re.compile(r"^[a-z0-9]{2,30}$")
_POSTAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{1,10}[A-Za-z0-9]$")


def _is_url(value):
    low = value.lower()
    return low.startswith(("http://", "https://", "//")) or ".craigslist.org" in low


def _parse_link(value):
    """A craigslist.org link -> (host label, path segments). Raises
    ValueError for another site."""
    url = value
    if url.startswith("//"):
        url = "https:" + url
    elif not url.lower().startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host.endswith(".craigslist.org"):
        raise ValueError("not a craigslist.org link")
    label = host[: -len(".craigslist.org")]
    segments = [s for s in parsed.path.split("/") if s]
    return label, segments


# ---- links -----------------------------------------------------------------------

def site_link(hostname):
    return f"https://{hostname}.craigslist.org" if hostname else None


def view_link(slug, uuid):
    """The site's canonical listing link (what every listing page redirects to)."""
    if not uuid:
        return None
    return f"{SITE}/view/d/{slug}/{uuid}" if slug else f"{SITE}/view/{uuid}"


def image_link(image_id, size="1200x900"):
    """'3:00C0C_7py8tcpPuyA_0CI0t2' -> the image link at `size` (50x50c,
    300x300, 600x450, 1200x900). The leading '<n>:' is the image generation;
    generation 0 has no 1200x900 rendition."""
    if not isinstance(image_id, str) or not image_id:
        return None
    generation, sep, name = image_id.partition(":")
    if not sep:
        generation, name = "3", image_id
    if not name:
        return None
    if generation == "0" and size == "1200x900":
        size = "600x450"
    return f"{IMAGE_HOST}/{name}_{size}.jpg"


def slug_of_view_link(link):
    """'https://www.craigslist.org/view/d/<slug>/<uuid>' -> '<slug>' (None
    for the slug-less form)."""
    if not isinstance(link, str):
        return None
    match = re.search(r"/view/d/([^/?#]+)/[A-Za-z0-9_\-]+", link)
    return match.group(1) if match else None


# ---- location --------------------------------------------------------------------

def resolve_location(value):
    """One `location` value -> a spec dict the search resolves against the
    live site table:

        {"kind": "area", "value": "newyork"}          hostname / abbreviation / name
        {"kind": "postal", "value": "94103"}
        {"kind": "coordinates", "latitude": .., "longitude": ..}
    """
    value = " ".join(str(value or "").split())
    if not value:
        raise ValueError("location is required")
    match = _COORDS_RE.match(value)
    if match:
        latitude, longitude = float(match.group(1)), float(match.group(2))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("location coordinates must be latitude,longitude (40.7128,-74.0060)")
        return {"kind": "coordinates", "latitude": latitude, "longitude": longitude}
    if _is_url(value):
        try:
            label, segments = _parse_link(value)
        except ValueError:
            raise ValueError("location must be a craigslist.org link, a site name (newyork), a postal code or latitude,longitude")
        if label == "www":
            # https://www.craigslist.org/area/newyork | /search/area/newyork
            if "area" in segments and segments.index("area") + 1 < len(segments):
                label = segments[segments.index("area") + 1].lower()
            else:
                raise ValueError("location link must point at one Craigslist site (https://newyork.craigslist.org)")
        if not _HOST_RE.match(label):
            raise ValueError("location link must point at one Craigslist site (https://newyork.craigslist.org)")
        return {"kind": "area", "value": label}
    if any(ch.isdigit() for ch in value):
        if not _POSTAL_RE.match(value):
            raise ValueError("location must be a site name (newyork), a postal code (94103) or latitude,longitude")
        return {"kind": "postal", "value": value.replace(" ", "").upper()}
    if len(value) > 60:
        raise ValueError("location is too long")
    return {"kind": "area", "value": value.lower()}


def resolve_code(value, what):
    """A three-letter Craigslist code (subarea 'brk', category 'cta')."""
    value = str(value or "").strip().lower()
    if not _CODE_RE.match(value):
        raise ValueError(f"{what} must be a three-letter Craigslist code")
    return value


# ---- listing ---------------------------------------------------------------------

_LISTING_HELP = ("listing must be a listing id from a search result (btYtLiemfZzTX5bosg3gfp) "
                 "or a craigslist.org listing link")


def resolve_listing(value):
    """Listing id (uuid) or any craigslist.org listing link ->

        {"uuid": "btYtLiemfZzTX5bosg3gfp"}
        {"hostname": "newyork", "subarea": "que" | None, "category": "cto", "post_id": 7978137819}

    A bare numeric post id cannot be looked up on its own (Craigslist needs
    the site and category it was posted in), so it is refused with a
    message that says what to pass instead.
    """
    value = str(value or "").strip()
    if not value:
        raise ValueError("listing is required")
    if _is_url(value):
        try:
            label, segments = _parse_link(value)
        except ValueError:
            raise ValueError(_LISTING_HELP)
        if "view" in segments:
            tail = segments[segments.index("view") + 1:]
            candidate = tail[-1] if tail else ""
            if _UUID_RE.match(candidate) and not candidate.isdigit():
                return {"uuid": candidate}
            raise ValueError("listing link must look like https://www.craigslist.org/view/d/<title>/<id>")
        last = segments[-1] if segments else ""
        match = re.match(r"^(\d{8,12})\.html?$", last)
        if not match or label == "www":
            raise ValueError("listing link must point at one listing "
                             "(https://newyork.craigslist.org/que/cto/d/<title>/7978137819.html)")
        head = segments[:-1]
        if "d" in head:
            head = head[: head.index("d")]
        codes = [s.lower() for s in head if _CODE_RE.match(s.lower())]
        if not codes or len(codes) > 2 or len(codes) != len(head):
            raise ValueError("listing link must carry the category it was posted in "
                             "(https://newyork.craigslist.org/cto/7978137819.html)")
        return {
            "hostname": label,
            "subarea": codes[0] if len(codes) == 2 else None,
            "category": codes[-1],
            "post_id": int(match.group(1)),
        }
    if _POST_ID_RE.match(value):
        raise ValueError("a numeric post id cannot be looked up on its own — pass the listing `id` "
                         "from a search result or the listing link")
    if not _UUID_RE.match(value):
        raise ValueError(_LISTING_HELP)
    return {"uuid": value}


def resolve_listings(value, max_items=20):
    """Comma-separated listing ids / links -> [spec, ...] (deduped, order kept)."""
    out = []
    for raw in str(value or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        spec = resolve_listing(raw)
        if spec not in out:
            out.append(spec)
    if not out:
        raise ValueError("listings is required")
    if len(out) > max_items:
        raise ValueError(f"at most {max_items} listings")
    return out
