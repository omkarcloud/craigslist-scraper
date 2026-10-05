"""Cache TTL per /craigslist/* endpoint (cache.py, keyed on the validated
params).

Tiers follow how fast Craigslist moves each surface: busy categories gain
a listing every few seconds, a listing itself is edited rarely (and deleted
often — a short TTL keeps a sold item from lingering), and the site /
category tables change a few times a decade.
"""
from datetime import timedelta

# Search results: new listings land constantly; the site's own result cache
# (cacheTs) turns over in about a minute.
SEARCH_CACHE = timedelta(minutes=2)
# Per-category counts for a query move with the same listings.
COUNTS_CACHE = timedelta(minutes=5)
# A listing: edits are rare, deletions are not.
LISTING_CACHE = timedelta(minutes=10)
# The filter form of a category only changes when Craigslist ships a release.
FILTERS_CACHE = timedelta(hours=12)
# Autocomplete terms are popularity-ranked and drift over days.
SUGGESTIONS_CACHE = timedelta(hours=6)
# Sites, subareas and the reverse geocode of a point.
LOCATIONS_CACHE = timedelta(days=1)
# The category tree is static (craigslist/categories.json).
CATEGORIES_CACHE = timedelta(days=1)
