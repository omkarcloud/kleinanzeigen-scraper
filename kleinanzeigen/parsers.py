"""Pure parsers: raw Kleinanzeigen payloads -> the public response shapes.
No I/O here, so everything is testable offline (test_parsers.py).

The app API serialises JAXB objects: every element may arrive wrapped as
{"name", "declaredType", "scope", "value": {...}}, scalars as {"value": x},
namespaced keys as "{http://…/ad/v1}ad", booleans and numbers as strings,
and absent fields as {}. unwrap() flattens all of that first.

Fields intentionally dropped (and why):
  displayoptions.*          UI switches of the app (only offer-allowed and
                            secure-payment-possible carry data -> kept)
  labels                    presentation chips, derived from fields we keep
  tracking, encryptedSellerId, ad-source-id, originId, ad-external-reference-id
                            internal / tracking ids
  financing-*               always empty / false for public listings
  placeholder-image-present, pictures[].viewport, pictures[].tags
                            image-cropping hints for the app
  ad-address.radius         blur radius of the map pin
  link[rel=self*]           API self links
  attribute search-display / fake-sub-category / render flags
                            form-rendering hints
  initials                  first letters of the name we already return
"""
import html
import math
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from kleinanzeigen import refs

IMAGE_RULE_LARGE = "57"       # XXL, the largest rendition the site serves
IMAGE_RULE_THUMB = "2"        # teaser
USER_IMAGE_RULE = "57"

PRICE_TYPES = {"SPECIFIED_AMOUNT": "fixed", "PLEASE_CONTACT": "negotiable", "FREE": "free"}
AD_TYPES = {"OFFERED": "offer", "WANTED": "wanted"}
PROMOTIONS = {"TOPAD": "top_ad", "HIGHLIGHT": "highlight", "HP_GALLERY": "gallery",
              "PRIORITY_AD": "priority", "AD_MULTI_BUMP_UP": "bump_up"}
CARRIERS = {"DHL": "DHL", "HERMES": "Hermes"}
# The site's wording for each badge level (seller pages, 2026-10-05).
BADGE_LABELS = {
    "rating": ("Na ja", "OK", "TOP"),
    "friendliness": ("Freundlich", "Sehr freundlich", "Besonders freundlich"),
    "reliability": ("Zuverlässig", "Sehr zuverlässig", "Besonders zuverlässig"),
}
ATTRIBUTE_TYPES = {"ENUM": "choice", "BOOLEAN": "boolean", "LONG": "number", "DECIMAL": "number",
                   "DATE": "date", "STRING": "text"}


# ---- primitives -------------------------------------------------------------------------

def unwrap(obj):
    """Flatten the JAXB JSON: wrapper objects -> their value, {"value": x}
    -> x, "{namespace}key" -> "key"."""
    if isinstance(obj, dict):
        if "declaredType" in obj and "value" in obj:
            return unwrap(obj["value"])
        if len(obj) == 1 and "value" in obj:
            return unwrap(obj["value"])
        return {key.rsplit("}", 1)[-1]: unwrap(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [unwrap(item) for item in obj]
    return obj


def text(value):
    """Non-empty stripped string, else None ({} / "" / None are all 'absent')."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if "&" in value:
            value = html.unescape(value)      # titles arrive entity-encoded (&#x2F;, &quot;)
        value = value.strip()
        return value or None
    return None


def number(value):
    """int when whole, float otherwise, None when absent / not numeric."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        try:
            num = float(value.strip().replace(",", "."))
        except ValueError:
            return None
    else:
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    return int(num) if num == int(num) else num


def integer(value):
    num = number(value)
    return int(num) if num is not None else None


def boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "ja"):
            return True
        if low in ("false", "0", "nein"):
            return False
    return None


def as_list(value):
    """A JAXB list field: list -> list, one object -> [object], absent -> []."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and value:
        return [value]
    return []


def node(value):
    return value if isinstance(value, dict) else {}


def iso_utc(value):
    """'2026-10-05T11:48:12.000+0200' -> '2026-10-05T09:48:12Z'."""
    raw = text(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw, fmt).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


def iso_date(value):
    """Datetime string -> 'YYYY-MM-DD' in the site's own (German) calendar day."""
    raw = text(value)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw or "")
    return m.group(1) if m else None


