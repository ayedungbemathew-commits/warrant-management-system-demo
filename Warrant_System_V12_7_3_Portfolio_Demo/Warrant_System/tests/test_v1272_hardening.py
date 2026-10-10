"""V12.7.2: saved warrants stay printable after catalogue / settings changes; start-up survives a failed upgrade backup; input hardening."""
import json
import os
import unittest

from tests.common import E, S, ORG, DbTestCase, default_style, make_payload


class HistoricalWarrantTests(DbTestCase):
    def _save(self):
        S.set_password("demo-test-passphrase"); S.unlock("demo-test-passphrase")
        return S.save_warrant(make_payload())["row"]["id"]

    def _data(self, wid):
        return S.load_warrant(wid)["data"]

    def test_recoded_mda_old_warrant_still_prints_but_new_save_is_strict(self):
        wid = self._save()
        S.change_mda_code(ORG, "0111999999")
        d = self._data(wid)
        self.assertTrue(S.validate_payload(d))                                   # full check still refuses it (editing / saving)
        self.assertEqual(S.validate_payload(d, historical=True), [])             # reprint / export / share / weekly export allowed
        self.assertEqual(E.build_pdf(os.path.join(self.tmp, "o.pdf"), d, default_style())["pages"], 1)

    def test_deleted_code_and_changed_settings_do_not_block_reprint(self):
        wid = self._save()
        S.delete_code(f"{ORG}/21010101")
        S.put("warrant_no_pattern", r"\d{6}/\d{4}")
        S.set_code_rules("18=Capital, 30=Recurrent")
        self.assertEqual(S.validate_payload(self._data(wid), historical=True), [])

    def test_historical_still_rejects_real_print_errors(self):
        d = make_payload()
        d["mdas"][0]["items"][0]["amount"] = 0
        self.assertTrue(S.validate_payload(d, historical=True))
        d = make_payload(); d["mdas"][0]["items"][0]["payee"] = ""
        self.assertTrue(S.validate_payload(d, historical=True))
        d = make_payload(); d["mdas"] = []
        self.assertTrue(S.validate_payload(d, historical=True))
        d = make_payload(); d["warrant_no"] = ""
        self.assertTrue(S.validate_payload(d, historical=True))

    def test_save_is_always_strict(self):
        S.set_password("demo-test-passphrase"); S.unlock("demo-test-passphrase")
        p = make_payload(); p["mdas"][0]["items"][0]["code"] = f"{ORG}/99999999"
        with self.assertRaises(S.WarrantError):
            S.save_warrant(p)

    def test_import_rejects_a_warrant_type_without_pdf_wording(self):
        S.set_password("demo-test-passphrase"); S.unlock("demo-test-passphrase")
        path = os.path.join(self.tmp, "settings.json")
        S.export_settings(path)
        with open(path, encoding="utf-8") as settings_file:
            doc = json.load(settings_file)
        doc["body"]["settings"]["warrant_types"] = "Supplementary"
        doc["checksum"] = S._body_digest(doc["body"])
        with open(path, "w", encoding="utf-8") as settings_file:
            json.dump(doc, settings_file)
        with self.assertRaisesRegex(ValueError, "can print only Recurrent and Capital"):
            S.import_settings(path, parts=("settings",))
        self.assertEqual(S.get("warrant_types"), "Recurrent,Capital")


class StartupBackupTests(DbTestCase):
    def test_failed_upgrade_backup_does_not_block_startup_when_no_conversion_pending(self):
        S.put("app_version", "12.0.0")
        S.put("backup_dir", "/proc/no_such_drive/backups")
        S.init(self.connect)                                                    # must not raise
        self.assertIn("could not be made", S.startup_warning)
        S.put("backup_dir", "")

    def test_failed_backup_still_blocks_when_a_conversion_is_pending(self):
        S.put("app_version", "12.0.0")
        c = S._c(); c.execute("DELETE FROM settings WHERE key='date_format_v4'"); c.commit(); c.close()
        S.put("backup_dir", "/proc/no_such_drive/backups")
        with self.assertRaises(Exception):
            S.init(self.connect)
        S.put("backup_dir", "")

    def test_catalog_repair_requires_a_backup_even_without_saved_warrants(self):
        c = S._c()
        c.execute("DROP TABLE warrants")
        c.execute("DELETE FROM settings WHERE key='budget_repair_v1'")
        c.commit(); c.close()
        self.assertTrue(S._migration_pending())


class InputHardeningTests(DbTestCase):
    def test_amount_formats_are_refused_not_guessed(self):
        for bad in ("1.250,50", "1_000", "1e3", "1,25", "12,34,567", "1,250,00", "abc", "", "-5"):
            with self.assertRaises(S.TemplateError, msg=bad):
                S.parse_amount(bad)
        self.assertEqual(S.parse_amount("N1,250,000.50"), 1250000.5)
        self.assertEqual(S.parse_amount("1,250.5"), 1250.5)

    def test_only_print_supported_warrant_types_are_accepted(self):
        d = make_payload(); d["warrant_type"] = "Supplementary"
        self.assertTrue(S.validate_payload(d))
        self.assertTrue(S.validate_payload(d, historical=True))
        d = make_payload(); d["warrant_type"] = "Capital"
        S.put("warrant_types", "Recurrent")
        self.assertEqual(S.validate_payload(d, historical=True), [])
        self.assertTrue(S.validate_payload(d))
        pdf = os.path.join(self.tmp, "saved_capital.pdf")
        E.build_pdf(pdf, d, default_style())
        try:
            from pypdf import PdfReader
        except ImportError:
            return
        text = " ".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
        self.assertIn("Capital Development Fund", text)
        self.assertNotIn("Consolidated Revenue Fund", text)

    def test_layout_cannot_be_broken_by_infinite_or_huge_values(self):
        inf = float("inf")
        lay = E.norm_layout("Recurrent", {"pag_rules_v": 5, "letterhead_lines": inf, "col_px": [inf, 100, 100, 100, 99999]})
        self.assertTrue(all(0 < c <= E.MAX_COL_PX for c in lay["col_px"]))
        self.assertEqual(lay["letterhead_lines"], E.default_layout("Recurrent")["letterhead_lines"])

    def test_state_name_with_markup_characters_prints_literally(self):
        p = make_payload(); p["state_short"] = "Example & <Fictional> State"
        import tempfile
        out = os.path.join(tempfile.mkdtemp(), "s.pdf")
        E.build_pdf(out, p, default_style())                                    # must not raise
        try:
            from pypdf import PdfReader
        except ImportError:
            return
        text = " ".join(pg.extract_text() or "" for pg in PdfReader(out).pages)
        self.assertIn("Fictional", text)


if __name__ == "__main__":
    unittest.main()

