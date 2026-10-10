import unittest
from tests.common import E, S, DbTestCase  # noqa: F401
import wms_dates as WD


class Dates(unittest.TestCase):
    def test_day_first_parse_and_format(self):
        d = WD.parse_input("4/10/26")
        self.assertEqual((d.day, d.month, d.year), (4, 10, 2026))                 # 4 October
        self.assertEqual(WD.fmt(d), "04/10/2026")
        self.assertEqual(WD.normalise("25/10/2026"), "25/10/2026")
        self.assertEqual(WD.normalise("2026-10-04"), "04/10/2026")
        self.assertIsNone(WD.parse_input("10/25/2026"))                          # month 25: rejected, not silently swapped
        self.assertIsNone(WD.parse_input("not a date"))
        self.assertIsNone(WD.parse_input("32/01/2026"))

    def test_stored_dates(self):
        self.assertEqual(WD.parse_stored("30/09/2026").month, 9)
        self.assertEqual(WD.parse_stored("30/09/26").year, 2026)
        self.assertEqual(WD.year_of("04/10/2026"), 2026)

    def test_month_first_text_from_older_versions(self):
        self.assertEqual(WD.from_mdy("09/30/2026"), "30/09/2026")
        self.assertEqual(WD.from_mdy("10/04/26"), "04/10/2026")
        self.assertIsNone(WD.from_mdy("30/09/2026"))                              # not month-first: left for the caller

    def test_with_year_and_leap_day(self):
        self.assertEqual(WD.with_year("29/02/2028", 2027), "28/02/2027")
        self.assertEqual(WD.with_year("04/10/2026", 2027), "04/10/2027")

    def test_warrant_number_still_lists_month_then_day(self):
        d = WD.parse_input("04/10/2026")
        self.assertEqual(WD.build_no("99", d), "991004/26")
        self.assertTrue(WD.no_matches_date("991004/26", d))
        self.assertEqual(WD.build_no_from_text("99", "4/10/2026"), "991004/26")


class AmountWords(unittest.TestCase):
    def test_normal(self):
        self.assertIn("Naira", E.amount_words(1250999.5, "Recurrent"))
        self.assertIn("Naira", E.amount_words(1250999.5, "Capital"))

    def test_trillions_do_not_crash(self):                      # used to raise KeyError from 1 trillion
        for wt in ("Recurrent", "Capital"):
            for v in (10 ** 12, 10 ** 12 + 5, 5 * 10 ** 14 + 0.5, S.MAX_AMOUNT):
                self.assertTrue(E.amount_words(v, wt).strip(), (wt, v))
        self.assertIn("trillion", E.amount_words(10 ** 12, "Recurrent").lower())

    def test_zero_is_not_blank(self):
        self.assertTrue(E.amount_words(0, "Recurrent").lower().startswith("zero"))
        self.assertTrue(E.amount_words(0, "Capital").lower().startswith("zero"))

    def test_kobo_is_introduced_by_and_in_both_types(self):
        for wt in ("Recurrent", "Capital"):
            w = E.amount_words(5580278419.32, wt)
            self.assertIn("Naira and Thirty-Two Kobo", w, wt)
            self.assertNotIn("Naira Thirty", w, wt)
        self.assertNotIn("Kobo", E.amount_words(1000.00, "Capital"))             # no kobo, no kobo wording

    def test_authorization_punctuation_is_identical_in_both_types(self):
        for wt in ("Recurrent", "Capital"):
            t = E.authorization_text(wt, 44455.25, 2026, "Example State")
            self.assertIn("Kobo (\u20a644,455.25) Only, being part of the fund", t.replace("funds", "fund"), wt)
            self.assertNotIn(".(", t)                                              # no stray full stop before the bracket
            self.assertNotIn(")Only", t)                                           # a space after the bracket
            self.assertNotIn("  ", t)                                              # no double spaces

    def test_known_values(self):
        self.assertEqual(E.capital_money_words(1000).split()[0:2], ["One", "Thousand"])


class WarrantNumbers(DbTestCase):
    def test_wno_settings_check(self):
        self.assertIsNone(S.check_wno_settings("", ""))
        self.assertIsNone(S.check_wno_settings(r"\d{6}/\d{2}", "990930/26"))
        self.assertIn("not a valid pattern", S.check_wno_settings("(", "1"))
        self.assertIn("does not match", S.check_wno_settings(r"\d{6}/\d{2}", "12"))

    def test_suggest_prefix_returns_two_digits_or_blank(self):
        from datetime import date
        p = S.suggest_prefix(date(2026, 10, 4), "99")
        self.assertRegex(p or "00", r"^\d{2}$")


if __name__ == "__main__":
    unittest.main()