def plain_text(value):
    """Description / imprint markup -> text: <br> and block ends become
    newlines, other tags go, entities (&#x2F;) are decoded."""
    raw = value.strip() if isinstance(value, str) else text(value)
    if not raw:
        return None
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"</(p|li|div|ul|ol|h\d)\s*>", "\n", raw, flags=re.I)
    raw = re.sub(r"<li[^>]*>", "• ", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = html.unescape(raw).replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"[ \t]+\n", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip() or None


def image_link(href, rule):
    """Any rendition link of an image -> the rendition `rule` of it."""
    href = text(href)
    if not href:
        return None
    return re.sub(r"\$_(\{imageId\}|\d+)\.", f"$_{rule}.", href)


# ---- listing ----------------------------------------------------------------------------

def _links(ad):
    return {text(node(l).get("rel")): text(node(l).get("href")) for l in as_list(ad.get("link"))}


def price(ad):
    raw = node(ad.get("price"))
    reduction = node(ad.get("price-reduction"))
    kind = PRICE_TYPES.get(text(raw.get("price-type")) or "")
    amount = number(raw.get("amount"))
    original = number(reduction.get("starting-price-in-euro"))
    if not raw and original is None:
        return None
    if amount is None and kind == "free":
        amount = 0
    return {
        "amount": amount,
        "currency": text(raw.get("currency-iso-code") if not isinstance(raw.get("currency-iso-code"), dict)
                         else node(raw.get("currency-iso-code")).get("value")) or "EUR",
        "type": kind,
        "is_negotiable": kind == "negotiable" if kind else None,
        "is_free": kind == "free" if kind else None,
        "original_amount": original,
        "is_reduced": bool(original is not None and amount is not None and original > amount),
    }


def images(ad):
    out = []
    for picture in as_list(node(ad.get("pictures")).get("picture")):
        rels = {text(node(l).get("rel")): text(node(l).get("href")) for l in as_list(node(picture).get("link"))}
        base = rels.get("canonicalUrl") or rels.get("XXL") or rels.get("large") or next(iter(rels.values()), None)
        link = image_link(base, IMAGE_RULE_LARGE)
        if link:
            out.append({"link": link, "thumbnail_link": image_link(base, IMAGE_RULE_THUMB)})
    return out


def _attribute_value(kind, raw, label):
    if kind == "BOOLEAN":
        return boolean(raw)
    if kind in ("LONG", "DECIMAL"):
        num = number(raw)
        return num if num is not None else label
    if kind == "DATE":
        return raw
    return label or raw


def attributes(ad):
    """[{key, name, value, raw_value, unit}] — `value` is typed (number /
    boolean / the site's label), `raw_value` is the token the `filters`
    search param takes."""
    out = []
    for attr in as_list(node(ad.get("attributes")).get("attribute")):
        attr = node(attr)
        key = text(attr.get("name"))
        if not key:
            continue
        kind = text(attr.get("type")) or ""
        values = []
        for item in as_list(attr.get("value")):
            if isinstance(item, dict):
                raw, label = text(item.get("value")), text(item.get("localized-label"))
            else:
                raw, label = text(item), None
            if raw is None and label is None:
                continue
            values.append((_attribute_value(kind, raw, label), raw))
        if not values:
            continue
        multi = len(values) > 1
        out.append({
            "key": key,
            "name": text(attr.get("localized-label")),
            "value": [v for v, _ in values] if multi else values[0][0],
            "raw_value": [r for _, r in values] if multi else values[0][1],
            "unit": text(attr.get("unit")),
        })
    return out


def location(ad):
    address = node(ad.get("ad-address"))
    loc = node(next(iter(as_list(node(ad.get("locations")).get("location"))), None))
    if not address and not loc:
        return None
    regions = as_list(node(loc.get("regions")).get("region"))
    return {
        "id": text(loc.get("id")),
        "name": text(loc.get("localized-name")),
        "postal_code": text(address.get("zip-code")) or _postal_code(loc.get("id-name")),
        "locality": text(address.get("state")),
        "state": text(node(next(iter(regions), None)).get("localized-name")),
        "street": text(address.get("street")),
        "latitude": number(address.get("latitude")) if address else number(loc.get("latitude")),
        "longitude": number(address.get("longitude")) if address else number(loc.get("longitude")),
    }


def category(raw):
    raw = node(raw)
    if not text(raw.get("id")):
        return None
    return {"id": text(raw.get("id")), "name": text(raw.get("localized-name")),
            "slug": text(raw.get("id-name")), "parent_id": text(raw.get("parent-id"))}


def shipping(ad):
    """{is_available, carriers}. Search rows carry no shipping attribute, so
    there the answer comes from the carrier options and the site's
    "Versand möglich" chip."""
    options = [text(node(o).get("id")) for o in as_list(node(ad.get("shipping-options")).get("shipping-option"))]
    carriers = []
    for option in options:
        if not option:
            continue
        carrier = CARRIERS.get(option.split("_")[0].upper(), option.split("_")[0].title())
        if carrier not in carriers:
            carriers.append(carrier)
    available = True if carriers else None
    if available is None:
        for attr in as_list(node(ad.get("attributes")).get("attribute")):
            if (text(node(attr).get("name")) or "").endswith(".versand"):
                first = next(iter(as_list(node(attr).get("value"))), None)
                available = boolean(node(first).get("value") if isinstance(first, dict) else first)
    if available is None and "labels" in ad:
        chips = [text(node(l).get("name")) for l in as_list(node(ad.get("labels")).get("label"))]
        available = "versand" in chips
    return {"is_available": available, "carriers": carriers}


def rating(average, badges):
    """Seller reputation: the 0-1 satisfaction score plus the three site
    badges ({level 0-2, label}); reply rate / speed when the seller has them."""
    levels = {}
    for badge in as_list(badges):
        badge = node(badge)
        name = text(badge.get("name"))
        if name:
            levels[name] = (integer(badge.get("level")), text(badge.get("value")))
    score = number(node(average).get("averageRating")) if isinstance(average, dict) else number(average)

    def badge_block(name):
        if name not in levels or levels[name][0] is None:
            return None
        level = levels[name][0]
        labels = BADGE_LABELS[name]
        return {"level": level, "label": labels[level] if 0 <= level < len(labels) else None}

    reply_rate = levels.get("replyRate", (None, None))[1]
    out = {
        "score": round(score, 2) if score is not None else None,
        "satisfaction": badge_block("rating"),
        "friendliness": badge_block("friendliness"),
        "reliability": badge_block("reliability"),
        "reply_rate_percent": integer((reply_rate or "").rstrip("%")) if reply_rate else None,
        "reply_time": levels.get("replySpeed", (None, None))[1],
    }
    return out, integer(levels.get("followers", (None, None))[1])


def _store_ref(store_id, title, slug):
    store_id = text(store_id)
    if not store_id:
        return None
    slug = text(slug)
    return {"id": store_id, "name": text(title), "slug": slug, "link": refs.store_link(slug)}


def listing_seller(ad):
    user_id = text(ad.get("user-id"))
    reputation, _ = rating(ad.get("user-rating"), node(ad.get("userBadges")).get("badges"))
    has_reputation = any(v is not None for v in reputation.values())
    company = node(ad.get("company"))
    return {
        "id": user_id,
        "name": text(ad.get("contact-name")) or text(company.get("name")),
        "link": refs.seller_link(user_id) if user_id else None,
        "type": (text(ad.get("poster-type")) or "").lower() or None,
        "account_type": (text(ad.get("seller-account-type")) or "").lower() or None,
        "member_since": iso_date(ad.get("user-since-date-time")),
        "phone": text(ad.get("phone")),
        "logo": image_link(company.get("logo"), USER_IMAGE_RULE),
        "rating": reputation if has_reputation else None,
        "store": _store_ref(ad.get("store-id"), ad.get("store-title"), ad.get("store-url-extension")),
    }


def listing(raw):
    """One ad (search row or detail) -> the public listing. Detail-only
    fields (seller name / rating, phone, edit date, legal notice) are null
    on search rows."""
    ad = unwrap(raw)
    ad = node(ad.get("ad")) or node(ad)
    listing_id = text(ad.get("id"))
    if not listing_id:
        return None
    pics = images(ad)
    promotions = []
    for feature in as_list(node(ad.get("features-active")).get("feature-active")):
        name = text(node(feature).get("name"))
        if name:
            promotions.append(PROMOTIONS.get(name, name.lower()))
    options = node(ad.get("displayoptions"))
    media = []
    for item in as_list(node(ad.get("medias")).get("media")):
        link = node(node(item).get("media-link"))
        if text(link.get("href")):
            media.append({"title": text(node(item).get("title")), "link": text(link.get("href")),
                          "type": text(link.get("type"))})
    documents = []
    for doc in as_list(node(ad.get("documents")).get("document")):
        doc = node(doc)
        links = _links(doc)
        link = text(doc.get("href")) or text(doc.get("url")) or next((v for v in links.values() if v), None)
        if link:
            documents.append({"title": text(doc.get("title")) or text(doc.get("name")), "link": link})
    return {
        "id": listing_id,
        "title": text(ad.get("title")),
        "link": _links(ad).get("self-public-website") or refs.listing_link(listing_id),
        "description": plain_text(ad.get("description")),
        "type": AD_TYPES.get(text(ad.get("ad-type")) or ""),
        "status": (text(ad.get("ad-status")) or "").lower() or None,
        "is_buy_now": boolean(node(ad.get("buy-now")).get("selected")),
        "accepts_offers": boolean(options.get("offer-allowed")),
        "has_secure_payment": boolean(options.get("secure-payment-possible")),
        "is_top_ad": "top_ad" in promotions,
        "views": None,
        "image_count": len(pics),
        "price": price(ad),
        "category": category(ad.get("category")),
        "location": location(ad),
        "shipping": shipping(ad),
        "seller": listing_seller(ad),
        "attributes": attributes(ad),
        "images": pics,
        "virtual_tours": media,
        "documents": documents,
        "promotions": promotions,
        "legal_notice": plain_text(ad.get("imprint")),
        "posted_at": iso_utc(ad.get("start-date-time")),
        "updated_at": iso_utc(ad.get("last-user-edit-date")),
    }


def search_page(raw, page, page_size, window):
    """ads.json -> (listings, total count, category histogram [{id, count}])."""
    data = unwrap(raw)
    ads = node(data.get("ads"))
    rows = [item for item in (listing(ad) for ad in as_list(ads.get("ad"))) if item]
    total = integer(node(ads.get("paging")).get("numFound")) or 0
    histogram = []
    for bucket in as_list(node(node(ads.get("ads-search-histograms")).get("ads-category-histogram"))
                          .get("ads-category")):
        bucket = node(bucket)
        count = integer(bucket.get("value"))
        if text(bucket.get("id")) and count:
            histogram.append({"id": text(bucket.get("id")), "count": count})
    histogram.sort(key=lambda b: -b["count"])
    return rows, total, histogram


def pagination(page, page_size, total, window=10000):
    """The block route_glue.paginate lifts into count / total_pages / next.
    The API only serves the first `window` results of any search."""
    reachable = min(total, window)
    return {"page": page, "items_per_page": page_size,
            "total_pages": math.ceil(reachable / page_size) if page_size else 0,
            "total_count": total}


def view_counts(raw):
    """v2/counters/ads/vip -> {listing id: views}."""
    data = unwrap(raw)
    out = {}
    rows = as_list(data.get("counters")) if "counters" in data else [data]
    for row in rows:
        row = node(row)
        if text(row.get("adId")):
            out[text(row.get("adId"))] = integer(row.get("value"))
    return out


# ---- seller / store ---------------------------------------------------------------------

def seller(raw):
    data = unwrap(raw)
    user_id = text(data.get("id"))
    if not user_id:
        return None
    counters = node(data.get("counters"))
    reputation, badge_followers = rating(data.get("userRatings"), node(data.get("userBadges")).get("badges"))
    reply = node(data.get("replyIndicators"))
    if reputation["reply_time"] is None:
        reputation["reply_time"] = text(reply.get("replySpeed"))
    if reputation["reply_rate_percent"] is None and text(reply.get("replyRate")):
        reputation["reply_rate_percent"] = integer(text(reply.get("replyRate")).rstrip("%"))
    brand = node(data.get("bizBranding"))
    slug = text(brand.get("webUrl")).rsplit("/pro/", 1)[-1] if "/pro/" in (text(brand.get("webUrl")) or "") else None
    store = None
    if text(brand.get("id")):
        store = {"id": text(brand.get("id")), "name": text(brand.get("title")), "slug": slug,
                 "link": text(brand.get("webUrl")) or refs.store_link(slug),
                 "logo": image_link(brand.get("logoUrl"), USER_IMAGE_RULE),
                 "contact_person": text(brand.get("contactPerson"))}
    followers = integer(counters.get("followers"))
    return {
        "id": user_id,
        "name": text(data.get("contactName")),
        "link": refs.seller_link(user_id),
        "type": (text(data.get("posterType")) or "").lower() or None,
        "account_type": (text(data.get("sellerAccountType")) or "").lower() or None,
        "member_since": iso_date(data.get("userSince")),
        "has_secure_payment": boolean(data.get("securePayment")),
        "active_listings": integer(counters.get("onlineAds")),
        "total_listings": integer(counters.get("historicalAds")),
        "followers": followers if followers is not None else badge_followers,
        "rating": reputation,
        "store": store,
    }


def store(raw):
    data = unwrap(raw)
    store_id = text(data.get("id"))
    if not store_id:
        return None
    slug = text(data.get("urlExtension"))
    hours = []
    for row in as_list(data.get("openingHours")):
        row = node(row)
        days = (text(row.get("days")) or "").rstrip(":").strip() or None
        slots = [text(h) for h in as_list(row.get("hours")) if text(h)] if isinstance(row.get("hours"), list) \
            else ([text(row.get("hours"))] if text(row.get("hours")) else [])
        if days or slots:
            hours.append({"days": days, "hours": slots})
    phones = [p for p in (text(data.get("phoneNumber")), text(data.get("phoneNumber2"))) if p]
    # locationName is "<Bundesland> - <city>"
    state, sep, city = (text(data.get("locationName")) or "").partition(" - ")
    if not sep:
        state, city = None, state
    return {
        "id": store_id,
        "name": text(data.get("title")),
        "slug": slug,
        "link": text(data.get("webUrl")) or refs.store_link(slug),
        "seller_id": text(data.get("userId")),
        "summary": plain_text(data.get("summary")),
        "description": plain_text(data.get("description")),
        "highlights": [text(u) for u in as_list(data.get("usps")) if text(u)],
        "website": text(data.get("externalUrl")),
        "phones": phones,
        "account_type": (text(data.get("userAccountType")) or "").lower() or None,
        "is_active": boolean(data.get("active")),
        "is_commercial": boolean(data.get("commercial")),
        "has_showcase": boolean(data.get("hasAdShowcase")),
        "logo": image_link(data.get("logoUrl"), USER_IMAGE_RULE),
        "header_image": image_link(data.get("headerImageUrl"), USER_IMAGE_RULE),
        "images": [image_link(u, USER_IMAGE_RULE) for u in as_list(data.get("imageUrls")) if text(u)],
        "address": {
            "street": text(data.get("street")),
            "postal_code": text(data.get("zipCode")),
            "city": city or None,
            "state": state or None,
            "location_id": text(data.get("locationId")),
            "latitude": number(data.get("latitude")),
            "longitude": number(data.get("longitude")),
        },
        "opening_hours": hours,
        "legal_notice": plain_text(data.get("imprint")),
    }


# ---- categories ---------------------------------------------------------------------------

def category_tree(raw_node):
    """One categories.json node -> {id, name, slug, parent_id, link, children}."""
    raw_node = node(raw_node)
    category_id = text(raw_node.get("id"))
    if category_id is None:
        return None
    children = [c for c in (category_tree(child) for child in as_list(raw_node.get("category"))) if c]
    return {"id": category_id, "name": text(raw_node.get("localized-name")),
            "slug": text(raw_node.get("id-name")), "parent_id": text(raw_node.get("parent-id")),
            "link": refs.category_link(category_id) if category_id != "0" else refs.SITE,
            "children": children}


def categories(raw):
    """categories.json -> the root node ('Alle Kategorien', id 0) as a tree."""
    data = unwrap(raw)
    root = next(iter(as_list(node(data.get("categories")).get("category"))), None)
    return category_tree(root)


def flatten_categories(tree, path=()):
    """Tree -> [{id, name, slug, parent_id, path: [names]}] depth-first."""
    out = []
    if not tree:
        return out
    here = path + ((tree["name"],) if tree["id"] != "0" else ())
    out.append({"id": tree["id"], "name": tree["name"], "slug": tree["slug"],
                "parent_id": tree["parent_id"], "path": list(here)})
    for child in tree.get("children") or []:
        out.extend(flatten_categories(child, here))
    return out


def filters(raw):
    """attributes/metadata/<category>.json -> [{key, name, type, is_searchable,
    is_range, is_multi_select, unit, min, max, group, depends_on, options,
    suggested_values}]. `key` + an option `value` (or min-max for a range)
    is what the `filters` search param takes."""
    data = unwrap(raw)
    attrs = as_list(node(data.get("attributes")).get("attribute"))
    parents = {}
    for attr in attrs:
        attr = node(attr)
        if text(attr.get("dependent-name")) and text(attr.get("name")):
            parents[text(attr.get("dependent-name"))] = text(attr.get("name"))
    out = []
    for attr in attrs:
        attr = node(attr)
        key = text(attr.get("name"))
        if not key:
            continue
        kind = text(attr.get("type")) or ""
        options = []
        for option in as_list(attr.get("supported-value")):
            option = node(option)
            if text(option.get("value")) is not None:
                options.append({"value": text(option.get("value")), "label": text(option.get("localized-label"))})
        out.append({
            "key": key,
            "name": text(attr.get("localized-label")),
            "type": ATTRIBUTE_TYPES.get(kind, kind.lower() or None),
            "is_searchable": text(attr.get("search-param")) not in (None, "unsupported"),
            "is_range": text(attr.get("search-style")) == "range",
            "is_multi_select": boolean(attr.get("search-multi-select")),
            "unit": text(attr.get("unit")),
            "min": number(attr.get("min-value")),
            "max": number(attr.get("max-value")),
            "group": text(attr.get("group-localized-label")),
            "depends_on": text(attr.get("parent-name")) or parents.get(key),
            "child_filter": text(attr.get("dependent-name")),
            "options": options,
            "suggested_values": [number(v) if number(v) is not None else text(v)
                                 for v in as_list(attr.get("suggested-value")) if text(v)],
        })
    return out


# ---- locations ----------------------------------------------------------------------------

def _postal_code(value):
    value = text(value)
    return value if value and re.fullmatch(r"\d{5}", value) else None


def location_node(raw_node, with_children=True):
    raw_node = node(raw_node)
    location_id = text(raw_node.get("id"))
    if location_id is None:
        return None
    regions = as_list(node(raw_node.get("regions")).get("region"))
    out = {
        "id": location_id,
        "name": text(raw_node.get("localized-name")),
        "link": refs.location_link(location_id),
        "postal_code": _postal_code(raw_node.get("id-name")),
        "state": text(node(next(iter(regions), None)).get("localized-name")),
        "latitude": number(raw_node.get("latitude")),
        "longitude": number(raw_node.get("longitude")),
        "radius_km": number(raw_node.get("radius")),
        "parent_id": text(raw_node.get("parent-id")),
        "children_count": integer(raw_node.get("children-count")),
    }
    if with_children:
        out["children"] = [c for c in (location_node(child) for child in as_list(raw_node.get("location"))) if c]
    return out


def locations(raw, with_children=False):
    """locations.json -> [location nodes] in the site's ranking."""
    data = unwrap(raw)
    rows = as_list(node(data.get("locations")).get("location"))
    return [n for n in (location_node(row, with_children) for row in rows) if n]


def location_detail(raw):
    data = unwrap(raw)
    return location_node(data.get("location"), True)


# ---- site page / suggestions ----------------------------------------------------------------

def similar_ids(markup):
    """Listing ids of the ad page's 'Das könnte dich auch interessieren' block."""
    doc = BeautifulSoup(markup or "", "html.parser")
    ids = []
    for article in doc.select("article.aditem[data-adid]"):
        ad_id = (article.get("data-adid") or "").strip()
        if ad_id.isdigit() and ad_id not in ids:
            ids.append(ad_id)
    return ids


def suggestions(raw):
    """Algolia answer -> [{text, search_frequency, categories: [{id, name}]}]."""
    out = []
    for hit in as_list(node(raw).get("hits")):
        hit = node(hit)
        completion = text(hit.get("completion"))
        if not completion:
            continue
        cats = []
        for cat in as_list(hit.get("categories")):
            cat = node(cat)
            cat_id = text(cat.get("id"))
            if cat_id and cat_id != "0":
                cats.append({"id": cat_id, "name": text(node(cat.get("localizedNames")).get("de_DE"))})
        out.append({"text": completion, "search_frequency": integer(hit.get("frequency")), "categories": cats})
    return out
