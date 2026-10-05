"""/craigslist/listings/* — one listing in full, or several at once.

A listing is read from the site's posting API. By id that is one call. A
listing link carries the numeric post id instead; with the subarea in the
link the API takes host / subarea / category / post id directly, and
without one the short link's own redirect names the id first."""
import re

from craigslist import parsers as P
from craigslist import refs
from craigslist.fetch import (CraigslistBadRequest, CraigslistNotFound, rapi, redirect_target,
                              run_parallel)

_UUID_IN_LINK = re.compile(r"/view/(?:d/[^/?#]+/)?([A-Za-z0-9_\-]{20,24})(?:[/?#]|$)")


def _posting(spec):
    """A resolved `listing` spec -> the raw posting object."""
    if spec.get("uuid"):
        path = f"/postings/{spec['uuid']}"
    elif spec.get("subarea"):
        path = f"/postings/{spec['hostname']}/{spec['subarea']}/{spec['category']}/{spec['post_id']}"
    else:
        target = redirect_target(f"{refs.site_link(spec['hostname'])}/{spec['category']}/{spec['post_id']}.html",
                                 label="listing link")
        match = _UUID_IN_LINK.search(target or "")
        if not match:
            raise CraigslistNotFound(f"listing {spec['post_id']} not found")
        path = f"/postings/{match.group(1)}"
    try:
        data = rapi(path, label="listing")
    except CraigslistBadRequest:
        # e.g. a subarea code the site does not have: the link is wrong.
        raise CraigslistNotFound("listing not found")
    items = data.get("items") if isinstance(data.get("items"), list) else []
    if not items or not isinstance(items[0], dict):
        raise CraigslistNotFound("listing not found (deleted, expired or flagged)")
    return items[0]


def _label(spec):
    return spec.get("uuid") or str(spec.get("post_id"))


def details(listing):
    """Everything Craigslist shows on one listing page."""
    return {"listing": P.listing_details(_posting(listing))}


def batch(listings):
    """Up to 20 listings in one call. A listing that is gone comes back as
    {"listing": <what was asked>, "error": ...} instead of failing the set."""
    def one(spec):
        def run():
            try:
                return P.listing_details(_posting(spec))
            except CraigslistNotFound as e:
                return {"id": spec.get("uuid"), "post_id": spec.get("post_id"), "error": str(e) or "not found"}
        return run

    rows = run_parallel([one(spec) for spec in listings])
    found = [r for r in rows if r and "error" not in r]
    return {
        "count": len(found),
        "requested_count": len(listings),
        "listings": found,
        "not_found": [{"id": r.get("id"), "post_id": r.get("post_id"), "error": r["error"]}
                      for r in rows if r and "error" in r],
    }
