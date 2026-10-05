"""Reference lookups shared by the endpoint modules, memoised in-process:
the category tree, a category's filter metadata, and the resolution of
`category` / `location` / `store` refs (refs.py) into upstream ids.

Everything here raises ValueError with a user-facing message for input the
site does not know (route_glue maps it to HTTP 400)."""
import difflib
import math

from kleinanzeigen import fetch
from kleinanzeigen import parsers as P

TREE_TTL = 24 * 3600
FILTERS_TTL = 24 * 3600
LOCATION_TTL = 7 * 86400
STORE_TTL = 7 * 86400

_tree = fetch.Memo(TREE_TTL, 4)
_filters = fetch.Memo(FILTERS_TTL, 500)
_locations = fetch.Memo(LOCATION_TTL, 20000)
_stores = fetch.Memo(STORE_TTL, 20000)


# ---- categories -------------------------------------------------------------------------

def category_tree():
    """The whole tree (root id 0), parsed."""
    tree = _tree.get("tree")
    if tree is None:
        tree = P.categories(fetch.api("categories.json", label="categories"))
        if not tree:
            raise fetch.KleinanzeigenUpstreamError("categories: empty tree")
        _tree.put("tree", tree)
    return tree


def category_index():
    """{category id: {id, name, slug, parent_id, path}}."""
    index = _tree.get("index")
    if index is None:
        index = {row["id"]: row for row in P.flatten_categories(category_tree())}
        _tree.put("index", index)
    return index


def _norm(value):
    return " ".join((value or "").lower().replace("_", " ").replace("&", " ").replace(",", " ").split())


def category_id(ref):
    """A validated category ref ({"id"} | {"name"}) -> category id (str)."""
    if not ref:
        return None
    index = category_index()
    if ref.get("id") is not None:
        if ref["id"] not in index:
            raise ValueError(f"Unknown category id {ref['id']} (see /kleinanzeigen/categories).")
        return ref["id"]
    want = _norm(ref["name"])
    names = {}
    for row in index.values():
        if row["id"] == "0":
            continue
        for label in (row["name"], row["slug"]):
            names.setdefault(_norm(label), []).append(row["id"])
    hits = names.get(want)
    if hits and len(set(hits)) == 1:
        return hits[0]
    if hits:
        # "Sonstiges" exists under several parents
        raise ValueError(f"Category name '{ref['name']}' is ambiguous — use one of the ids {sorted(set(hits))}.")
    close = difflib.get_close_matches(want, list(names), n=4, cutoff=0.6)
    hint = f" Did you mean: {', '.join(index[names[c][0]]['name'] for c in close)}?" if close else ""
    raise ValueError(f"Unknown category '{ref['name']}'.{hint} See /kleinanzeigen/categories.")


def category_card(cat_id):
    row = category_index().get(cat_id) if cat_id else None
    if not row:
        return None
    return {"id": row["id"], "name": row["name"], "slug": row["slug"], "parent_id": row["parent_id"],
            "path": row["path"]}


def subtree(cat_id):
    """The tree node of one category (None when unknown)."""
    def find(node):
        if node["id"] == cat_id:
            return node
        for child in node.get("children") or []:
            hit = find(child)
            if hit:
                return hit
        return None
    return find(category_tree())


# ---- filters ----------------------------------------------------------------------------

def category_filters(cat_id):
    """Filter metadata of one category (parsed, memoised)."""
    hit = _filters.get(cat_id)
    if hit is None:
        raw = fetch.api(f"attributes/metadata/{cat_id}.json", label="category filters")
        hit = _filters.put(cat_id, P.filters(raw))
    return hit


def category_for_filters(attrs):
    """The category a set of filter keys belongs to: the key prefix
    ('autos' in autos.marke) is the lower-cased category slug."""
    prefixes = {key.split(".", 1)[0] for key in attrs}
    if len(prefixes) != 1:
        raise ValueError("All filters must belong to one category (same prefix before the dot).")
    prefix = next(iter(prefixes))
    hits = [row["id"] for row in category_index().values() if (row["slug"] or "").lower() == prefix]
    if len(hits) == 1:
        return hits[0]
    raise ValueError(f"Pass `category` together with the '{prefix}.*' filters.")


def _check_number(key, value, meta):
    try:
        num = float(value)
    except ValueError:
        raise ValueError(f"Filter {key}: '{value}' is not a number.")
    if not math.isfinite(num):
        raise ValueError(f"Filter {key}: '{value}' is not a number.")
    if meta.get("min") is not None and num < meta["min"]:
        raise ValueError(f"Filter {key}: minimum is {meta['min']}.")
    if meta.get("max") is not None and num > meta["max"]:
        raise ValueError(f"Filter {key}: maximum is {meta['max']}.")
    return value


