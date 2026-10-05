# 🛒 Kleinanzeigen Scraper

Kleinanzeigen Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Kleinanzeigen data for free.

## ✨ What Can I Get?

- 🔍 **Search 58M+ live listings** — keyword, category, location, radius, price & category filters, 100 per page
- 🚗 **Full listing details** — price, attributes, every photo, coordinates, shipping, view count & seller reputation
- 🏪 **Sellers & stores** — profiles, badges, reply rate, phone, opening hours and their whole inventory
- 🏠 **Cars, real estate & jobs** — search by make, mileage, rooms, living area and more with plain params

## 🎥 Example: A Full Kleinanzeigen Listing

```json
{
  "id": "3238808629",
  "title": "Porsche 911 2.2 S 1971 Umfrangreich restauriert",
  "link": "https://www.kleinanzeigen.de/s-anzeige/porsche-911-2-2-s-1971-umfrangreich-restauriert/3238808629-216-3433",
  "description": "Dieser 1971 Porsche 911 2.2 S stammt aus deutscher Erstauslieferung und präsentiert sich in einem sehr guten optischen wie technischen Gesamtzustand. Innen, außen und technisch umfassend restauriert...",
  "type": "offer",
  "status": "active",
  "views": 6734,
  "image_count": 20,
  "price": { "amount": 99500, "currency": "EUR", "type": "fixed", "is_negotiable": false },
  "category": { "id": "216", "name": "Autos", "parent_id": "210" },
  "location": {
    "id": "3433",
    "name": "13595 Spandau",
    "postal_code": "13595",
    "state": "Berlin",
    "street": "Scharfe Lanke 109-131",
    "latitude": 52.507668,
    "longitude": 13.191692
  },
  "seller": {
    "id": "122110278",
    "name": "MOTORSPORT24 GmbH",
    "type": "commercial",
    "member_since": "2022-08-31",
    "phone": "+49 (0)30 692014098",
    "rating": {
      "score": 0.7,
      "satisfaction": { "level": 2, "label": "TOP" },
      "friendliness": { "level": 1, "label": "Sehr freundlich" },
      "reliability": { "level": 1, "label": "Sehr zuverlässig" }
    },
    "store": { "id": "96132", "name": "MOTORSPORT24 GmbH", "link": "https://www.kleinanzeigen.de/pro/MOTORSPORT24" },
    "active_listings": 128,
    "total_listings": 1987,
    "followers": 1046
  },
  "attributes": [
    { "key": "autos.marke", "name": "Marke", "value": "Porsche", "raw_value": "porsche", "unit": null },
    { "key": "autos.km", "name": "Kilometerstand", "value": 91111, "raw_value": "91111", "unit": "km" },
    { "key": "autos.power", "name": "Leistung", "value": 179, "raw_value": "179", "unit": "PS" }
  ],
  "images": [
    {
      "link": "https://img.kleinanzeigen.de/api/v1/prod-ads/images/26/26d4690a-1a56-41e5-bfd2-541c9e9497cc?rule=$_57.JPG",
      "thumbnail_link": "https://img.kleinanzeigen.de/api/v1/prod-ads/images/26/26d4690a-1a56-41e5-bfd2-541c9e9497cc?rule=$_2.JPG"
    }
  ],
  "posted_at": "2026-10-03T10:04:26Z",
  "updated_at": "2026-09-18T10:58:14Z"
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Kleinanzeigen Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/kleinanzeigen-scraper
cd kleinanzeigen-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

Kleinanzeigen only answers German IP addresses. In Germany this works as is. Anywhere else, start it with a German proxy:
```bash
KLEINANZEIGEN_PROXY=http://user:pass@host:port python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/listings/details?listing=3238808629"
```

```json
{
  "id": "3238808629",
  "title": "Porsche 911 2.2 S 1971 Umfrangreich restauriert",
  "link": "https://www.kleinanzeigen.de/s-anzeige/porsche-911-2-2-s-1971-umfrangreich-restauriert/3238808629-216-3433",
  "type": "offer",
  "status": "active",
  "views": 6734,
  "image_count": 20,
  "price": { "amount": 99500, "currency": "EUR", "type": "fixed", "is_negotiable": false },
  "category": { "id": "216", "name": "Autos", "parent_id": "210" },
  "location": { "id": "3433", "name": "13595 Spandau", "state": "Berlin", "latitude": 52.507668, "longitude": 13.191692 },
  "seller": {
    "id": "122110278",
    "name": "MOTORSPORT24 GmbH",
    "type": "commercial",
    "member_since": "2022-08-31",
    "active_listings": 128,
    "followers": 1046
  },
  "posted_at": "2026-10-03T10:04:26Z"
}
```

All 19 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

19 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Listing Details | `/listings/details` | Everything about one listing in a single call |
| Search Suggestions | `/search/suggestions` | What people type into the search box, most searched first |
| Search Listings | `/search` | Up to 100 listings per page with every filter the site has |
| Search Cars / Real Estate / Jobs | `/cars/search`, `/real-estate/search`, `/jobs/search` | Make, mileage, rooms, area and job field as plain params |
| Listing Details Batch | `/listings/details/batch` | Full details for 20 listings in one call |
| Similar Listings | `/listings/similar` | The listings the site recommends next to one |
| Listing Views | `/listings/views` | View counts for 50 listings at once to track demand |
| Seller Profile / Listings | `/sellers/profile`, `/sellers/listings` | Reputation badges, reply rate and the seller's whole inventory |
| Store Profile / Listings | `/stores/profile`, `/stores/listings` | Phone, website, address, opening hours and the store's inventory |
| Categories / Category Filters | `/categories`, `/categories/filters` | The category tree and every filter each category supports |
| Search Locations / Location Details | `/locations/search`, `/locations/details` | Cities, districts and postal codes with IDs and coordinates |
| Nearest Location / Top Locations | `/locations/nearest`, `/locations/top` | The location for any coordinate, and the top cities |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-kleinanzeigen-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-kleinanzeigen-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-kleinanzeigen-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/listings/details",
    params={"listing": "3238808629"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Kleinanzeigen Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Kleinanzeigen%20Scraper%20API.)

[![Ask Us by Email about Kleinanzeigen Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Kleinanzeigen%20Scraper%20API&body=I%20need%20help%20using%20the%20Kleinanzeigen%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/kleinanzeigen-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/kleinanzeigen-scraper)