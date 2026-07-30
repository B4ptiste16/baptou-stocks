"""Classifies every ticker into a tradeable theme.

GICS-style sectors are too coarse for what we want ("Technology" lumps a
foundry with a payroll SaaS), so themes are resolved in priority order:

    1. explicit ticker overrides   - cross-cutting themes no label can express
    2. yfinance industry string    - precise, used whenever we have a profile
    3. security-name keywords      - fallback for names with no profile yet
    4. yfinance sector string      - coarse fallback
    5. "other"

Industry outranks the name keywords deliberately: a name ending in "Resources"
looks like mining, but if its industry is "Oil & Gas E&P" the industry is
right. The overrides sit on top because they encode intent the taxonomy has no
field for.

That ordering matters: NVDA's industry is "Semiconductors", but for a thematic
screener it belongs under AI, so the override wins.
"""

from __future__ import annotations

import re

# theme id -> (display label, sort order)
THEME_META: dict[str, tuple[str, int]] = {
    "ai_semis":      ("AI & Semiconductors", 10),
    "software":      ("Software & SaaS", 20),
    "internet":      ("Internet & Platforms", 30),
    "cyber":         ("Cybersecurity", 40),
    "quantum":       ("Quantum Computing", 50),
    "hardware":      ("Hardware & Electronics", 60),
    "space_defense": ("Space, Defense & Aerospace", 70),
    "fintech":       ("Fintech & Payments", 80),
    "crypto":        ("Crypto & Digital Assets", 90),
    "banks":         ("Banks", 100),
    "insurance":     ("Insurance", 110),
    "capital_mkts":  ("Capital Markets & Asset Managers", 120),
    "biotech":       ("Biotech", 130),
    "pharma":        ("Pharma", 140),
    "medtech":       ("Medical Devices & Diagnostics", 150),
    "health_svcs":   ("Healthcare Services", 160),
    "oil_gas":       ("Oil & Gas", 170),
    "clean_energy":  ("Clean Energy & Solar", 180),
    "nuclear":       ("Uranium & Nuclear", 190),
    "utilities":     ("Utilities", 200),
    "mining":        ("Mining & Precious Metals", 210),
    "materials":     ("Chemicals & Materials", 220),
    "ev_auto":       ("Autos & EV", 230),
    "industrials":   ("Industrials & Machinery", 240),
    "transport":     ("Transport & Logistics", 250),
    "shipping":      ("Shipping & Tankers", 260),
    "retail":        ("Retail & E-commerce", 270),
    "consumer":      ("Consumer Brands", 280),
    "food_bev":      ("Food & Beverage", 290),
    "restaurants":   ("Restaurants", 300),
    "travel":        ("Travel & Leisure", 310),
    "gaming":        ("Gaming & Casinos", 320),
    "media":         ("Media & Entertainment", 330),
    "telecom":       ("Telecom", 340),
    "reits":         ("Real Estate & REITs", 350),
    "homebuild":     ("Homebuilders & Construction", 360),
    "cannabis":      ("Cannabis", 370),
    "biz_svcs":      ("Business & Consumer Services", 380),
    "other":         ("Other", 900),
}

