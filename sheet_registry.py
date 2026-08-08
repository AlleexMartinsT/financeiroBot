DEFAULT_SHEET_IDS = {
    "MVA_2027": "1Xga252VNtTS9GwauZNhXkbBHNsvN75FFYrYM5tiO8dU",
    "EH_2027": "1BayYx0I-gqcXpwNPiswAbQDklgcC3EfefHCWbwjzmKs",
}

SHEET_KEYS = (
    "EH_2025",
    "EH_2026",
    "EH_2027",
    "MVA_2025",
    "MVA_2026",
    "MVA_2027",
)


def sheet_ids_from_config(configured_sheet_ids):
    return {**DEFAULT_SHEET_IDS, **dict(configured_sheet_ids or {})}
