"""Live endpoint smoke tests: one call per /kleinanzeigen/* route against a
running service, with example values proven to return data (2026-10-05).
The listing tooling (derive_facts.py) reads each route's FIRST call from
this file's AST as its working example, so the values stay literals.

Skipped unless KLEINANZEIGEN_BASE points at a running service:

    ONLY_SCRAPER=kleinanzeigen python run.py            # or any bottle runner
    KLEINANZEIGEN_BASE=http://127.0.0.1:6002 python -m pytest kleinanzeigen/test_endpoints.py -q

Listings expire, so tests that need a listing id take a fresh one from a
search instead of a hard-coded id.
"""
import os

import pytest

BASE = os.environ.get("KLEINANZEIGEN_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set KLEINANZEIGEN_BASE to run live endpoint tests")


def get(path, **params):
    from curl_cffi import requests
    return requests.get(BASE + path, params=params, timeout=180)


def call(path, **params):
    resp = get(path, **params)
    assert resp.status_code == 200, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


def fresh_listing():
    return call("/kleinanzeigen/search", query="fahrrad", location="Berlin", page_size=5)["results"][0]


def test_search():
    page = call("/kleinanzeigen/search", query="fahrrad", location="Berlin", radius=20, min_price=100,
                max_price=500, sort="price_asc")
    assert page["count"] > 100 and page["per_page"] == 25 and page["next"] and len(page["results"]) == 25
    first = page["results"][0]
    assert first["id"] and first["link"] and first["price"]["amount"] >= 100 and first["location"]["latitude"]
    assert page["category_counts"][0]["name"]
    second = call("/kleinanzeigen/search", query="fahrrad", location="Berlin", radius=20, min_price=100,
                  max_price=500, sort="price_asc", page=2)
    assert second["current_page"] == 2 and second["results"][0]["id"] != first["id"]
    assert len(call("/kleinanzeigen/search", query="iphone", page_size=100)["results"]) == 100


def test_search_forms():
    by_link = call("/kleinanzeigen/search",
                   query="https://www.kleinanzeigen.de/s-autos/berlin/anbieter:privat/bmw/k0c216l3331r50+autos.marke_s:bmw")
    assert by_link["query"] == "bmw" and by_link["category"]["id"] == "216"
    assert all(r["seller"]["type"] == "private" for r in by_link["results"])
    filtered = call("/kleinanzeigen/search", filters="autos.marke:audi,autos.km:-100000,autos.navi:true")
    makes = {a["raw_value"] for r in filtered["results"] for a in r["attributes"] if a["key"] == "autos.marke"}
    assert makes == {"audi"}
    assert call("/kleinanzeigen/search", category="Fahrräder & Zubehör", location="10115", has_shipping="true")["results"]
    free = call("/kleinanzeigen/search", query="sofa", free_only="true")
    assert all(r["price"]["is_free"] for r in free["results"])
    assert call("/kleinanzeigen/search", query="iphone", latitude=52.5, longitude=13.4, radius=5, sort="distance")["results"]
    assert call("/kleinanzeigen/search", query="iphone", seller_type="commercial", ad_type="wanted")["results"]
    assert call("/kleinanzeigen/search", query="iphone", buy_now_only="true", has_photos="true")["results"][0]["is_buy_now"]
    assert call("/kleinanzeigen/search/suggestions", query="fahr")["suggestions"][0]["text"] == "fahrrad"


def test_search_errors():
    assert get("/kleinanzeigen/search", category="Fahrad").status_code == 400
    assert get("/kleinanzeigen/search", filters="autos.marke:bmww").status_code == 400
    assert get("/kleinanzeigen/search", query="x", location="Zzzzqqq").status_code == 400
    assert get("/kleinanzeigen/search", query="x", radius=10).status_code == 400
    assert get("/kleinanzeigen/search", query="x", page=500, page_size=100).status_code == 400
    assert call("/kleinanzeigen/search", query="zzzzqqqxxyy")["count"] == 0


