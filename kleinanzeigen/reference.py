"""/kleinanzeigen/categories* and /kleinanzeigen/locations/*: the reference
data the search params take — category ids and their filters, location ids."""
from kleinanzeigen import fetch, lookup
from kleinanzeigen import parsers as P


def categories(category=None):
    """The category tree (all ~160 categories), or the subtree under one
    category. Each node's `id` is what `category` params take."""
    if category:
        category_id = lookup.category_id(category)
        node = lookup.subtree(category_id)
        card = lookup.category_card(category_id) or {}
        return {"category": {**node, "path": card.get("path")}}
    tree = lookup.category_tree()
    return {"count": len(lookup.category_index()) - 1, "categories": tree["children"]}


def category_filters(category):
    """The filters a category supports (car make / mileage / first
    registration, flat size / rooms, condition, …): each `key` with its
    options or numeric range is what the `filters` search param takes."""
    category_id = lookup.category_id(category)
    items = lookup.category_filters(category_id)
    return {"category": lookup.category_card(category_id),
            "count": len(items), "filters": items}


def location_search(query, limit=20):
    """Cities, districts and postal codes matching a name or postal code,
    best match first. Each `id` is what `location` params take (as id:<id>)."""
    rows = P.locations(fetch.api("locations.json", {"q": query, "depth": 0}, label="location search"))
    return {"query": query, "count": len(rows[:limit]), "locations": rows[:limit]}


def location_details(location):
    """One location with its coordinates, default search radius, parent
    and direct children (a city's districts, a district's postal codes)."""
    location_id = lookup.location_id(location)
    raw = fetch.api(f"locations/{location_id}.json", {"depth": 1}, label="location",
                    missing=f"Location {location_id} not found on Kleinanzeigen")
    item = P.location_detail(raw)
    if not item:
        raise fetch.KleinanzeigenNotFound(f"Location {location_id} not found on Kleinanzeigen")
    for child in item.get("children") or []:
        child.pop("children", None)
    return item


def location_nearest(latitude, longitude):
    """The Kleinanzeigen location (postal-code area) a coordinate falls in."""
    rows = P.locations(fetch.api("locations.json", {"latitude": latitude, "longitude": longitude},
                                 label="nearest location"))
    if not rows:
        raise fetch.KleinanzeigenNotFound("No Kleinanzeigen location at these coordinates (outside Germany?)")
    return rows[0]


def top_locations():
    """The big cities the site lists as top locations."""
    rows = P.locations(fetch.api("locations/top-locations.json", {"depth": 0}, label="top locations"))
    return {"count": len(rows), "locations": rows}