def validate_filters(cat_id, attrs):
    """{filter key: raw value} -> the same dict with values normalised to
    upstream tokens, checked against the category's metadata: unknown keys,
    unknown choice values and out-of-range numbers are 400s (the API itself
    silently ignores them and returns the unfiltered list)."""
    if not attrs:
        return {}
    metadata = {m["key"]: m for m in category_filters(cat_id)}
    children = {m["child_filter"] for m in metadata.values() if m.get("child_filter")}
    out = {}
    for key, value in attrs.items():
        meta = metadata.get(key)
        if meta is None:
            if key in children:           # e.g. autos.model: its options depend on autos.marke
                out[key] = str(value).strip().lower().replace(" ", "_")
                continue
            known = ", ".join(sorted(k for k, m in metadata.items() if m["is_searchable"])) or "none"
            raise ValueError(f"Unknown filter '{key}' for category {cat_id}. Available: {known}.")
        if not meta["is_searchable"]:
            raise ValueError(f"Filter '{key}' cannot be searched on.")
        value = str(value).strip()
        if meta["is_range"]:
            low, sep, high = value.partition(",")
            if not sep:                    # a single number = exactly that value
                low = high = value
            for bound in (low, high):
                if bound:
                    _check_number(key, bound, meta)
            if low and high and float(low) > float(high):
                raise ValueError(f"Filter {key}: the range minimum is above its maximum.")
            out[key] = f"{low},{high}"
        elif meta["type"] == "boolean":
            low = value.lower()
            if low not in ("true", "false", "1", "0", "yes", "no", "ja", "nein"):
                raise ValueError(f"Filter {key} is a flag: use true or false.")
            out[key] = "true" if low in ("true", "1", "yes", "ja") else "false"
        elif meta["options"]:
            by_token = {}
            for option in meta["options"]:
                by_token[_norm(option["value"])] = option["value"]
                by_token.setdefault(_norm(option["label"]), option["value"])
            token = by_token.get(_norm(value))
            if token is None:
                allowed = ", ".join(o["value"] for o in meta["options"][:80])
                raise ValueError(f"Filter {key}: '{value}' is not one of: {allowed}.")
            out[key] = token
        else:
            out[key] = value
    return out


# ---- locations --------------------------------------------------------------------------

def location_id(ref):
    """A validated location ref ({"id"} | {"postal_code"} | {"name"}) ->
    location id (str). Names / postal codes take the site's best match."""
    if not ref:
        return None
    if ref.get("id") is not None:
        return ref["id"]
    term = ref.get("postal_code") or ref.get("name")
    key = term.lower()
    hit = _locations.get(key)
    if hit is None:
        rows = P.locations(fetch.api("locations.json", {"q": term, "depth": 0}, label="location lookup"))
        if not rows:
            raise ValueError(f"No Kleinanzeigen location matches '{term}' "
                             "(try /kleinanzeigen/locations/search).")
        hit = _locations.put(key, rows[0]["id"])
    return hit


# ---- stores -----------------------------------------------------------------------------

def store_raw(ref):
    """A validated store ref ({"id"} | {"slug"}) -> the raw store payload."""
    if ref.get("id") is not None:
        return fetch.api(f"stores/{ref['id']}.json", label="store",
                         missing=f"Store {ref['id']} not found on Kleinanzeigen")
    slug = ref["slug"]
    try:
        raw = fetch.api("stores.json", {"urlExtension": slug}, label="store",
                        missing=f"Store '{slug}' not found on Kleinanzeigen")
    except fetch.KleinanzeigenBadRequest:
        raise fetch.KleinanzeigenNotFound(f"Store '{slug}' not found on Kleinanzeigen")
    data = P.unwrap(raw)
    if isinstance(data, list):             # the slug lookup answers with a list
        data = next((row for row in data if isinstance(row, dict)), {})
    if not P.text(P.node(data).get("id")):
        raise fetch.KleinanzeigenNotFound(f"Store '{slug}' not found on Kleinanzeigen")
    return data


def store_id(ref):
    """A validated store ref -> store id (memoised for slugs)."""
    if ref.get("id") is not None:
        key = f"id:{ref['id']}"
        if _stores.get(key) is None:
            store_raw(ref)
            _stores.put(key, ref["id"])
        return ref["id"]
    key = ref["slug"].lower()
    hit = _stores.get(key)
    if hit is None:
        hit = _stores.put(key, P.text(P.unwrap(store_raw(ref)).get("id")))
    return hit
