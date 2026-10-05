"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. See README.md → "Endpoints" for everything the package can do.
"""
import json
import os

from cache import DontCache
from kleinanzeigen import listings, search, verticals

os.makedirs("output", exist_ok=True)


def save(name, data):
    if isinstance(data, DontCache):      # an empty search page comes wrapped
        data = data.data
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # a listing id or any kleinanzeigen.de listing link
    save("listing_3238808629.json", listings.details("3238808629"))

    # up to 100 listings per page
    save("search_macbook_pro.json", search.search(query={"query": "macbook pro"}, page_size=50))

    # cars with named filters instead of raw category filters
    save("cars_porsche.json", verticals.cars(make="porsche", min_price=40000, page_size=50))
