import json
import os
import shutil
import sqlite3
import unittest
from tests.common import E, S, ORG, DbTestCase, make_payload, BUNDLED_DB


class BundledData(unittest.TestCase):
    """The shipped database must be usable as delivered."""
    def setUp(self):
        self.c = sqlite3.connect(f"file:{BUNDLED_DB}?mode=ro", uri=True)
        self.addCleanup(self.c.close)

    def test_integrity(self):
        self.assertEqual(self.c.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_every_code_sits_under_its_own_mda(self):
        n = self.c.execute("SELECT COUNT(*) FROM budget WHERE organisation_economic_code NOT LIKE organisation_code||'/%'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_no_blank_or_duplicate_names(self):
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM budget WHERE TRIM(COALESCE(organisation_name,''))=''").fetchone()[0], 0)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM (SELECT organisation_code FROM budget GROUP BY 1 HAVING COUNT(DISTINCT organisation_name)>1)").fetchone()[0], 0)

    def test_tables_agree(self):
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM budget").fetchone()[0], self.c.execute("SELECT COUNT(*) FROM economic_codes").fetchone()[0])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM budget b JOIN economic_codes e USING(organisation_economic_code) WHERE b.description<>e.description").fetchone()[0], 0)


class RepairBudget(unittest.TestCase):
    def test_repair_fixes_known_defects_and_is_idempotent(self):
        c = sqlite3.connect(":memory:")
        c.executescript("""CREATE TABLE budget(organisation_code TEXT,organisation_name TEXT,organisation_economic_code TEXT,description TEXT);
            CREATE TABLE economic_codes(organisation_economic_code TEXT PRIMARY KEY,description TEXT);""")
        rows = [("0990000001", "Fictional  Administration ", "0990000001/21010101", "Salary"),
                ("990000001", "Fictional Administration", "0990000001/22020101", "Travel  and stuff"),    # lost leading zero
                ("9900000002", "Sample Social Services", "9900000002/22020101", "Misc"),
                ("9900000002", "Sample Social Services", "9900000003/21010101", "Salary"),                # filed under the wrong MDA
                ("9900000003", "", "9900000003/22020101", "Other"),                                       # blank name
                ("9900000003", "Demo Program Agency", "9900000003/22020102", "Other 2")]
        c.executemany("INSERT INTO budget VALUES(?,?,?,?)", rows)
        c.executemany("INSERT INTO economic_codes VALUES(?,?)", [(r[2], r[3]) for r in rows])
        out = S.repair_budget(c)
        self.assertEqual(out["rehomed"], 2)
        got = c.execute("SELECT organisation_code,organisation_name,organisation_economic_code,description FROM budget ORDER BY rowid").fetchall()
        self.assertEqual(got[1][:2], ("0990000001", "Fictional Administration"))
        self.assertEqual(got[3][:2], ("9900000003", "Demo Program Agency"))
        self.assertEqual(got[4][1], "Demo Program Agency")
        self.assertEqual(got[1][3], "Travel and stuff")
        self.assertEqual(c.execute("SELECT description FROM economic_codes WHERE organisation_economic_code='0990000001/22020101'").fetchone()[0], "Travel and stuff")
        self.assertEqual(S.repair_budget(c), {"rehomed": 0, "named": 0, "tidied": 0})
        self.assertEqual(c.execute("SELECT COUNT(*) FROM budget").fetchone()[0], 6)           # nothing deleted

    def test_unrelated_prefix_is_left_alone(self):
        c = sqlite3.connect(":memory:")
        c.executescript("""CREATE TABLE budget(organisation_code TEXT,organisation_name TEXT,organisation_economic_code TEXT,description TEXT);
            CREATE TABLE economic_codes(organisation_economic_code TEXT PRIMARY KEY,description TEXT);""")
        c.execute("INSERT INTO budget VALUES('0111','A','0999/1','x')")
        S.repair_budget(c)
        self.assertEqual(c.execute("SELECT organisation_code FROM budget").fetchone()[0], "0111")      # no guessing


