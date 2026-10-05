"""/kleinanzeigen/sellers/* and /kleinanzeigen/stores/*: a seller's public
profile and inventory; a commercial store page (/pro/<slug>) and its
inventory."""
from kleinanzeigen import fetch, lookup
from kleinanzeigen import parsers as P
from kleinanzeigen.search import keep_fresh_if_empty, name_categories, run_search


def _seller(user_id):
    try:
        raw = fetch.api(f"users/public/{user_id}/profile", label="seller profile",
                        missing=f"Seller {user_id} not found on Kleinanzeigen")
    except fetch.KleinanzeigenBadRequest:   # an id the API cannot parse / does not know
        raise fetch.KleinanzeigenNotFound(f"Seller {user_id} not found on Kleinanzeigen")
    item = P.seller(raw)
    if not item:
        raise fetch.KleinanzeigenNotFound(f"Seller {user_id} not found on Kleinanzeigen")
    return item


def profile(seller):
    """A seller's public profile: name, private / commercial, member since,
    listing counts, followers, satisfaction / friendliness / reliability
    badges, reply rate and time, and the store they run (if any)."""
    return _seller(seller)


def listings(seller, query=None, category=None, min_price=None, max_price=None, sort="newest",
             page=1, page_size=25):
    """A seller's current listings, optionally narrowed by keyword,
    category or price."""
    category_id = lookup.category_id(category) if category else None
    result = run_search(user_id=seller, query=query, category_id=category_id, min_price=min_price,
                        max_price=max_price, sort=sort, page=page, page_size=page_size,
                        label="seller listings")
    if not result["results"] and page == 1 and not (query or category_id or min_price or max_price):
        _seller(seller)             # an unknown seller is a 404, not an empty list
    return keep_fresh_if_empty({"seller_id": seller, "results": result["results"],
                                "category_counts": name_categories(result["category_counts"]),
                                "pagination": result["pagination"]})


def store_profile(store):
    """A commercial store page: description, highlights, logo and gallery,
    phone, website, address with coordinates, opening hours, legal notice,
    plus the owner's seller profile."""
    item = P.store(lookup.store_raw(store))
    if not item:
        raise fetch.KleinanzeigenNotFound("Store not found on Kleinanzeigen")
    owner = None
    if item.get("seller_id"):
        owner = fetch.run_parallel_quiet([lambda: _seller(item["seller_id"])])[0]
    if owner:
        owner.pop("store", None)
    item["seller"] = owner
    return item


def store_listings(store, query=None, category=None, min_price=None, max_price=None, sort="newest",
                   page=1, page_size=25):
    """A store's current listings, optionally narrowed by keyword, category
    or price."""
    store_id = lookup.store_id(store)
    category_id = lookup.category_id(category) if category else None
    result = run_search(store_id=store_id, query=query, category_id=category_id, min_price=min_price,
                        max_price=max_price, sort=sort, page=page, page_size=page_size,
                        label="store listings")
    return keep_fresh_if_empty({"store_id": store_id, "results": result["results"],
                                "category_counts": name_categories(result["category_counts"]),
                                "pagination": result["pagination"]})