def test_listings():
    row = fresh_listing()
    item = call("/kleinanzeigen/listings/details", listing=row["id"])
    assert item["id"] == row["id"] and item["title"] and item["seller"]["name"] and item["views"] is not None
    assert item["seller"]["total_listings"] is not None and item["posted_at"].endswith("Z")
    assert call("/kleinanzeigen/listings/details", listing=row["link"])["id"] == row["id"]
    batch = call("/kleinanzeigen/listings/details/batch", listings=f"{row['id']},1")
    assert batch["count"] == 1 and batch["not_found"] == ["1"] and batch["listings"][0]["views"] is not None
    similar = call("/kleinanzeigen/listings/similar", listing=row["id"])
    assert similar["results"] and similar["results"][0]["id"] != row["id"]
    views = call("/kleinanzeigen/listings/views", listings=row["id"])
    assert views["listings"][0]["id"] == row["id"] and views["listings"][0]["views"] is not None
    assert get("/kleinanzeigen/listings/details", listing="1").status_code == 404
    assert get("/kleinanzeigen/listings/similar", listing="1").status_code == 404
    assert get("/kleinanzeigen/listings/details", listing="abc").status_code == 400


def test_sellers_and_stores():
    seller = call("/kleinanzeigen/sellers/profile", seller="158553607")
    assert seller["name"] and seller["rating"]["satisfaction"]["label"] and seller["store"]["id"] == "202135"
    listings = call("/kleinanzeigen/sellers/listings",
                    seller="https://www.kleinanzeigen.de/s-bestandsliste.html?userId=158553607", sort="price_desc")
    assert listings["count"] > 10 and listings["results"][0]["seller"]["id"] == "158553607"
    store = call("/kleinanzeigen/stores/profile",
                 store="https://www.kleinanzeigen.de/pro/holz--naturstein--historische-baustoffe")
    assert store["id"] == "202135" and store["address"]["city"] and store["opening_hours"] and store["seller"]["id"]
    assert call("/kleinanzeigen/stores/profile", store="202135")["slug"] == "holz--naturstein--historische-baustoffe"
    assert call("/kleinanzeigen/stores/listings", store="holz--naturstein--historische-baustoffe", query="tisch")["results"]
    assert get("/kleinanzeigen/sellers/profile", seller="99999999999").status_code == 404
    assert get("/kleinanzeigen/stores/profile", store="this-store-does-not-exist-xyz").status_code == 404


def test_reference():
    tree = call("/kleinanzeigen/categories")
    assert tree["count"] > 100 and tree["categories"][0]["children"]
    assert call("/kleinanzeigen/categories", category="Immobilien")["category"]["id"] == "195"
    filters = call("/kleinanzeigen/categories/filters", category="216")
    assert any(f["key"] == "autos.marke" and f["options"] for f in filters["filters"])
    places = call("/kleinanzeigen/locations/search", query="Münster")
    assert places["locations"][0]["id"] == "929"
    assert call("/kleinanzeigen/locations/search", query="48143")["locations"][0]["postal_code"] == "48143"
    berlin = call("/kleinanzeigen/locations/details", location="Berlin")
    assert berlin["id"] == "3331" and len(berlin["children"]) > 20
    assert call("/kleinanzeigen/locations/details", location="id:929")["name"].startswith("Münster")
    assert call("/kleinanzeigen/locations/nearest", latitude=48.137, longitude=11.575)["state"] == "Bayern"
    assert len(call("/kleinanzeigen/locations/top")["locations"]) > 10


def test_verticals():
    cars = call("/kleinanzeigen/cars/search", make="BMW", model="3er", fuel="diesel", transmission="automatic",
                min_year=2015, max_mileage=150000)
    assert cars["make"] == "bmw" and cars["results"]
    for row in cars["results"]:
        attrs = {a["key"]: a["raw_value"] for a in row["attributes"]}
        assert attrs.get("autos.marke") == "bmw" and attrs.get("autos.fuel") == "diesel"
    flats = call("/kleinanzeigen/real-estate/search", type="apartment_rent", location="Hamburg", min_rooms=2,
                 max_price=1500, features="balcony,built_in_kitchen")
    assert flats["category"]["id"] == "203" and flats["results"][0]["price"]["amount"] <= 1500
    houses = call("/kleinanzeigen/real-estate/search", type="house_buy", location="Köln", radius=30,
                  min_area=120, property_type="Reihenhaus")
    assert houses["category"]["id"] == "208" and houses["results"]
    jobs = call("/kleinanzeigen/jobs/search", query="fahrer", field="transport_logistics", location="Berlin")
    assert jobs["category"]["id"] == "247" and jobs["results"]
    assert get("/kleinanzeigen/cars/search", make="bmww").status_code == 400
    assert get("/kleinanzeigen/cars/search", model="3er").status_code == 400
