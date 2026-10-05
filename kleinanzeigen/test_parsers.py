"""Offline tests: parsers on trimmed real payloads (fixtures/, captured
2026-10-05), ref resolvers and request schemas. No network.

    python -m pytest kleinanzeigen/test_parsers.py -q
"""
import json
import os

import pytest

from kleinanzeigen import parsers as P
from kleinanzeigen import refs
from kleinanzeigen import schemas as S
from schema_fields import load_query

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f) if name.endswith(".json") else f.read()


# ---- primitives -------------------------------------------------------------------------

def test_unwrap_flattens_jaxb():
    raw = {"{http://x/ad/v1}ad": {"name": "n", "declaredType": "t", "scope": "s",
                                  "value": {"id": "1", "title": {"value": "T"}, "phone": {}}}}
    assert P.unwrap(raw) == {"ad": {"id": "1", "title": "T", "phone": {}}}


def test_primitives():
    assert P.text({}) is None and P.text("  ") is None and P.text("a &#x2F; b") == "a / b"
    assert P.number("150000") == 150000 and P.number("65.5") == 65.5 and P.number({}) is None
    assert P.boolean("true") is True and P.boolean("nein") is False and P.boolean({}) is None
    assert P.iso_utc("2026-10-05T11:48:12.000+0200") == "2026-10-05T09:48:12Z"
    assert P.iso_utc("2025-12-16T08:18:47.000+0100") == "2025-12-16T07:18:47Z"
    assert P.iso_date("2023-08-19T17:08:19.000+0200") == "2023-08-19"
    assert P.plain_text("a<br />b&#x2F;c<b>d</b>") == "a\nb/cd"
    assert P.image_link("https://img/x?rule=$_{imageId}.JPG", "57") == "https://img/x?rule=$_57.JPG"
    assert P.image_link("https://img/x?rule=$_2.AUTO", "57") == "https://img/x?rule=$_57.AUTO"


# ---- listing ----------------------------------------------------------------------------

def test_listing_detail():
    item = P.listing(fixture("listing.json"))
    assert item["id"] == "3439651996"
    assert item["link"].startswith("https://www.kleinanzeigen.de/s-anzeige/") and item["link"].endswith("3439651996-217-3377")
    assert item["type"] == "offer" and item["status"] == "active"
    assert item["price"] == {"amount": 14, "currency": "EUR", "type": "fixed", "is_negotiable": False,
                             "is_free": False, "original_amount": None, "is_reduced": False}
    assert item["category"] == {"id": "217", "name": "Fahrräder & Zubehör", "slug": "Fahrraeder", "parent_id": "210"}
    loc = item["location"]
    assert loc["postal_code"] == "10961" and loc["state"] == "Berlin" and loc["locality"] == "Kreuzberg"
    assert isinstance(loc["latitude"], float) and isinstance(loc["longitude"], float)
    assert item["shipping"] == {"is_available": False, "carriers": []}
    seller = item["seller"]
    assert seller["id"] == "133032271" and seller["type"] == "commercial" and seller["member_since"] == "2023-08-19"
    assert seller["rating"]["satisfaction"] == {"level": 2, "label": "TOP"}
    assert seller["rating"]["friendliness"]["label"] == "Freundlich"
    assert {"key": "fahrraeder.art", "name": "Art", "value": "Damen", "raw_value": "damen", "unit": None} in item["attributes"]
    assert item["image_count"] == len(item["images"]) > 0
    assert "$_57." in item["images"][0]["link"] and "$_2." in item["images"][0]["thumbnail_link"]
    assert item["is_top_ad"] is True and "top_ad" in item["promotions"]
    assert "&#x2F;" not in item["description"] and "https://recyclies.com" in item["description"]
    assert item["legal_notice"] and "\r" not in item["legal_notice"]
    assert item["posted_at"].endswith("Z") and item["updated_at"].endswith("Z")
    assert item["views"] is None            # filled by the endpoint


