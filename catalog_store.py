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


DEFAULT_BRANDING_CONFIG = {
    "company_name": "NowDJ",
    "logo_emoji": "🎧",
    "logo_image": "",
    "accent_color": "#fa854f",
}


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return (250, 133, 79)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def build_branding_style(branding: dict) -> str:
    color = branding.get("accent_color", "#fa854f")
    try:
        r, g, b = _hex_to_rgb(color)
    except Exception:
        r, g, b = 250, 133, 79
    rl = min(255, int(r + (255 - r) * 0.35))
    gl = min(255, int(g + (255 - g) * 0.35))
    bl = min(255, int(b + (255 - b) * 0.35))
    rd = max(0, int(r * 0.88))
    gd = max(0, int(g * 0.88))
    bd = max(0, int(b * 0.88))
    light = f"#{rl:02x}{gl:02x}{bl:02x}"
    dark = f"#{rd:02x}{gd:02x}{bd:02x}"
    return (
        f"<style>:root {{\n"
        f"  --accent: {color};\n"
        f"  --accent-light: {light};\n"
        f"  --accent-dark: {dark};\n"
        f"  --accent-dim: rgba({r},{g},{b},0.14);\n"
        f"  --accent-glow: rgba({r},{g},{b},0.28);\n"
        f"  --border-active: rgba({r},{g},{b},0.55);\n"
        f"  --shadow-glow: 0 0 32px rgba({r},{g},{b},0.18);\n"
        f"}}</style>"
    )


def load_branding_config() -> dict:
    raw = get_setting("branding_config")
    if raw:
        try:
            saved = json.loads(raw)
            return {**DEFAULT_BRANDING_CONFIG, **saved}
        except Exception:
            pass
    return DEFAULT_BRANDING_CONFIG.copy()


def save_branding_config(config: dict) -> None:
    set_setting("branding_config", json.dumps(config, ensure_ascii=False))
