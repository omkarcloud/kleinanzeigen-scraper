"""The 19 Kleinanzeigen endpoints. Every path is served with and without the
`/kleinanzeigen` prefix, so code generated against the hosted API on RapidAPI
(paths like /listings/details) runs unchanged against this server.

Params are validated by the marshmallow schemas in kleinanzeigen/schemas.py:
unknown or invalid params answer 400 with the reason."""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from cache import cached_call
from kleinanzeigen import cache_config as ttl
from kleinanzeigen import listings, reference, search, sellers, verticals
from kleinanzeigen import schemas as S
from schema_fields import load_query
from scraper_errors import BadRequest, NotFound


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The query as unicode strings (bottle 0.12's .get() hands back latin-1
    decoded bytes, so a UTF-8 "München" would arrive as "MÃ¼nchen")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _page_link(params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "")}
    query["page"] = page
    host = request.headers.get("Host") or "localhost"
    return f"{request.urlparts.scheme}://{host}{request.path}?{urlencode(query)}"


def paginate(result, params):
    """Lift the `pagination` block into count / per_page / current_page /
    total_pages / next / previous, like the hosted API."""
    result = dict(result)
    pagination = result.pop("pagination", None) or {}
    result.pop("count", None)
    page = int(pagination.get("page") or params.get("page") or 1)
    total_pages = int(pagination.get("total_pages") or 0)
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(params, page + 1 if page < total_pages else None),
        "previous": _page_link(params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(path, schema, fn, paginated, cache):
    """Validate, run, map errors: bad params -> 400, missing entity -> 404,
    retries exhausted / blocked -> 500."""
    raw = query_dict()
    data, error = load_query(schema, raw)
    if error:
        return json_response(error, 400)
    try:
        result = cached_call(f"{fn.__module__}.{fn.__name__}", data, fn, cache)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"kleinanzeigen rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:
        return json_response({"error": f"kleinanzeigen {path.strip('/')} failed: {e}"}, 500)
    return json_response(paginate(result, raw) if paginated else result)


ENDPOINTS = [
    # (path, schema, function, paginated, cache)
    ("/listings/details", S.ListingSchema, listings.details, False, ttl.LISTING_CACHE),
    ("/search/suggestions", S.SuggestionsSchema, search.suggestions, False, ttl.SUGGEST_CACHE),
    ("/search", S.SearchSchema, search.search, True, ttl.SEARCH_CACHE),
    ("/cars/search", S.CarsSchema, verticals.cars, True, ttl.SEARCH_CACHE),
    ("/real-estate/search", S.RealEstateSchema, verticals.real_estate, True, ttl.SEARCH_CACHE),
    ("/jobs/search", S.JobsSchema, verticals.jobs, True, ttl.SEARCH_CACHE),
    ("/listings/details/batch", S.ListingBatchSchema, listings.batch, False, ttl.LISTING_CACHE),
    ("/listings/similar", S.ListingSchema, listings.similar, False, ttl.SIMILAR_CACHE),
    ("/listings/views", S.ListingViewsSchema, listings.views, False, ttl.VIEWS_CACHE),
    ("/sellers/profile", S.SellerSchema, sellers.profile, False, ttl.SELLER_CACHE),
    ("/sellers/listings", S.SellerListingsSchema, sellers.listings, True, ttl.INVENTORY_CACHE),
    ("/stores/profile", S.StoreSchema, sellers.store_profile, False, ttl.STORE_CACHE),
    ("/stores/listings", S.StoreListingsSchema, sellers.store_listings, True, ttl.INVENTORY_CACHE),
    ("/categories", S.CategoriesSchema, reference.categories, False, ttl.CATEGORIES_CACHE),
    ("/categories/filters", S.CategoryFiltersSchema, reference.category_filters, False, ttl.FILTERS_CACHE),
    ("/locations/search", S.LocationSearchSchema, reference.location_search, False, ttl.LOCATION_CACHE),
    ("/locations/details", S.LocationSchema, reference.location_details, False, ttl.LOCATION_CACHE),
    ("/locations/nearest", S.CoordinatesSchema, reference.location_nearest, False, ttl.LOCATION_CACHE),
    ("/locations/top", S.EmptySchema, reference.top_locations, False, ttl.LOCATION_CACHE),
]


def mount(path, schema, fn, paginated, cache):
    """Serve a handler at /path and /kleinanzeigen/path."""
    def handler():
        return call(path, schema, fn, paginated, cache)
    handler.__name__ = "kleinanzeigen_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/kleinanzeigen" + path, method="GET")(handler)


for _endpoint in ENDPOINTS:
    mount(*_endpoint)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [e[0] for e in ENDPOINTS]})
