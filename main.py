"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. A location is a Craigslist site (newyork), a postal code or
"latitude,longitude"; a listing is a listing ID or any craigslist.org
listing link.
"""
import json
import os

from craigslist import listings, refs, search

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # 120 listings a page from any category, up to 10,000 per search
    results = search.search(location=refs.resolve_location("newyork"), query="macbook pro")
    save("search_macbook_pro.json", results)

    # cars with Craigslist's own filters (fuel_type "4" = electric, see craigslist/schemas.py)
    save("vehicles_tesla_model_3.json", search.vehicles(
        location=refs.resolve_location("losangeles"), category="cta", make_model="tesla model 3", fuel_type=["4"]))

    # everything on one listing page: description, attributes, address, every photo
    first = results["listings"][0]["id"]
    save("listing_details.json", listings.details(refs.resolve_listing(first)))
