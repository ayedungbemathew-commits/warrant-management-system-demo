import copy
import os
import random
import unittest
from tests.common import E, make_payload, default_style, DbTestCase  # noqa: F401
import tempfile
from types import SimpleNamespace
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

WORDS = "Procurement of office furniture steel cabinet maintenance generators motor vehicle fuel training allowance salary".split()


def words(n, rnd):
    return " ".join(rnd.choice(WORDS) for _ in range(n))


class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wms_eng_")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.pdf = os.path.join(self.tmp, "w.pdf")

    def test_builds_both_types(self):
        for wt in ("Recurrent", "Capital"):
            sm = E.build_pdf(self.pdf, make_payload(wtype=wt, amounts=(1250000.5, 999.99)), default_style())
            self.assertGreaterEqual(sm["pages"], 1)
            with open(self.pdf, "rb") as pdf_file:
                self.assertTrue(pdf_file.read().startswith(b"%PDF"))
        self.assertEqual(os.listdir(self.tmp), ["w.pdf"])           # no temporary file left behind

    def test_large_row_padding_no_longer_crashes(self):          # 10 pt+ padding made the No. column negative-width
        for pad in (0.0, 3.0, 9.0, 12.0, 20.0):
            for wt in ("Recurrent", "Capital"):
                data = make_payload(wtype=wt, amounts=(5.0, 6.0, 7.0))
                data["table_fmt"].update(row_padding=pad, col_headings=True)
                E.build_pdf(self.pdf, data, default_style())

    def test_padx_is_unchanged_at_normal_settings(self):
        sp = E.Spec(make_payload(), default_style())
        self.assertEqual(sp.padx, sp.pad)

    def test_failed_build_leaves_nothing_and_keeps_old_pdf(self):
        E.build_pdf(self.pdf, make_payload(), default_style())
        with open(self.pdf, "rb") as pdf_file:
            good = pdf_file.read()
        data = make_payload()
        data["mdas"][0]["items"][0]["description"] = " ".join(["Maintenance"] * 600)
        with self.assertRaises(ValueError) as cm:
            E.build_pdf(self.pdf, data, default_style())
        self.assertIn("taller than a whole page", str(cm.exception))
        with open(self.pdf, "rb") as pdf_file:
            self.assertEqual(pdf_file.read(), good)                  # the earlier good file is untouched
        self.assertEqual(os.listdir(self.tmp), ["w.pdf"])

    def test_every_line_appears_once_and_totals_reconcile(self):
        rnd = random.Random(11)
        for it in range(40):
            wt = rnd.choice(["Recurrent", "Capital"])
            lay = E.default_layout(wt)
            if it % 3 == 0:
                lay.update(rnd.choice([{"row_padding": 12.0, "table_size": 14.0}, {"row_padding": 0.0, "table_size": 6.0},
                                       {"col_headings": True}, {"min_head_rows": 6, "min_tail_rows": 6}, {"letterhead_all_pages": True}]))
            mdas = []
            for _ in range(rnd.randint(1, 5)):
                items = [{"code": "9900000001/21010101", "payee": words(rnd.randint(1, 5), rnd), "description": words(rnd.choice([1, 4, 12, 40]), rnd),
                          "amount": round(rnd.uniform(1, 5e8), 2)} for _ in range(rnd.choice([1, 2, 10, 35, 80]))]
                mdas.append({"code": "9900000001", "name": words(rnd.randint(1, 12), rnd), "items": items})
            data = make_payload(wtype=wt)
            data["mdas"], data["table_fmt"] = mdas, lay
            sm = E.build_pdf(self.pdf, data, default_style())
            for i, m in enumerate(sm["mdas"]):
                covered = []
                for (_pg, a, b) in m["parts"]:
                    covered += list(range(a, b))
                self.assertEqual(covered, list(range(len(mdas[i]["items"]))), f"case {it} mda {i}")
                self.assertIsNotNone(m["total_page"])
                if not sm["layout"]["final_total_full"]:
                    self.assertAlmostEqual(sum(t for _p, t, _x in m["totals"]), sum(x["amount"] for x in mdas[i]["items"]), places=1)

    @unittest.skipUnless(PdfReader, "pypdf not installed")
    def test_printed_serials_and_lines_continue_across_pages_without_gaps(self):
        data = make_payload(amounts=tuple(5000000 + i * 1000000 for i in range(12)))
        names = ["ONUKA KALU UKWA", "FACILITY MANAGEMENT SERVICE OFFICE", "MINISTRY OF INFORMATION AND STRATEGY",
                 "DR IDIKA MBA IDIKA", "NEWS CENTRAL MEDIA LIMITED", "GOLD LINK COMMUNICATION LIMITED",
                 "XTRAMILE COMMUNICATIONS LTD", "BENEFICIARY EIGHT", "ARISE GLOBAL MEDIA LTD", "CHANNELS INCORP LTD",
                 "OFFICE OF THE SPECIAL ADVISER FERDINAND EKEOMA", "OFFICE OF SPECIAL ADVISER TO GOVERNOR ON MEDIA PUBLICITY"]
        for i, item in enumerate(data["mdas"][0]["items"]):
            item["payee"] = f"ROWMARK{i + 1:02d} {names[i]}"
            item["description"] = "Common Services (Committee / Commissions / Interventions)"
        sm = E.build_pdf(self.pdf, data, default_style())
        self.assertGreaterEqual(sm["pages"], 2)
        pages = PdfReader(self.pdf).pages
        text = "\n".join(p.extract_text() or "" for p in pages)
        serials = [line.strip() for line in text.splitlines() if line.strip().isdigit() and 1 <= int(line.strip()) <= 12]
        self.assertEqual(serials, [str(i) for i in range(1, 13)])
        for i in range(1, 13):
            self.assertEqual(text.count(f"ROWMARK{i:02d}"), 1, f"Schedule line {i} missing or duplicated in PDF")

    def test_missing_split_range_is_refused_before_pdf_can_be_saved(self):
        data = make_payload(amounts=(10.0, 20.0, 30.0))
        spec = SimpleNamespace(L={"final_total_full": False}, log=[
            {"mda": 0, "start": 0, "end": 1, "with_total": False, "total": 10.0},
            {"mda": 0, "start": 2, "end": 3, "with_total": True, "total": 30.0},
        ])
        with self.assertRaisesRegex(ValueError, "missing or repeated line"):
            E._verify_drawn_schedule(spec, data)


if __name__ == "__main__":
    unittest.main()
