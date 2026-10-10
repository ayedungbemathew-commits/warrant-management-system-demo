import os
import unittest
from datetime import date, timedelta
from tests.common import S, E, DbTestCase, make_payload, default_style
import wms_weekly as WK


class Weekly(DbTestCase):
    def test_pending_ranges(self):
        self.assertEqual(WK.pending_ranges(date(2026, 10, 5), 0, None), [(date(2026, 9, 28), date(2026, 10, 4))])
        self.assertEqual(WK.pending_ranges(date(2026, 10, 5), 0, date(2026, 9, 20)),
                         [(date(2026, 9, 21), date(2026, 9, 27)), (date(2026, 9, 28), date(2026, 10, 4))])
        self.assertEqual(WK.pending_ranges(date(2026, 10, 5), 0, date(2026, 10, 4)), [])

    def test_manual_export_writes_pdf_and_summary(self):
        S.save_warrant(make_payload())
        today = date.today()
        res = WK.run_manual(today - timedelta(days=1), today + timedelta(days=1), os.path.join(self.tmp, "out"), default_style(), "created")
        self.assertEqual(res["skipped"], [])
        self.assertEqual(res["failed"], [])
        names = sorted(os.listdir(res["dir"]))
        self.assertIn("Export_Summary.txt", names)
        self.assertEqual(len([n for n in names if n.endswith(".pdf")]), 1)
        self.assertFalse([n for n in names if n.endswith(".part")])


if __name__ == "__main__":
    unittest.main()
