"""Reference parsing for /kleinanzeigen/* params: ONE param per input, each
accepting a bare id OR a kleinanzeigen.de link (tripadvisor QueryOrLinkField
convention). Every resolver raises ValueError with a user-facing message.

  listing    3439651996 | https://www.kleinanzeigen.de/s-anzeige/<slug>/3439651996-217-3377
  seller     133032271 | https://www.kleinanzeigen.de/s-bestandsliste.html?userId=133032271
  store      202135 | holz--naturstein--historische-baustoffe | https://www.kleinanzeigen.de/pro/<slug>
  category   217 | Fahrräder & Zubehör | Fahrraeder | any category / search link (…/c217…)
  location   Berlin | 10115 (postal code) | id:3331 | any search link (…l3331…)
  query      free text | a search link — its keyword, category, location,
             radius, price range and filters are read from the link
"""
import re
from urllib.parse import parse_qs, unquote, urlparse

SITE = "https://www.kleinanzeigen.de"
HOSTS = ("kleinanzeigen.de", "ebay-kleinanzeigen.de")

_SEARCH_CODE = re.compile(r"^(k0)?(?:c(\d+))?(?:l(\d+))?(?:r(\d+))?$")
_ATTR_SUFFIX = re.compile(r"_(s|i|d|b|l)$")


def is_link(value):
    return bool(re.match(r"^(https?:)?//", value or "", re.I)) or (value or "").lower().startswith("www.")


def _parsed(value):
    """A kleinanzeigen.de link -> ParseResult, else ValueError."""
    url = value.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in HOSTS):
        raise ValueError("Must be a kleinanzeigen.de link.")
    return parsed


# ---- links ------------------------------------------------------------------------------

def listing_link(listing_id):
    return f"{SITE}/s-anzeige/{listing_id}"


def seller_link(user_id):
    return f"{SITE}/s-bestandsliste.html?userId={user_id}"


def store_link(slug):
    return f"{SITE}/pro/{slug}" if slug else None


def category_link(category_id):
    return f"{SITE}/s-kategorie/c{category_id}"


def location_link(location_id):
    return f"{SITE}/s-ort/l{location_id}"


# ---- listing / seller / store -------------------------------------------------------------

def resolve_listing(value):
    """-> listing id (str)."""
    value = (value or "").strip()
    if value.isdigit():
        return value
    if is_link(value):
        parsed = _parsed(value)
        m = re.search(r"/s-anzeige/(?:[^/]+/)?(\d+)(?:-\d+)*/?$", parsed.path)
        if m:
            return m.group(1)
        ad_id = parse_qs(parsed.query).get("adId")
        if ad_id and ad_id[0].isdigit():
            return ad_id[0]
        raise ValueError("Not a listing link (expected …/s-anzeige/<title>/<id>-…).")
    raise ValueError("Must be a listing id (digits) or a kleinanzeigen.de listing link.")


def resolve_seller(value):
    """-> user id (str)."""
    value = (value or "").strip()
    if value.isdigit():
        return value
    if is_link(value):
        parsed = _parsed(value)
        user_id = parse_qs(parsed.query).get("userId")
        if user_id and user_id[0].isdigit():
            return user_id[0]
        raise ValueError("Not a seller link (expected …/s-bestandsliste.html?userId=<id>). "
                         "For a /pro/ store page use the stores endpoints.")
    raise ValueError("Must be a seller (user) id or a kleinanzeigen.de seller link.")


def resolve_store(value):
    """-> {"id": str} | {"slug": str}."""
    value = (value or "").strip()
    if value.isdigit():
        return {"id": value}
    if is_link(value):
        parsed = _parsed(value)
        m = re.search(r"/pro/([^/?#]+)", parsed.path)
        if not m:
            raise ValueError("Not a store link (expected …/pro/<store-name>).")
        return {"slug": unquote(m.group(1))}
    if re.fullmatch(r"[\w.\-]+", value, re.UNICODE):
        return {"slug": value}
    raise ValueError("Must be a store id, a store slug or a kleinanzeigen.de/pro/ link.")


# ---- category / location ------------------------------------------------------------------

def _search_code(path):
    """The trailing search code of a site path ('k0c217l3331r50+attr:…') ->
    (has_keyword, category id, location id, radius, attribute suffix) or None."""
    last = unquote(path.rstrip("/").rsplit("/", 1)[-1]).replace(" ", "+")
    code, _, suffix = last.partition("+")
    m = _SEARCH_CODE.match(code)
    if not m or not code:
        return None
    return bool(m.group(1)), m.group(2), m.group(3), m.group(4), suffix


def resolve_category(value):
    """-> {"id": str} | {"name": str} (a name is matched against the tree later)."""
    value = (value or "").strip()
    if value.isdigit():
        return {"id": value}
    if is_link(value):
        parsed = _parsed(value)
        code = _search_code(parsed.path)
        if code and code[1]:
            return {"id": code[1]}
        category_id = parse_qs(parsed.query).get("categoryId")
        if category_id and category_id[0].isdigit():
            return {"id": category_id[0]}
        raise ValueError("That link carries no category (expected …/c<id>).")
    return {"name": value}


