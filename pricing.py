CATALOG = {
    "dj_hire": {
        "title": "DJ Hire",
        "icon": "headphones",
        "items": {
            "standard_dj": {
                "name": "Standard DJ",
                "description": "4 hours of professional DJ service. Perfect for parties, birthdays & events.",
                "price": 350,
            },
            "premium_dj": {
                "name": "Premium DJ",
                "description": "6 hours with premium equipment, full music consultation & playlist planning.",
                "price": 550,
            },
            "wedding_dj": {
                "name": "Wedding DJ Package",
                "description": "Full day coverage — ceremony, drinks reception & evening entertainment.",
                "price": 750,
            },
            "extra_hours": {
                "name": "Extra Hour",
                "description": "Extend any DJ booking beyond the standard package duration.",
                "price": 75,
            },
        },
    },
    "speaker_hire": {
        "title": "Speaker Hire",
        "icon": "volume-2",
        "items": {
            "small_pa": {
                "name": "Small PA System",
                "description": "Crystal-clear audio for intimate venues up to 50 guests.",
                "price": 80,
            },
            "medium_pa": {
                "name": "Medium Event PA",
                "description": "Powerful system suited for mid-size events up to 150 guests.",
                "price": 120,
            },
            "large_pa": {
                "name": "Large Venue System",
                "description": "Professional line-array for large venues, 150+ guests.",
                "price": 200,
            },
            "subwoofer": {
                "name": "Subwoofer Add-On",
                "description": "Extra low-end punch. Pairs with any PA system.",
                "price": 50,
            },
        },
    },
    "equipment_hire": {
        "title": "DJ Equipment Hire",
        "icon": "sliders",
        "items": {
            "pioneer_decks": {
                "name": "Pioneer Decks",
                "description": "CDJ-2000NXS2 professional media players — industry standard.",
                "price": 90,
            },
            "dj_booth": {
                "name": "DJ Booth",
                "description": "Professional DJ booth with branded façade and cable management.",
                "price": 80,
            },
            "lighting": {
                "name": "Lighting Package",
                "description": "Moving heads, lasers, strobes & LED dance floor lighting.",
                "price": 120,
            },
            "smoke_machine": {
                "name": "Smoke Machine",
                "description": "Atmospheric haze & smoke effects for the perfect dance floor ambiance.",
                "price": 40,
            },
            "wireless_mics": {
                "name": "Wireless Microphones",
                "description": "Professional wireless mic set (2 handheld mics included).",
                "price": 25,
            },
        },
    },
    "photo_booth": {
        "title": "Photo Booth Hire",
        "icon": "camera",
        "items": {
            "photo_booth_std": {
                "name": "Standard Photo Booth",
                "description": "Classic enclosed booth with fun props, digital delivery & print option.",
                "price": 300,
            },
            "booth_360": {
                "name": "360 Video Booth",
                "description": "Stunning slow-motion 360° video experience. Instant social sharing.",
                "price": 450,
            },
            "backdrop": {
                "name": "Custom Backdrop",
                "description": "Personalised branded backdrop with your name, logo or event theme.",
                "price": 40,
            },
            "unlimited_prints": {
                "name": "Unlimited Prints",
                "description": "Physical photo prints for every guest, all night long.",
                "price": 50,
            },
        },
    },
}

# Flat price lookup — used for server-side total calculation
PRICES: dict[str, float] = {
    item_id: item["price"]
    for category in CATALOG.values()
    for item_id, item in category["items"].items()
}