# Cross-cutting themes that a sector/industry label will never reveal.
TICKER_OVERRIDES: dict[str, set[str]] = {
    "ai_semis": {
        "NVDA", "AMD", "AVGO", "TSM", "MU", "MRVL", "SMCI", "ARM", "INTC",
        "ASML", "AMAT", "LRCX", "KLAC", "TER", "ONTO", "ACLS", "AEHR",
        "ALAB", "CRDO", "POWI", "MPWR", "SITM", "RMBS", "SNPS", "CDNS",
        "PLTR", "AI", "BBAI", "SOUN", "TEM", "CRWV", "NBIS", "APLD",
        "IREN", "CIFR", "WULF", "GLXY", "VRT", "MOD", "CLS", "FLEX",
        "ANET", "PSTG", "NTAP", "WDC", "STX", "SNOW", "MDB", "DDOG",
    },
    "quantum": {"IONQ", "RGTI", "QBTS", "QUBT", "ARQQ", "QMCO"},
    "crypto": {
        "COIN", "MSTR", "MARA", "RIOT", "CLSK", "HUT", "BITF", "CORZ",
        "HIVE", "BTBT", "CAN", "BTDR", "SBET", "BMNR", "GLXY", "CEP",
    },
    "space_defense": {
        "RKLB", "ASTS", "LUNR", "RDW", "SPCE", "PL", "MNTS", "ASTR",
        "LMT", "RTX", "NOC", "GD", "LHX", "HII", "TXT", "BA", "HWM",
        "AVAV", "KTOS", "MRCY", "LDOS", "BAH", "CACI", "SAIC", "PSN",
        "ACHR", "JOBY", "EH", "AIRO",
    },
    "nuclear": {
        "CCJ", "UEC", "UUUU", "DNN", "NXE", "URG", "LEU", "SMR", "OKLO",
        "BWXT", "LTBR", "ASPI", "NNE", "CEG", "VST", "TLN",
    },
    "clean_energy": {
        "FSLR", "ENPH", "SEDG", "RUN", "NOVA", "ARRY", "SHLS", "CSIQ",
        "JKS", "MAXN", "PLUG", "BE", "BLDP", "FCEL", "AMPX", "QS",
        "STEM", "NEP", "TPIC",
    },
    "ev_auto": {
        "TSLA", "RIVN", "LCID", "NIO", "XPEV", "LI", "GM", "F", "STLA",
        "PSNY", "GOEV", "MULN", "NKLA", "HYZN", "WKHS", "BLNK", "CHPT",
        "EVGO", "WBX",
    },
    "cannabis": {"TLRY", "CGC", "ACB", "CRON", "SNDL", "OGI", "GRWG", "IIPR"},
    "fintech": {
        "SQ", "XYZ", "PYPL", "SOFI", "AFRM", "UPST", "LC", "HOOD", "NU",
        "TOST", "MQ", "PAYO", "DLO", "STNE", "PAGS", "BILL", "FOUR",
    },
}
# Flatten to ticker -> theme (first theme listed wins on collision).
_OVERRIDE_LOOKUP: dict[str, str] = {}
for _theme, _tks in TICKER_OVERRIDES.items():
    for _t in _tks:
        _OVERRIDE_LOOKUP.setdefault(_t, _theme)

# Applied to the security name. Ordered — first hit wins.
NAME_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("quantum",      re.compile(r"\bquantum\b", re.I)),
    ("crypto",       re.compile(r"\b(bitcoin|blockchain|crypto|digital asset|"
                                r"mining corp.*bitcoin)\b", re.I)),
    ("ai_semis",     re.compile(r"\b(artificial intelligence|semiconductor|"
                                r"micro devices|photonics)\b", re.I)),
    ("space_defense", re.compile(r"\b(space|aerospace|defense|satellite|"
                                 r"orbital|rocket)\b", re.I)),
    ("nuclear",      re.compile(r"\b(uranium|nuclear|fission|atomic)\b", re.I)),
    ("clean_energy", re.compile(r"\b(solar|hydrogen|fuel cell|renewable|"
                                r"wind energy|clean energy)\b", re.I)),
    ("cannabis",     re.compile(r"\b(cannabis|cannabinoid|marijuana)\b", re.I)),
    ("biotech",      re.compile(r"\b(bio(?:tech|sciences|pharma|therapeutics)?|"
                                r"therapeutics|genomics|oncology|immuno)\b", re.I)),
    ("mining",       re.compile(r"\b(gold|silver|copper|lithium|mining|"
                                r"minerals|resources)\b", re.I)),
    ("reits",        re.compile(r"\b(REIT|realty|properties trust)\b", re.I)),
    ("shipping",     re.compile(r"\b(shipping|tankers|maritime|carriers|"
                                r"bulkers)\b", re.I)),
    ("banks",        re.compile(r"\b(bancorp|bancshares|bankshares|"
                                r"savings bank|national bank)\b", re.I)),

    # Generic fallbacks, deliberately last. These only ever apply to tickers
    # with no profile cached yet, so a loose match beats dumping the name in
    # "Other" — the industry label supersedes them as soon as it arrives.
    ("pharma",       re.compile(r"\bpharmaceutical|\bpharma\b", re.I)),
    ("medtech",      re.compile(r"\b(medical|health|surgical|diagnostic|"
                                r"dental)\b", re.I)),
    ("banks",        re.compile(r"\bbank\b", re.I)),
    ("insurance",    re.compile(r"\b(insurance|assurance|casualty|indemnity|"
                                r"underwriters)\b", re.I)),
    ("capital_mkts", re.compile(r"\b(capital|financial|asset management|"
                                r"holdings? (?:corp|inc)|partners)\b", re.I)),
    ("oil_gas",      re.compile(r"\b(petroleum|oil|natural gas|midstream|"
                                r"drilling|offshore)\b", re.I)),
    ("utilities",    re.compile(r"\b(electric|power (?:company|corp)|"
                                r"utilities)\b", re.I)),
    ("software",     re.compile(r"\b(software|technolog(?:y|ies)|systems|"
                                r"cloud|digital|data|cyber|network)\b", re.I)),
    ("industrials",  re.compile(r"\b(industri(?:es|al)|manufacturing|"
                                r"machinery|engineering|steel|equipment)\b", re.I)),
    ("transport",    re.compile(r"\b(logistics|freight|transport|railway|"
                                r"airlines?)\b", re.I)),
    ("food_bev",     re.compile(r"\b(foods?|beverages?|brewing|dairy|"
                                r"restaurants? group)\b", re.I)),
    ("retail",       re.compile(r"\b(stores?|retail|markets?|commerce)\b", re.I)),
    ("homebuild",    re.compile(r"\b(homes?|construction|builders?|"
                                r"cement|concrete)\b", re.I)),
    ("media",        re.compile(r"\b(media|entertainment|broadcasting|"
                                r"studios|communications)\b", re.I)),
]

