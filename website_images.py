"""Registry + storage for the marketing site's uploadable image slots.

Each slot corresponds 1:1 with an <ImageSlot id="..."> on
hertfordshiredjrental.co.uk (see components/ImageSlot.tsx in that repo).
The ids here are the source of truth — the Next.js site's ids must match
exactly, or an uploaded image simply won't show up on that slot.

Uploaded files are written to disk (under the same persistent volume the
sqlite db lives on) rather than stored as base64 in the settings JSON, so
GET /api/website-images stays small even once every slot has a real photo.
"""

import base64
import binascii
import re
from pathlib import Path

from database import DB_PATH, get_setting, set_setting

UPLOADS_DIR = Path(DB_PATH).parent / "uploads"

SLOT_REGISTRY = [
    # Homepage
    {"id": "home.hero.intro-main", "section": "Homepage", "label": "Intro — main photo", "description": "DJ booth / crowd from behind the decks"},
    {"id": "home.hero.intro-detail", "section": "Homepage", "label": "Intro — detail photo", "description": "Equipment detail — mixer / jog wheel"},
    {"id": "home.events.private", "section": "Homepage", "label": "Event selector — Private Events", "description": "Private party, crowd energy"},
    {"id": "home.events.wedding", "section": "Homepage", "label": "Event selector — Weddings", "description": "Wedding dancefloor, evening"},
    {"id": "home.events.corporate", "section": "Homepage", "label": "Event selector — Corporate", "description": "Corporate / branded event"},
    {"id": "home.events.venue", "section": "Homepage", "label": "Event selector — Venues", "description": "Venue DJ booth / bar"},
    {"id": "home.djhire.main", "section": "Homepage", "label": "DJ Hire section photo", "description": "DJ booth with crowd, wide"},
    {"id": "home.equipment.cdj", "section": "Homepage", "label": "Equipment section photo", "description": "CDJs / mixer, dramatic dark product shot"},
    {"id": "home.photobooth.guests", "section": "Homepage", "label": "Photo booth preview 1", "description": "Photo booth guests"},
    {"id": "home.photobooth.prints", "section": "Homepage", "label": "Photo booth preview 2", "description": "Booth prints / detail"},
    {"id": "home.gallery.1", "section": "Homepage", "label": "Gallery — Wedding / Hertfordshire", "description": "Event photo — landscape"},
    {"id": "home.gallery.2", "section": "Homepage", "label": "Gallery — Private party", "description": "Event photo — portrait"},
    {"id": "home.gallery.3", "section": "Homepage", "label": "Gallery — Booth detail", "description": "Detail shot — decks / hands"},
    {"id": "home.gallery.4", "section": "Homepage", "label": "Gallery — Corporate", "description": "Corporate event photo"},

    # Service pages
    {"id": "dj-hire.split", "section": "DJ Hire page", "label": "Section photo", "description": "DJ booth with crowd"},
    {"id": "corporate.split", "section": "Corporate DJ Hire page", "label": "Section photo", "description": "Corporate event — dark, sophisticated"},
    {"id": "private-party.split", "section": "Private Party DJ Hire page", "label": "Section photo", "description": "Private party — crowd, energy"},
    {"id": "residencies.split", "section": "DJ Residencies page", "label": "Section photo", "description": "Venue DJ booth / bar"},
    {"id": "speaker-hire.split", "section": "Speaker Hire page", "label": "Section photo", "description": "PA speakers / stage setup"},
    {"id": "equipment-hire.split", "section": "DJ Equipment Hire page", "label": "Section photo", "description": "Equipment flightcased / booth setup"},
    {"id": "equipment.cdj-media-players", "section": "DJ Equipment Hire page", "label": "Item — CDJ media players", "description": "Product shot"},
    {"id": "equipment.club-mixers", "section": "DJ Equipment Hire page", "label": "Item — Club mixers", "description": "Product shot"},
    {"id": "equipment.all-in-one-controllers", "section": "DJ Equipment Hire page", "label": "Item — All-in-one controllers", "description": "Product shot"},
    {"id": "equipment.direct-drive-turntables", "section": "DJ Equipment Hire page", "label": "Item — Direct-drive turntables", "description": "Product shot"},
    {"id": "equipment.active-pa-speakers", "section": "DJ Equipment Hire page", "label": "Item — Active PA speakers", "description": "Product shot"},
    {"id": "equipment.subwoofers", "section": "DJ Equipment Hire page", "label": "Item — Subwoofers", "description": "Product shot"},
    {"id": "equipment.microphones", "section": "DJ Equipment Hire page", "label": "Item — Microphones", "description": "Product shot"},
    {"id": "equipment.moving-heads", "section": "DJ Equipment Hire page", "label": "Item — Moving heads", "description": "Product shot"},
    {"id": "equipment.uplighting", "section": "DJ Equipment Hire page", "label": "Item — Uplighting", "description": "Product shot"},
    {"id": "equipment.dancefloor-effects", "section": "DJ Equipment Hire page", "label": "Item — Dancefloor effects", "description": "Product shot"},

    # About
    {"id": "about.how-we-work", "section": "About page", "label": "How we work photo", "description": "Team / booth / equipment"},
    {"id": "about.team.ben-henderson", "section": "About page", "label": "Team photo — Ben Henderson", "description": "Headshot"},
    {"id": "about.team.anton-elliott", "section": "About page", "label": "Team photo — Anton Elliott", "description": "Headshot"},
    {"id": "about.team.matt-crawford", "section": "About page", "label": "Team photo — Matt Crawford", "description": "Headshot"},

    # Journal (shared between the index thumbnail and the article page)
    {"id": "journal.how-to-plan-the-music-for-your-wedding-day", "section": "Journal", "label": "How to plan the music for your wedding day", "description": "Wedding dancefloor / booth"},
    {"id": "journal.what-size-pa-do-you-actually-need", "section": "Journal", "label": "What size PA do you actually need?", "description": "PA speakers / stage setup"},
    {"id": "journal.sound-limiters-in-hertfordshire-venues-explained", "section": "Journal", "label": "Sound limiters in Hertfordshire venues, explained", "description": "Venue DJ booth / bar"},
    {"id": "journal.cdjs-vs-controllers-which-should-you-hire", "section": "Journal", "label": "CDJs vs controllers: which should you hire?", "description": "CDJs / mixer, dramatic dark product shot"},
    {"id": "journal.getting-the-work-christmas-party-right", "section": "Journal", "label": "Getting the work Christmas party right", "description": "Corporate event — dark, sophisticated"},
    {"id": "journal.uplighting-what-it-does-to-a-room", "section": "Journal", "label": "Uplighting: what it does to a room", "description": "Uplighting in a venue"},
]