def test_search_page():
    rows, total, histogram = P.search_page(fixture("search.json"), 1, 25, 10000)
    assert len(rows) == 2 and total > 1000
    store_row, plain_row = rows
    assert store_row["seller"]["store"]["id"] and store_row["seller"]["store"]["link"].startswith("https://www.kleinanzeigen.de/pro/")
    assert plain_row["seller"]["store"] is None
    for row in rows:
        assert row["category"]["id"] == "216" and row["seller"]["type"] == "commercial"
        keys = {a["key"] for a in row["attributes"]}
        assert "autos.marke" in keys
        km = next((a for a in row["attributes"] if a["key"] == "autos.km"), None)
        if km:
            assert isinstance(km["value"], int) and km["unit"] == "km"
        assert row["shipping"]["is_available"] is False      # search rows: no chip -> not shippable
    assert all(set(b) == {"id", "count"} for b in histogram)


def test_free_listing_price():
    rows, _, _ = P.search_page(fixture("search_free.json"), 1, 1, 10000)
    assert rows[0]["price"]["type"] == "free" and rows[0]["price"]["is_free"] is True
    assert rows[0]["price"]["amount"] == 0


def test_listing_partial_payload_never_crashes():
    assert P.listing({}) is None
    assert P.listing({"id": "5"})["price"] is None
    item = P.listing({"ad": {"id": "7", "price": {"amount": {}}, "attributes": {"attribute": {"name": "a.b"}},
                             "pictures": {}, "locations": {}, "category": {}}})
    assert item["id"] == "7" and item["attributes"] == [] and item["images"] == [] and item["category"] is None


def test_pagination_window():
    assert P.pagination(1, 25, 155000)["total_pages"] == 400
    assert P.pagination(1, 100, 250) == {"page": 1, "items_per_page": 100, "total_pages": 3, "total_count": 250}
    assert P.pagination(1, 25, 0)["total_pages"] == 0


def test_view_counts():
    assert P.view_counts({"adId": "5", "value": 7}) == {"5": 7}
    counts = P.view_counts(fixture("views.json"))
    assert counts["3439651996"] > 1000


# ---- seller / store ---------------------------------------------------------------------

def test_seller():
    item = P.seller(fixture("seller.json"))
    assert item["id"] == "131267961" and item["type"] == "private"
    assert item["link"].endswith("userId=131267961")
    assert item["rating"]["reply_rate_percent"] == 80 and item["rating"]["reply_time"] == "10min"
    assert 0 <= item["rating"]["score"] <= 1
    assert isinstance(item["followers"], int) and isinstance(item["total_listings"], int)
    assert item["store"] is None


def test_seller_with_store():
    item = P.seller(fixture("seller_store.json"))
    assert item["account_type"] == "commercial_propremium"
    assert item["store"]["id"] == "202135" and item["store"]["slug"] == "holz--naturstein--historische-baustoffe"
    assert "$_57." in item["store"]["logo"]


def test_store():
    item = P.store(fixture("store.json"))
    assert item["id"] == "202135" and item["seller_id"] == "158553607"
    assert item["address"]["city"] == "Ahaus" and item["address"]["state"] == "Nordrhein-Westfalen"
    assert item["opening_hours"][0] == {"days": "Mo - Fr", "hours": ["09:00 - 17:30"]}
    assert item["highlights"] and item["phones"] and "{imageId}" not in "".join(item["images"] + [item["logo"]])
    assert P.store({}) is None


# ---- reference data ---------------------------------------------------------------------

def test_categories():
    tree = P.categories(fixture("categories.json"))
    assert tree["id"] == "0" and [c["id"] for c in tree["children"]] == ["210", "195"]
    autos = tree["children"][0]["children"][0]
    assert autos == {"id": "216", "name": "Autos", "slug": "Autos", "parent_id": "210",
                     "link": "https://www.kleinanzeigen.de/s-kategorie/c216", "children": []}
    flat = {row["id"]: row for row in P.flatten_categories(tree)}
    assert flat["216"]["path"] == ["Auto, Rad & Boot", "Autos"] and flat["0"]["path"] == []


def test_filters():
    items = {f["key"]: f for f in P.filters(fixture("filters.json"))}
    make = items["autos.marke"]
    assert make["type"] == "choice" and make["child_filter"] == "autos.model" and make["is_searchable"]
    assert {"value": "bmw", "label": "BMW"} in make["options"]
    km = items["autos.km"]
    assert km["is_range"] and km["unit"] == "km" and km["min"] == 0 and km["max"] == 2000000
    assert 100000 in km["suggested_values"]
    assert items["autos.navi"]["type"] == "boolean" and items["autos.navi"]["group"] == "Innenausstattung"
    assert items["autos.ezm"]["is_searchable"] is False