class Validation(DbTestCase):
    def test_good_payload(self):
        self.assertEqual(S.validate_payload(make_payload()), [])

    def test_bad_amounts_are_refused(self):
        for bad in (float("nan"), float("inf"), -float("inf"), 0, -5, S.MAX_AMOUNT * 10, "abc", None):
            errs = S.validate_payload(make_payload(amounts=(bad,)))
            self.assertTrue(errs, f"{bad!r} was accepted")

    def test_parse_amount(self):
        self.assertEqual(S.parse_amount("N1,250,000.50"), 1250000.5)
        self.assertEqual(S.parse_amount("\u20a6 2,500"), 2500)
        for bad in ("nan", "inf", "-1", "0", "abc", "", "1e30"):
            with self.assertRaises(S.TemplateError):
                S.parse_amount(bad)


class Saving(DbTestCase):
    def test_save_duplicate_and_stale(self):
        r = S.save_warrant(make_payload())
        with self.assertRaises(S.WarrantError):
            S.save_warrant(make_payload())                                   # same warrant no.
        p2 = make_payload(amounts=(5.0,))
        r2 = S.save_warrant(p2, r["row"]["id"], r["row"]["updated_at"])
        self.assertEqual(r2["row"]["revision"], 2)
        with self.assertRaises(S.WarrantError):
            S.save_warrant(p2, r["row"]["id"], r["row"]["updated_at"])       # someone else saved in between
        self.assertEqual(len(S.search_warrants()), 1)

    def test_search(self):
        S.save_warrant(make_payload("991004/26"))
        S.save_warrant(make_payload("991005/26", "05/10/2026"))
        self.assertEqual(len(S.search_warrants("9910")), 1)
        self.assertEqual(len(S.search_warrants()), 2)


class Backups(DbTestCase):
    def test_verify_backup_with_awkward_folder_names(self):
        names = ["plain", "with space", "has#hash", "pct%20x"]
        if os.name != "nt":
            names.append("q?mark")                      # '?' is not allowed in Windows folder names, so it can only occur elsewhere
        for sub in names:
            d = os.path.join(self.tmp, sub); os.makedirs(d)
            p = os.path.join(d, "warrant_data.db"); shutil.copy(BUNDLED_DB, p)
            self.assertIsNone(S.verify_backup(p), sub)

    def test_verify_rejects_garbage(self):
        p = os.path.join(self.tmp, "junk.db")
        with open(p, "wb") as junk_file:
            junk_file.write(b"not a database at all")
        self.assertIsNotNone(S.verify_backup(p))

    def test_backup_and_restore_roundtrip(self):
        S.save_warrant(make_payload("991004/26"))
        bp = S.backup_now("manual")
        self.assertIsNone(S.verify_backup(bp))
        S.save_warrant(make_payload("991005/26", "05/10/2026"))
        self.assertEqual(len(S.search_warrants()), 2)
        S.restore_backup(bp)
        self.assertEqual(len(S.search_warrants()), 1)

    def test_settings_export_import_skips_markers(self):
        S.set_password("demo-test-passphrase"); S.unlock("demo-test-passphrase")
        p = os.path.join(self.tmp, "cfg.json")
        S.export_settings(p)
        with open(p, encoding="utf-8") as settings_file:
            data = json.load(settings_file)
        flat = json.dumps(data["body"]["settings"])
        for marker in ("date_format_v2", "date_format_v3", "budget_repair_v1", "app_version"):
            self.assertNotIn(marker, flat)
        S.import_settings(p)


