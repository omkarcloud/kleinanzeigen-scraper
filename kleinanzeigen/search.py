"""/kleinanzeigen/search and /kleinanzeigen/search/suggestions, plus
run_search(), the one search core every listing list goes through (keyword
search, a seller's or a store's inventory, the cars / real-estate / jobs
presets).

Search runs on the app API's ads.json: up to 100 results per page, the
first 10,000 results of any search reachable (the site itself stops at
50 pages x 25)."""
from cache import DontCache

from kleinanzeigen import fetch, lookup
from kleinanzeigen import parsers as P

WINDOW = 10000                      # ads.json serves offsets below this
SORTS = {"newest": "DATE_DESCENDING", "price_asc": "PRICE_ASCENDING",
         "price_desc": "PRICE_DESCENDING", "distance": "DISTANCE_ASCENDING"}
SELLER_TYPES = {"private": "PRIVATE", "commercial": "COMMERCIAL"}
AD_TYPES = {"offer": "OFFERED", "wanted": "WANTED"}


def _flag(value):
    return "true" if value else None


def run_search(*, query=None, category_id=None, location_id=None, radius=None, latitude=None,
               longitude=None, min_price=None, max_price=None, free_only=None, sort="newest",
               seller_type=None, ad_type=None, has_shipping=None, has_photos=None, buy_now_only=None,
               include_top_ads=None, attrs=None, user_id=None, store_id=None, page=1, page_size=25,
               label="search"):
    """One ads.json page -> {"results", "category_counts", "pagination"}.
    Everything arrives resolved (ids, upstream attribute tokens)."""
    if page * page_size > WINDOW:
        raise ValueError(f"Kleinanzeigen serves the first {WINDOW:,} results of a search: "
                         f"page x page_size must stay at or below {WINDOW}.")
    if (latitude is None) != (longitude is None):
        raise ValueError("Pass latitude and longitude together.")
    has_place = location_id is not None or latitude is not None
    if radius is not None and not has_place:
        raise ValueError("radius needs a location (or latitude + longitude).")
    if sort == "distance" and not has_place:
        raise ValueError("sort=distance needs a location (or latitude + longitude).")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise ValueError("min_price is above max_price.")
    if free_only:
        min_price, max_price = 0, 0
    if latitude is not None and radius is None:
        radius = 10                 # coordinates without a distance match nothing
    params = {
        "q": query,
        "categoryId": category_id,
        "locationId": location_id,
        "distance": P.number(radius),
        "latitude": latitude,
        "longitude": longitude,
        "minPrice": P.number(min_price),
        "maxPrice": P.number(max_price),
        "adType": AD_TYPES.get(ad_type),
        "posterType": SELLER_TYPES.get(seller_type),
        "sortType": SORTS.get(sort) if sort != "newest" else None,
        "pictureRequired": _flag(has_photos),
        "buyNowOnly": _flag(buy_now_only),
        "shippable": _flag(has_shipping),
        "includeTopAds": _flag(include_top_ads),
        "userIds": user_id,
        "storeIds": store_id,
        "page": page - 1,
        "size": page_size,
    }
    for key, value in (attrs or {}).items():
        params[f"attr[{key}]"] = value
    raw = fetch.search(params, label=label)
    rows, total, histogram = P.search_page(raw, page, page_size, WINDOW)
    return {"results": rows, "category_counts": histogram,
            "pagination": P.pagination(page, page_size, total, WINDOW)}


def keep_fresh_if_empty(out):
    """An empty page is returned but not cached: the API intermittently
    reports no results for a search that has some (see fetch.search)."""
    return out if out.get("results") else DontCache(out)


def name_categories(histogram):
    """Add each bucket's name / path from the (memoised) category tree."""
    try:
        index = lookup.category_index()
    except Exception:
        index = {}
    return [{"id": b["id"], "name": (index.get(b["id"]) or {}).get("name"), "count": b["count"]}
            for b in histogram]


def resolve_filters(category_id, filters):
    """(category id | None, {key: raw value} | None) -> (category id,
    validated upstream attrs). Filters without a category pick theirs from
    the key prefix."""
    if not filters:
        return category_id, {}
    if category_id is None:
        category_id = lookup.category_for_filters(filters)
    return category_id, lookup.validate_filters(category_id, filters)


def search(query=None, category=None, location=None, radius=None, latitude=None, longitude=None,
           min_price=None, max_price=None, free_only=None, sort=None, seller_type=None, ad_type=None,
           has_shipping=None, has_photos=None, buy_now_only=None, include_top_ads=None, filters=None,
           page=None, page_size=25):
    """Listings matching keywords and / or filters. `query` also takes a
    kleinanzeigen.de search link: everything the link encodes (keyword,
    category, location, radius, price range, seller type, filters, page) is
    the starting point, and any param passed explicitly overrides it."""
    link = dict(query or {})
    keywords = link.pop("query", None)
    category_id = lookup.category_id(category) if category else link.get("category_id")
    location_id = lookup.location_id(location) if location else link.get("location_id")
    attrs = dict(link.get("filters") or {})
    attrs.update(filters or {})
    category_id, attrs = resolve_filters(category_id, attrs)

    def pick(value, key):
        return value if value is not None else link.get(key)

    page = page or link.get("page") or 1
    sort = sort or link.get("sort") or "newest"
    result = run_search(
        query=keywords, category_id=category_id, location_id=location_id,
        radius=pick(radius, "radius"), latitude=latitude, longitude=longitude,
        min_price=pick(min_price, "min_price"), max_price=pick(max_price, "max_price"),
        free_only=free_only, sort=sort, seller_type=pick(seller_type, "seller_type"),
        ad_type=pick(ad_type, "ad_type"), has_shipping=pick(has_shipping, "has_shipping"),
        has_photos=has_photos, buy_now_only=pick(buy_now_only, "buy_now_only"),
        include_top_ads=include_top_ads, attrs=attrs, page=page, page_size=page_size)
    return keep_fresh_if_empty({
        "query": keywords,
        "category": lookup.category_card(category_id) if category_id else None,
        "location_id": location_id,
        "sort": sort,
        "results": result["results"],
        "category_counts": name_categories(result["category_counts"]),
        "pagination": result["pagination"],
    })


def suggestions(query, limit=10):
    """Search-box completions for a partial keyword, most searched first,
    each with the categories the site would offer it in."""
    return {"query": query, "suggestions": P.suggestions(fetch.suggest(query, limit))}
