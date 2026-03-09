import json
from copy import deepcopy

from pricing import CATALOG as _DEFAULT_CATALOG
from database import get_setting, set_setting

DEFAULT_SITE_CONFIG = {
    "eyebrow": "Professional DJ Hire",
    "hero_title": "Build Your",
    "hero_gradient": "DJ Package",
    "hero_subtitle": "Select the services you need. Your package builds live on the right — then request a quote in seconds.",
}


def load_catalog() -> dict:
    raw = get_setting("catalog_override")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return deepcopy(_DEFAULT_CATALOG)


def save_catalog(catalog: dict) -> None:
    set_setting("catalog_override", json.dumps(catalog, ensure_ascii=False))


def get_prices(catalog: dict | None = None) -> dict[str, float]:
    if catalog is None:
        catalog = load_catalog()
    return {
        item_id: float(item["price"])
        for category in catalog.values()
        for item_id, item in category["items"].items()
    }


def load_site_config() -> dict:
    raw = get_setting("site_config")
    if raw:
        try:
            saved = json.loads(raw)
            return {**DEFAULT_SITE_CONFIG, **saved}
        except Exception:
            pass
    return DEFAULT_SITE_CONFIG.copy()


def save_site_config(config: dict) -> None:
    set_setting("site_config", json.dumps(config, ensure_ascii=False))
