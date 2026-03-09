import json
import os
from copy import deepcopy
from pathlib import Path

from pricing import CATALOG as _DEFAULT_CATALOG

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
CATALOG_PATH = _DATA_DIR / "catalog_override.json"
SITECONFIG_PATH = _DATA_DIR / "site_config.json"

DEFAULT_SITE_CONFIG = {
    "eyebrow": "Professional DJ Hire",
    "hero_title": "Build Your",
    "hero_gradient": "DJ Package",
    "hero_subtitle": "Select the services you need. Your package builds live on the right — then request a quote in seconds.",
}


def load_catalog() -> dict:
    if CATALOG_PATH.exists():
        try:
            return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return deepcopy(_DEFAULT_CATALOG)


def save_catalog(catalog: dict) -> None:
    CATALOG_PATH.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_prices(catalog: dict | None = None) -> dict[str, float]:
    if catalog is None:
        catalog = load_catalog()
    return {
        item_id: float(item["price"])
        for category in catalog.values()
        for item_id, item in category["items"].items()
    }


def load_site_config() -> dict:
    if SITECONFIG_PATH.exists():
        try:
            saved = json.loads(SITECONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULT_SITE_CONFIG, **saved}
        except Exception:
            pass
    return DEFAULT_SITE_CONFIG.copy()


def save_site_config(config: dict) -> None:
    SITECONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