# yfinance industry substring -> theme. Checked in this order.
INDUSTRY_PATTERNS: list[tuple[str, str]] = [
    ("semiconductor", "ai_semis"),
    ("software-infrastructure", "software"),
    ("software-application", "software"),
    ("software", "software"),
    ("information technology services", "software"),
    ("internet content", "internet"),
    ("internet retail", "retail"),
    ("electronic gaming", "gaming"),
    ("computer hardware", "hardware"),
    ("consumer electronics", "hardware"),
    ("electronic components", "hardware"),
    ("communication equipment", "hardware"),
    ("scientific & technical instruments", "hardware"),
    ("aerospace & defense", "space_defense"),
    ("credit services", "fintech"),
    ("financial data", "capital_mkts"),
    ("capital markets", "capital_mkts"),
    ("asset management", "capital_mkts"),
    ("banks", "banks"),
    ("insurance", "insurance"),
    ("mortgage finance", "banks"),
    ("biotechnology", "biotech"),
    ("drug manufacturers", "pharma"),
    ("pharmaceutical retailers", "health_svcs"),
    ("medical devices", "medtech"),
    ("medical instruments", "medtech"),
    ("diagnostics & research", "medtech"),
    ("medical care facilities", "health_svcs"),
    ("healthcare plans", "health_svcs"),
    ("health information services", "health_svcs"),
    ("medical distribution", "health_svcs"),
    ("oil & gas", "oil_gas"),
    ("uranium", "nuclear"),
    ("solar", "clean_energy"),
    ("utilities-renewable", "clean_energy"),
    ("utilities", "utilities"),
    ("gold", "mining"),
    ("silver", "mining"),
    ("copper", "mining"),
    ("other precious metals", "mining"),
    ("other industrial metals", "mining"),
    ("coking coal", "mining"),
    ("thermal coal", "mining"),
    ("aluminum", "materials"),
    ("steel", "materials"),
    ("chemicals", "materials"),
    ("paper", "materials"),
    ("packaging", "materials"),
    ("lumber", "materials"),
    ("auto manufacturers", "ev_auto"),
    ("auto parts", "ev_auto"),
    ("auto & truck dealerships", "retail"),
    ("recreational vehicles", "consumer"),
    ("marine shipping", "shipping"),
    ("railroads", "transport"),
    ("trucking", "transport"),
    ("integrated freight", "transport"),
    ("airlines", "travel"),
    ("airports", "travel"),
    ("travel services", "travel"),
    ("lodging", "travel"),
    ("resorts & casinos", "gaming"),
    ("gambling", "gaming"),
    ("leisure", "travel"),
    ("restaurants", "restaurants"),
    ("beverages", "food_bev"),
    ("packaged foods", "food_bev"),
    ("farm products", "food_bev"),
    ("confectioners", "food_bev"),
    ("food distribution", "food_bev"),
    ("grocery stores", "retail"),
    ("discount stores", "retail"),
    ("department stores", "retail"),
    ("specialty retail", "retail"),
    ("apparel retail", "retail"),
    ("home improvement retail", "retail"),
    ("luxury goods", "consumer"),
    ("apparel manufacturing", "consumer"),
    ("footwear", "consumer"),
    ("household & personal products", "consumer"),
    ("tobacco", "consumer"),
    ("furnishings", "consumer"),
    ("entertainment", "media"),
    ("broadcasting", "media"),
    ("advertising agencies", "media"),
    ("publishing", "media"),
    ("telecom services", "telecom"),
    ("real estate", "reits"),
    ("reit", "reits"),
    ("residential construction", "homebuild"),
    ("building products", "homebuild"),
    ("engineering & construction", "homebuild"),
    ("building materials", "homebuild"),
    ("specialty industrial machinery", "industrials"),
    ("farm & heavy construction", "industrials"),
    ("industrial distribution", "industrials"),
    ("electrical equipment", "industrials"),
    ("metal fabrication", "industrials"),
    ("tools & accessories", "industrials"),
    ("pollution & treatment", "industrials"),
    ("waste management", "biz_svcs"),
    ("conglomerates", "industrials"),
    ("business equipment", "biz_svcs"),
    ("consulting services", "biz_svcs"),
    ("staffing", "biz_svcs"),
    ("security & protection", "biz_svcs"),
    ("rental & leasing", "biz_svcs"),
    ("specialty business services", "biz_svcs"),
    ("personal services", "biz_svcs"),
    ("education", "biz_svcs"),
]

