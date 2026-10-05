"""/kleinanzeigen/cars/search, /real-estate/search and /jobs/search: the
general search with the category fixed and the category's filters exposed
as named params (make, mileage, rooms, living area, …) instead of raw
`filters` keys. Extra `filters` of the same category are still accepted."""
from kleinanzeigen import lookup
from kleinanzeigen.search import keep_fresh_if_empty, name_categories, run_search

CARS_CATEGORY = "216"
JOBS_CATEGORY = "102"
# public type -> (category id, attribute prefix)
REAL_ESTATE = {
    "apartment_rent": ("203", "wohnung_mieten"),
    "apartment_buy": ("196", "wohnung_kaufen"),
    "house_rent": ("205", "haus_mieten"),
    "house_buy": ("208", "haus_kaufen"),
}
PROPERTY_TYPE_KEY = {"apartment_rent": "wohnungstyp", "apartment_buy": "wohnungstyp",
                     "house_rent": "haustyp", "house_buy": "haustyp"}


def _range(low, high):
    """(min, max) -> 'min,max' with either side open, or None."""
    if low is None and high is None:
        return None
    fmt = lambda v: "" if v is None else (str(int(v)) if float(v) == int(v) else str(v))
    return f"{fmt(low)},{fmt(high)}"


def _attrs(category_id, named, extra):
    """Named-param attrs + raw `filters` of the same category, validated."""
    attrs = {key: value for key, value in named.items() if value is not None}
    for key, value in (extra or {}).items():
        attrs.setdefault(key, value)
    return lookup.validate_filters(category_id, attrs)


def _finish(result, category_id, **echo):
    return {**echo, "category": lookup.category_card(category_id), "results": result["results"],
            "pagination": result["pagination"]}


def cars(query=None, location=None, radius=None, make=None, model=None, fuel=None, transmission=None,
         body_type=None, color=None, condition=None, min_year=None, max_year=None, min_mileage=None,
         max_mileage=None, min_power=None, max_power=None, min_price=None, max_price=None,
         seller_type=None, has_photos=None, sort="newest", filters=None, page=1, page_size=25):
    """Used cars by make, model, first registration year, mileage, power
    (PS), fuel, transmission, body type, colour and condition."""
    if model and not make:
        raise ValueError("model needs make (e.g. make=bmw&model=3er).")
    named = {
        "autos.marke": make,
        "autos.model": model,
        "autos.fuel": fuel,
        "autos.shift": transmission,
        "autos.typ": body_type,
        "autos.aussenfarbe": color,
        "autos.schaden": condition,
        "autos.ez": _range(min_year, max_year),
        "autos.km": _range(min_mileage, max_mileage),
        "autos.power": _range(min_power, max_power),
    }
    attrs = _attrs(CARS_CATEGORY, named, filters)
    result = run_search(query=query, category_id=CARS_CATEGORY, location_id=lookup.location_id(location),
                        radius=radius, min_price=min_price, max_price=max_price, seller_type=seller_type,
                        has_photos=has_photos, sort=sort, attrs=attrs, page=page, page_size=page_size,
                        label="cars search")
    return keep_fresh_if_empty(_finish(result, CARS_CATEGORY, query=query, make=attrs.get("autos.marke"),
                                       model=attrs.get("autos.model")))


def real_estate(type="apartment_rent", query=None, location=None, radius=None, min_price=None,
                max_price=None, min_rooms=None, max_rooms=None, min_area=None, max_area=None,
                min_plot_area=None, max_plot_area=None, min_year_built=None, max_year_built=None,
                property_type=None, features=None, seller_type=None, has_photos=None, sort="newest",
                filters=None, page=1, page_size=25):
    """Flats and houses to rent or buy by rooms, living area, plot area,
    year built, property type and features (balcony, garden, garage, …).
    min_price / max_price are the monthly rent or the purchase price."""
    category_id, prefix = REAL_ESTATE[type]
    named = {
        f"{prefix}.zimmer": _range(min_rooms, max_rooms),
        f"{prefix}.qm": _range(min_area, max_area),
        f"{prefix}.grundstuecksflaeche": _range(min_plot_area, max_plot_area),
        f"{prefix}.baujahr": _range(min_year_built, max_year_built),
        f"{prefix}.{PROPERTY_TYPE_KEY[type]}": property_type,
    }
    for feature in features or []:
        named[f"{prefix}.{feature}"] = "true"
    attrs = _attrs(category_id, named, filters)
    result = run_search(query=query, category_id=category_id, location_id=lookup.location_id(location),
                        radius=radius, min_price=min_price, max_price=max_price, seller_type=seller_type,
                        has_photos=has_photos, sort=sort, attrs=attrs, page=page, page_size=page_size,
                        label="real-estate search")
    return keep_fresh_if_empty(_finish(result, category_id, type=type, query=query))


def jobs(query=None, field=None, location=None, radius=None, seller_type=None, ad_type=None,
         sort="newest", page=1, page_size=25):
    """Job listings by keyword, field of work and place. `field` narrows
    to one of the site's job categories; without it every job category is
    searched."""
    category_id = field or JOBS_CATEGORY
    result = run_search(query=query, category_id=category_id, location_id=lookup.location_id(location),
                        radius=radius, seller_type=seller_type, ad_type=ad_type, sort=sort, page=page,
                        page_size=page_size, label="jobs search")
    out = _finish(result, category_id, query=query)
    out["field_counts"] = name_categories(result["category_counts"])
    return keep_fresh_if_empty(out)
