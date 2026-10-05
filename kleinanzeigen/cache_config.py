"""Cache TTL per /kleinanzeigen/* endpoint (cache.py, keyed on the validated
params — marshmallow fills the defaults, so `?page=1` and no `page` share a
row).

Classifieds move fast: a search sorted by newest changes every minute and a
listing can sell or be edited at any time, so listing lists are cached for
minutes only; a listing's own text changes rarely but its view counter and
status do, so details sit a little longer. Seller / store profiles change
over days, and the category tree, filter metadata and locations are
reference data.
"""
from datetime import timedelta

# --- search / lists --------------------------------------------------------------------
SEARCH_CACHE = timedelta(minutes=10)
SUGGEST_CACHE = timedelta(days=1)
INVENTORY_CACHE = timedelta(minutes=30)    # a seller's / store's listings

# --- listings --------------------------------------------------------------------------
LISTING_CACHE = timedelta(minutes=30)
SIMILAR_CACHE = timedelta(hours=2)
VIEWS_CACHE = timedelta(minutes=10)

# --- sellers / stores ------------------------------------------------------------------
SELLER_CACHE = timedelta(hours=6)
STORE_CACHE = timedelta(hours=12)

# --- reference data --------------------------------------------------------------------
CATEGORIES_CACHE = timedelta(days=7)
FILTERS_CACHE = timedelta(days=3)
LOCATION_CACHE = timedelta(days=30)