def resolve_location(value):
    """-> {"id": str} | {"postal_code": str} | {"name": str}. A bare 5-digit
    number is a postal code; write a location id as id:3331 (or l3331)."""
    value = (value or "").strip()
    m = re.fullmatch(r"(?:id:|l)(\d+)", value, re.I)
    if m:
        return {"id": m.group(1)}
    if re.fullmatch(r"\d{5}", value):
        return {"postal_code": value}
    if value.isdigit():
        return {"id": value}
    if is_link(value):
        parsed = _parsed(value)
        code = _search_code(parsed.path)
        if code and code[2]:
            return {"id": code[2]}
        location_id = parse_qs(parsed.query).get("locationId")
        if location_id and location_id[0].isdigit():
            return {"id": location_id[0]}
        raise ValueError("That link carries no location (expected …/l<id>).")
    return {"name": value}


# ---- attribute filters ----------------------------------------------------------------------

def parse_filters(value):
    """'autos.marke:bmw,autos.ez:2015-2020,autos.navi:true' ->
    {'autos.marke': 'bmw', 'autos.ez': '2015,2020', 'autos.navi': 'true'}.
    A range is min-max with either side optional (2015- / -2020). The site's
    own URL spelling (autos.marke_s:bmw, autos.ez_i:2015,2020 joined by +)
    is accepted too."""
    out = {}
    if not value:
        return out
    text = value.strip().replace("+", ";")
    # a comma inside the site's range spelling (ez_i:2015,2020) is not a separator
    parts = re.split(r"[;]|,(?=\s*[\w.]+\s*:)", text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        key, sep, val = part.partition(":")
        key, val = key.strip().lower(), val.strip()
        if not sep or not key or val == "":
            raise ValueError(f"'{part}' is not <filter>:<value> (e.g. autos.marke:bmw, autos.ez:2015-2020).")
        key = _ATTR_SUFFIX.sub("", key)
        if not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_.]+", key):
            raise ValueError(f"'{key}' is not a filter name (see /kleinanzeigen/categories/filters).")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)?\s*-\s*(\d+(?:\.\d+)?)?", val)
        if m and (m.group(1) or m.group(2)):
            val = f"{m.group(1) or ''},{m.group(2) or ''}"
        out[key] = val
    return out


# ---- search links -------------------------------------------------------------------------

_SITE_SORTS = {"preis": "price_asc", "preis-absteigend": "price_desc", "entfernung": "distance",
               "SORTING_DATE": "newest", "PRICE_AMOUNT": "price_asc"}


def parse_search_link(value):
    """A kleinanzeigen.de search link -> the search params it encodes:
    {query, category_id, location_id, radius, min_price, max_price, sort,
    seller_type, ad_type, has_shipping, buy_now_only, page, filters}. Both
    the path form (/s-autos/berlin/preis:100:500/bmw/k0c216l3331r50+autos.marke_s:bmw)
    and the form-post form (/s-suchanfrage.html?keywords=…) are read."""
    parsed = _parsed(value)
    out = {}
    query = parse_qs(parsed.query)

    def first(key):
        vals = query.get(key)
        return vals[0].strip() if vals and vals[0].strip() else None

    if first("keywords"):
        out["query"] = first("keywords")
    for src, dst in (("categoryId", "category_id"), ("locationId", "location_id")):
        if first(src) and first(src).isdigit():
            out[dst] = first(src)
    for src, dst in (("radius", "radius"), ("minPrice", "min_price"), ("maxPrice", "max_price")):
        try:
            if first(src) is not None:
                out[dst] = float(first(src))
        except ValueError:
            pass
    if first("sortingField") in _SITE_SORTS:
        out["sort"] = _SITE_SORTS[first("sortingField")]

    segs = [unquote(s) for s in parsed.path.split("/") if s]
    if not segs or not segs[0].startswith("s-") or segs[0] in ("s-anzeige", "s-bestandsliste.html"):
        if out:
            return out
        raise ValueError("Not a search link (expected …/s-<…>/k0… or …/s-suchanfrage.html?keywords=…).")
    code = _search_code(parsed.path)
    body = segs[:-1] if code else segs
    if body:
        body[0] = body[0][2:]            # drop the "s-" prefix
    if code:
        has_keyword, category_id, location_id, radius, suffix = code
        if category_id:
            out["category_id"] = category_id
        if location_id:
            out["location_id"] = location_id
        if radius:
            out["radius"] = float(radius)
        if suffix:
            filters = {k: v for k, v in parse_filters(suffix).items() if not k.startswith("global.")}
            if filters:
                out["filters"] = filters
        plain = [s for s in body if ":" not in s]
        if has_keyword and plain:
            out["query"] = plain[-1].replace("-", " ").strip()
    for seg in body:
        key, sep, val = seg.partition(":")
        if not sep:
            continue
        if key == "seite" and val.isdigit():
            out["page"] = int(val)
        elif key == "preis":
            low, _, high = val.partition(":")
            if low.isdigit():
                out["min_price"] = float(low)
            if high.isdigit():
                out["max_price"] = float(high)
        elif key == "sortierung" and val in _SITE_SORTS:
            out["sort"] = _SITE_SORTS[val]
        elif key == "anbieter" and val in ("privat", "gewerblich"):
            out["seller_type"] = "private" if val == "privat" else "commercial"
        elif key == "anzeige" and val in ("angebote", "gesuche"):
            out["ad_type"] = "offer" if val == "angebote" else "wanted"
        elif key == "versand" and val == "ja":
            out["has_shipping"] = True
        elif key == "direktkaufen" and val == "aktiv":
            out["buy_now_only"] = True
    if not out:
        raise ValueError("That link carries no search (no keyword, category, location or filter).")
    return out


def resolve_query(value):
    """Free text -> {"query": text}; a search link -> its parsed params."""
    value = " ".join((value or "").split())
    if is_link(value):
        return parse_search_link(value)
    return {"query": value}