SECTOR_FALLBACK: dict[str, str] = {
    "Technology": "software",
    "Financial Services": "capital_mkts",
    "Healthcare": "health_svcs",
    "Energy": "oil_gas",
    "Utilities": "utilities",
    "Basic Materials": "materials",
    "Industrials": "industrials",
    "Consumer Cyclical": "consumer",
    "Consumer Defensive": "food_bev",
    "Communication Services": "media",
    "Real Estate": "reits",
}

# Cybersecurity has no industry code of its own — it lives inside
# "Software—Infrastructure", so it is override-only.
TICKER_OVERRIDES["cyber"] = {
    "CRWD", "PANW", "ZS", "S", "FTNT", "OKTA", "CYBR", "QLYS", "TENB",
    "RPD", "VRNS", "SAIL", "NET", "AKAM", "GEN",
}
for _t in TICKER_OVERRIDES["cyber"]:
    _OVERRIDE_LOOKUP.setdefault(_t, "cyber")


def classify(ticker: str, name: str = "", industry: str = "",
             sector: str = "") -> str:
    """Resolve one ticker to a theme id."""
    if ticker in _OVERRIDE_LOOKUP:
        return _OVERRIDE_LOOKUP[ticker]

    if industry:
        # yfinance writes "Software - Infrastructure"; normalise every dash
        # variant to a bare hyphen so one pattern form matches them all.
        ind = (industry.lower()
               .replace("—", "-").replace("–", "-").replace(" - ", "-"))
        for frag, theme in INDUSTRY_PATTERNS:
            if frag in ind:
                return theme

    if name:
        for theme, pat in NAME_PATTERNS:
            if pat.search(name):
                return theme

    if sector and sector in SECTOR_FALLBACK:
        return SECTOR_FALLBACK[sector]

    return "other"


def label(theme_id: str) -> str:
    return THEME_META.get(theme_id, THEME_META["other"])[0]


def order(theme_id: str) -> int:
    return THEME_META.get(theme_id, THEME_META["other"])[1]