_SLOT_IDS = {s["id"] for s in SLOT_REGISTRY}
_DATA_URL_RE = re.compile(r"^data:image/(png|jpe?g|webp|gif);base64,(.+)$", re.DOTALL)
_EXT_FOR_MIME = {"png": "png", "jpg": "jpg", "jpeg": "jpg", "webp": "webp", "gif": "gif"}


def is_valid_slot(slot_id: str) -> bool:
    return slot_id in _SLOT_IDS


def _mapping_key(tenant_id: int) -> str:
    return "website_images_map"


def _load_mapping(tenant_id: int) -> dict:
    raw = get_setting(_mapping_key(tenant_id), tenant_id=tenant_id)
    if not raw:
        return {}
    import json
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_mapping(tenant_id: int, mapping: dict) -> None:
    import json
    set_setting(_mapping_key(tenant_id), json.dumps(mapping, ensure_ascii=False), tenant_id=tenant_id)


def get_image_urls(tenant_id: int) -> dict:
    """slot_id -> public URL (cache-busted), for slots that have an uploaded image."""
    mapping = _load_mapping(tenant_id)
    out = {}
    for slot_id, entry in mapping.items():
        rel_path = entry.get("path")
        version = entry.get("version", "1")
        if rel_path:
            out[slot_id] = f"/uploads/{rel_path}?v={version}"
    return out


def save_image(tenant_id: int, slot_id: str, data_url: str) -> str:
    """Decode a base64 data URL, write it to disk, update the mapping. Returns the public URL."""
    if not is_valid_slot(slot_id):
        raise ValueError("Unknown slot id")

    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise ValueError("Expected a base64 image data URL")
    ext = _EXT_FOR_MIME[m.group(1).lower()]
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Invalid base64 image data")
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Image is larger than 8MB")

    tenant_dir = UPLOADS_DIR / str(tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slot_id}.{ext}"
    (tenant_dir / filename).write_bytes(raw)

    mapping = _load_mapping(tenant_id)
    prev_version = int((mapping.get(slot_id) or {}).get("version", 0))
    rel_path = f"{tenant_id}/{filename}"
    mapping[slot_id] = {"path": rel_path, "version": str(prev_version + 1)}
    _save_mapping(tenant_id, mapping)

    return f"/uploads/{rel_path}?v={prev_version + 1}"


def delete_image(tenant_id: int, slot_id: str) -> None:
    mapping = _load_mapping(tenant_id)
    entry = mapping.pop(slot_id, None)
    _save_mapping(tenant_id, mapping)
    if entry and entry.get("path"):
        try:
            (UPLOADS_DIR / entry["path"]).unlink(missing_ok=True)
        except Exception:
            pass
