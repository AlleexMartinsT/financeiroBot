import unittest

from sheet_registry import DEFAULT_SHEET_IDS, SHEET_KEYS, sheet_ids_from_config


class SheetRegistryTests(unittest.TestCase):
    def test_provides_2027_sheets_when_private_config_has_not_been_updated(self):
        sheet_ids = sheet_ids_from_config(
            {
                "EH_2025": "eh-2025",
                "EH_2026": "eh-2026",
                "MVA_2025": "mva-2025",
                "MVA_2026": "mva-2026",
            }
        )

        self.assertEqual(
            sheet_ids["MVA_2027"],
            "1Xga252VNtTS9GwauZNhXkbBHNsvN75FFYrYM5tiO8dU",
        )
        self.assertEqual(
            sheet_ids["EH_2027"],
            "1BayYx0I-gqcXpwNPiswAbQDklgcC3EfefHCWbwjzmKs",
        )
        self.assertIn("EH_2027", SHEET_KEYS)
        self.assertIn("MVA_2027", SHEET_KEYS)

    def test_allows_private_config_to_override_the_default_2027_sheet(self):
        sheet_ids = sheet_ids_from_config({"MVA_2027": "configured-sheet"})

        self.assertEqual(sheet_ids["MVA_2027"], "configured-sheet")
        self.assertEqual(
            sheet_ids["EH_2027"],
            DEFAULT_SHEET_IDS["EH_2027"],
        )