class DateUpgrade(DbTestCase):
    """Dates are stored DD/MM/YYYY. Older databases are converted once, according to the version that wrote them."""
    def _plant(self, markers, rows):
        c = self.connect()
        for k in ("date_format_v2", "date_format_v3", "date_format_v4"):
            c.execute("DELETE FROM settings WHERE key=?", (k,))
        for k in markers:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?)", (k, "x"))
        for no, d in rows:
            blob = json.dumps({"warrant_no": no, "date": d, "year": "2026", "warrant_type": "Recurrent", "mdas": []})
            c.execute("INSERT INTO warrants(warrant_no,warrant_date,data_json,created_at,updated_at,warrant_type,grand_total) VALUES(?,?,?,?,?,?,?)",
                      (no, d, blob, "2026-10-01T10:00:00", "2026-10-01T10:00:00", "Recurrent", 1.0))
            c.execute("INSERT INTO warrant_revisions(warrant_id,warrant_no,data_json,replaced_at) SELECT id,warrant_no,data_json,'2026-10-01T10:00:00' FROM warrants WHERE warrant_no=?", (no,))
        c.commit(); c.close()

    def _dates(self):
        return [(r[0], json.loads(r[1])["date"]) for r in self.q("SELECT warrant_date,data_json FROM warrants ORDER BY id")]

    def test_v12_6_database_month_first_becomes_day_first(self):
        self._plant(["date_format_v2", "date_format_v3"], [("990930/26", "09/30/2026"), ("991004/26", "10/04/2026"), ("990305/26", "03/05/2026")])
        S.init(self.connect)
        self.assertEqual(self._dates(), [("30/09/2026",) * 2, ("04/10/2026",) * 2, ("05/03/2026",) * 2])
        self.assertEqual([json.loads(r[0])["date"] for r in self.q("SELECT data_json FROM warrant_revisions ORDER BY id")],
                         ["30/09/2026", "04/10/2026", "05/03/2026"])
        self.assertEqual([r[0] for r in self.q("SELECT warrant_no FROM warrants ORDER BY id")], ["990930/26", "991004/26", "990305/26"])   # numbers untouched

    def test_v12_5_0_two_digit_month_first(self):
        self._plant(["date_format_v2"], [("990930/26", "09/30/26")])
        S.init(self.connect)
        self.assertEqual(self._dates(), [("30/09/2026",) * 2])

    def test_pre_12_5_database_is_already_day_first(self):
        self._plant([], [("990930/26", "30/09/2026"), ("991004/26", "04/10/2026")])
        S.init(self.connect)
        self.assertEqual(self._dates(), [("30/09/2026",) * 2, ("04/10/2026",) * 2])             # nothing swapped

    def test_runs_only_once(self):
        self._plant(["date_format_v2", "date_format_v3"], [("991004/26", "10/04/2026")])
        S.init(self.connect)
        S.init(self.connect)
        S.init(self.connect)
        self.assertEqual(self._dates(), [("04/10/2026",) * 2])                                  # a second pass would have swapped it back

    def test_an_old_backup_is_upgraded_when_restored(self):
        self._plant(["date_format_v2", "date_format_v3"], [("991004/26", "10/04/2026")])
        old = os.path.join(self.tmp, "old.db")
        c = self.connect(); b = sqlite3.connect(old); c.backup(b); b.close(); c.close()
        S.init(self.connect)                                                                      # current DB upgraded
        S.restore_backup(old)                                                                     # restoring the month-first copy
        self.assertEqual(self._dates(), [("04/10/2026",) * 2])

    def test_new_warrants_are_saved_day_first(self):
        S.save_warrant(make_payload(date="04/10/2026"))
        self.assertEqual(self._dates(), [("04/10/2026",) * 2])
        short = make_payload("991005/26", date="05/10/2026"); short["date"] = "5/10/26"           # typed short
        S.save_warrant(short)
        self.assertEqual(self._dates()[1], ("05/10/2026",) * 2)
        self.assertTrue(S.validate_payload(make_payload(date="10/25/2026")))                       # month 25 does not exist
        self.assertEqual(len(S.search_warrants(date="4/10/26")), 1)


class SavedItemsFollowTheirMda(DbTestCase):
    def setUp(self):
        super().setUp()
        S.set_password("demo-test-passphrase"); S.unlock("demo-test-passphrase")
        S.save_template(ORG, "Salary", f"{ORG}/21010101", "Salary")

    def test_recode_moves_saved_items(self):
        S.change_mda_code(ORG, "0111001999")
        t = S.list_templates()
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["org_code"], "0111001999")
        self.assertTrue(t[0]["economic_code"].startswith("0111001999/"))

    def test_delete_mda_removes_saved_items(self):
        S.delete_mda(ORG)
        self.assertEqual(S.list_templates(), [])

    def test_delete_code_removes_saved_items(self):
        S.delete_code(f"{ORG}/21010101")
        self.assertEqual(S.list_templates(), [])


if __name__ == "__main__":
    unittest.main()