def test_locations():
    rows = P.locations(fixture("locations.json"))
    assert rows[0]["id"] == "929" and rows[0]["state"] == "Nordrhein-Westfalen" and "children" not in rows[0]
    assert rows[0]["link"] == "https://www.kleinanzeigen.de/s-ort/l929"
    detail = P.location_detail(fixture("location.json"))
    assert detail["id"] == "929" and detail["parent_id"] == "928" and len(detail["children"]) == 2


def test_similar_ids_and_suggestions():
    ids = P.similar_ids(fixture("listing_page.html"))
    assert len(ids) == 3 and all(i.isdigit() for i in ids)
    hits = P.suggestions(fixture("suggestions.json"))
    assert hits[0]["text"] == "fahrrad" and hits[0]["search_frequency"] > 1000
    assert all(c["id"] != "0" for c in hits[0]["categories"])
    assert P.suggestions({}) == [] and P.similar_ids("") == []


# ---- refs -------------------------------------------------------------------------------

def test_listing_seller_store_refs():
    assert refs.resolve_listing("3439651996") == "3439651996"
    assert refs.resolve_listing("https://www.kleinanzeigen.de/s-anzeige/fahrrad-abo/3439651996-217-3377") == "3439651996"
    assert refs.resolve_listing("https://www.kleinanzeigen.de/s-anzeige/3439651996") == "3439651996"
    assert refs.resolve_seller("https://www.kleinanzeigen.de/s-bestandsliste.html?userId=133032271") == "133032271"
    assert refs.resolve_store("202135") == {"id": "202135"}
    assert refs.resolve_store("https://www.kleinanzeigen.de/pro/Autohaus-Moeller-GmbH-Co-KG") == {"slug": "Autohaus-Moeller-GmbH-Co-KG"}
    assert refs.resolve_store("isi-makler") == {"slug": "isi-makler"}
    for bad in ("abc", "https://www.ebay.de/itm/1", "https://www.kleinanzeigen.de/pro/x"):
        with pytest.raises(ValueError):
            refs.resolve_listing(bad)
    with pytest.raises(ValueError):
        refs.resolve_seller("https://www.kleinanzeigen.de/pro/x")


def test_category_location_refs():
    assert refs.resolve_category("217") == {"id": "217"}
    assert refs.resolve_category("Fahrräder & Zubehör") == {"name": "Fahrräder & Zubehör"}
    assert refs.resolve_category("https://www.kleinanzeigen.de/s-fahrraeder/berlin/c217l3331") == {"id": "217"}
    assert refs.resolve_location("Berlin") == {"name": "Berlin"}
    assert refs.resolve_location("10115") == {"postal_code": "10115"}
    assert refs.resolve_location("id:16777") == {"id": "16777"}
    assert refs.resolve_location("l3331") == {"id": "3331"}
    assert refs.resolve_location("929") == {"id": "929"}
    assert refs.resolve_location("https://www.kleinanzeigen.de/s-berlin/fahrrad/k0l3331") == {"id": "3331"}


def test_parse_filters():
    assert refs.parse_filters("autos.marke:bmw,autos.ez:2015-2020, autos.navi:true") == {
        "autos.marke": "bmw", "autos.ez": "2015,2020", "autos.navi": "true"}
    assert refs.parse_filters("autos.km:-100000") == {"autos.km": ",100000"}
    assert refs.parse_filters("autos.ez:2015-") == {"autos.ez": "2015,"}
    assert refs.parse_filters("autos.marke_s:bmw+autos.ez_i:2015,2020") == {"autos.marke": "bmw", "autos.ez": "2015,2020"}
    assert refs.parse_filters("autos.anzahl_tueren:2_3") == {"autos.anzahl_tueren": "2_3"}
    for bad in ("bmw", "marke:bmw", "autos.marke:"):
        with pytest.raises(ValueError):
            refs.parse_filters(bad)


