"""/kleinanzeigen/listings/*: one listing in full, a batch of listings,
similar listings and view counts."""
from kleinanzeigen import fetch
from kleinanzeigen import parsers as P

SIMILAR_MAX = 12


def _ad(listing_id):
    raw = fetch.api(f"ads/{listing_id}.json", label="listing",
                    missing=f"Listing {listing_id} not found on Kleinanzeigen (deleted or expired)")
    item = P.listing(raw)
    if not item:
        raise fetch.KleinanzeigenNotFound(f"Listing {listing_id} not found on Kleinanzeigen")
    return item


def _views(listing_ids):
    """{listing id: views}; one call for the whole batch."""
    if not listing_ids:
        return {}
    raw = fetch.api("v2/counters/ads/vip", {"adIds": ",".join(listing_ids)}, label="view counts")
    return P.view_counts(raw)


def _seller_profile(user_id):
    return P.seller(fetch.api(f"users/public/{user_id}/profile", label="seller profile"))


def _merge_seller(item, profile):
    """Fold the public profile (listing counts, followers, reply badges,
    store branding) into the listing's seller block."""
    if not profile or not item.get("seller"):
        return
    seller = item["seller"]
    for key in ("active_listings", "total_listings", "followers", "has_secure_payment"):
        seller[key] = profile.get(key)
    for key in ("name", "member_since", "account_type"):
        seller[key] = seller.get(key) or profile.get(key)
    if profile.get("rating") and any(v is not None for v in profile["rating"].values()):
        seller["rating"] = profile["rating"]
    if profile.get("store"):
        seller["store"] = {**(seller.get("store") or {}), **{k: v for k, v in profile["store"].items()
                                                             if v is not None}}


def details(listing):
    """Everything about one listing: text, price, attributes, images,
    location with coordinates, shipping, seller reputation and view count."""
    item = _ad(listing)
    user_id = (item.get("seller") or {}).get("id")
    views, profile = fetch.run_parallel_quiet([
        lambda: _views([listing]),
        (lambda: _seller_profile(user_id)) if user_id else (lambda: None),
    ])
    item["views"] = (views or {}).get(listing)
    _merge_seller(item, profile)
    return item


def batch(listings):
    """Up to 20 listings in one call. Listings that no longer exist come
    back in `not_found` instead of failing the call."""
    def one(listing_id):
        try:
            return _ad(listing_id)
        except fetch.KleinanzeigenNotFound:
            return None

    items, views = fetch.run_parallel([
        lambda: fetch.run_parallel([lambda i=i: one(i) for i in listings]),
        lambda: fetch.run_parallel_quiet([lambda: _views(listings)])[0],
    ])
    found, missing = [], []
    for listing_id, item in zip(listings, items):
        if item is None:
            missing.append(listing_id)
            continue
        item["views"] = (views or {}).get(listing_id)
        found.append(item)
    return {"count": len(found), "listings": found, "not_found": missing}


def similar(listing):
    """The listings the site recommends next to this one ("Das könnte dich
    auch interessieren"), each in full."""
    # the site answers 200 for a deleted listing's page, so existence is
    # checked on the API in parallel
    _, markup = fetch.run_parallel([
        lambda: _ad(listing),
        lambda: fetch.site_html(f"/s-anzeige/{listing}", label="listing page",
                                missing=f"Listing {listing} not found on Kleinanzeigen (deleted or expired)"),
    ])
    ids = [i for i in P.similar_ids(markup) if i != listing][:SIMILAR_MAX]

    def one(listing_id):
        return _ad(listing_id)

    items = [item for item in fetch.run_parallel_quiet([lambda i=i: one(i) for i in ids]) if item]
    views = fetch.run_parallel_quiet([lambda: _views([item["id"] for item in items])])[0] or {}
    for item in items:
        item["views"] = views.get(item["id"])
    return {"listing_id": listing, "count": len(items), "results": items}


def views(listings):
    """View counts of up to 50 listings, in the order asked (the counter
    outlives the listing, so deleted listings still report theirs)."""
    counts = _views(listings)
    return {"count": len(listings),
            "listings": [{"id": listing_id, "link": P.refs.listing_link(listing_id),
                          "views": counts.get(listing_id)} for listing_id in listings]}