def test_parse_search_link():
    link = ("https://www.kleinanzeigen.de/s-autos/berlin/preis:5000:20000/anbieter:privat/bmw/"
            "k0c216l3331r50+autos.marke_s:bmw+autos.ez_i:2015%2C2020")
    assert refs.parse_search_link(link) == {
        "query": "bmw", "category_id": "216", "location_id": "3331", "radius": 50.0,
        "min_price": 5000.0, "max_price": 20000.0, "seller_type": "private",
        "filters": {"autos.marke": "bmw", "autos.ez": "2015,2020"}}
    assert refs.parse_search_link("https://www.kleinanzeigen.de/s-fahrrad-24-zoll/k0") == {"query": "fahrrad 24 zoll"}
    assert refs.parse_search_link("https://www.kleinanzeigen.de/s-berlin/seite:3/fahrrad/k0l3331") == {
        "query": "fahrrad", "location_id": "3331", "page": 3}
    assert refs.parse_search_link("https://www.kleinanzeigen.de/s-autos/c216") == {"category_id": "216"}
    assert refs.parse_search_link("https://www.kleinanzeigen.de/s-berlin/sortierung:preis/versand:ja/"
                                  "direktkaufen:aktiv/anzeige:gesuche/iphone/k0l3331") == {
        "query": "iphone", "location_id": "3331", "sort": "price_asc", "has_shipping": True,
        "buy_now_only": True, "ad_type": "wanted"}
    assert refs.parse_search_link("https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=sofa&categoryId=88"
                                  "&locationId=3331&radius=10&minPrice=5&maxPrice=50") == {
        "query": "sofa", "category_id": "88", "location_id": "3331", "radius": 10.0,
        "min_price": 5.0, "max_price": 50.0}
    assert refs.resolve_query("  iphone   15 ") == {"query": "iphone 15"}
    for bad in ("https://www.kleinanzeigen.de/s-anzeige/x/1-2-3", "https://www.ebay.de/sch/x"):
        with pytest.raises(ValueError):
            refs.parse_search_link(bad)


# ---- schemas ----------------------------------------------------------------------------

def test_search_schema():
    data, error = load_query(S.SearchSchema, {"query": "fahrrad", "location": "10115", "radius": "20",
                                               "sort": "PRICE_ASC", "has_shipping": "1",
                                               "filters": "fahrraeder.type:city"})
    assert error is None
    assert data["query"] == {"query": "fahrrad"} and data["location"] == {"postal_code": "10115"}
    assert data["sort"] == "price_asc" and data["has_shipping"] is True and data["page"] is None
    assert data["page_size"] == 25 and data["filters"] == {"fahrraeder.type": "city"}
    for bad in ({"page_size": "101"}, {"page": "101", "page_size": "100"}, {"latitude": "52"},
                {"sort": "cheapest"}, {"foo": "1"}, {"filters": "bmw"},
                {"location": "Berlin", "latitude": "52", "longitude": "13"}):
        assert load_query(S.SearchSchema, bad)[1] is not None, bad


def test_listing_schemas():
    assert load_query(S.ListingSchema, {"listing": "https://www.kleinanzeigen.de/s-anzeige/x/3439651996-217-3377"})[0] == {
        "listing": "3439651996"}
    assert load_query(S.ListingSchema, {})[1] is not None
    data, _ = load_query(S.ListingBatchSchema, {"listings": "1, 2,2,https://www.kleinanzeigen.de/s-anzeige/3"})
    assert data == {"listings": ["1", "2", "3"]}
    assert load_query(S.ListingBatchSchema, {"listings": ",".join(str(i) for i in range(1, 22))})[1] is not None
    assert load_query(S.ListingViewsSchema, {"listings": ",".join(str(i) for i in range(1, 51))})[1] is None


def test_vertical_schemas():
    data, error = load_query(S.CarsSchema, {"make": "BMW", "fuel": "Electric", "transmission": "automatic",
                                             "body_type": "estate", "color": "green", "condition": "undamaged",
                                             "min_year": "2015", "max_mileage": "150000"})
    assert error is None
    assert (data["fuel"], data["transmission"], data["body_type"], data["color"], data["condition"]) == (
        "elektro", "automatik", "kombi", "grün", "nein")
    assert data["sort"] == "newest" and data["page"] == 1
    assert load_query(S.CarsSchema, {"radius": "10"})[1] is not None           # radius without location
    data, error = load_query(S.RealEstateSchema, {"features": "Balcony, garden", "min_rooms": "2.5"})
    assert error is None and data["type"] == "apartment_rent" and data["features"] == ["balcony", "garden"]
    assert load_query(S.RealEstateSchema, {"features": "pool"})[1] is not None
    assert load_query(S.JobsSchema, {"field": "sales"})[0]["field"] == "117"
