import os
import unittest
from tests.common import S, E, DbTestCase, make_payload
import wms_share as SH


class Share(DbTestCase):
    def setUp(self):
        super().setUp()
        self.row = S.save_warrant(make_payload())["row"]
        self.path = os.path.join(self.tmp, "x.wmsw")

    def test_roundtrip(self):
        SH.write(self.path, SH.build(self.row, "t", style={"Recurrent": {"a": 1}}))
        env = SH.read(self.path)
        self.assertTrue(SH.same_content(self.row["data"], env["warrant"]))
        self.assertIn("991006/26", SH.describe(env))

    def test_new_files_are_version_2_day_first(self):
        env = SH.build(self.row, "t")
        self.assertEqual(env["format_version"], 2)
        self.assertEqual(env["warrant"]["date"], "04/10/2026")
        self.assertIn("04/10/2026", SH.describe(env))

    def test_old_month_first_file_is_converted_on_open(self):
        env = SH.build(self.row, "t")
        env["format_version"] = 1                                  # what V12.5 - V12.6 wrote
        env["warrant"]["date"] = env["summary"]["date"] = "10/04/2026"
        env["checksum"] = SH.checksum(env)
        SH.write(self.path, env)
        got = SH.read(self.path)
        self.assertEqual(got["warrant"]["date"], "04/10/2026")      # 10/04 month-first = 4 October
        self.assertEqual(got["summary"]["date"], "04/10/2026")
        self.assertTrue(SH.same_content(self.row["data"], got["warrant"]))

    def test_a_newer_format_is_refused(self):
        env = SH.build(self.row, "t")
        env["format_version"] = 3
        env["checksum"] = SH.checksum(env)
        SH.write(self.path, env)
        with self.assertRaises(SH.ShareFileError):
            SH.read(self.path)

    def test_tampering_is_refused(self):
        SH.write(self.path, SH.build(self.row, "t"))
        with open(self.path, encoding="utf-8") as share_file:
            raw = share_file.read()
        self.assertIn('"amount":100.0', raw)
        with open(self.path, "w", encoding="utf-8") as share_file:
            share_file.write(raw.replace('"amount":100.0', '"amount":900.0'))
        with self.assertRaises(SH.ShareFileError):
            SH.read(self.path)

    def test_truncated_file_is_refused(self):
        SH.write(self.path, SH.build(self.row, "t"))
        with open(self.path, encoding="utf-8") as share_file:
            raw = share_file.read()
        with open(self.path, "w", encoding="utf-8") as share_file:
            share_file.write(raw[: len(raw) // 2])
        with self.assertRaises(SH.ShareFileError):
            SH.read(self.path)

    def test_not_json_is_refused(self):
        with open(self.path, "w") as share_file:
            share_file.write("hello")
        with self.assertRaises(SH.ShareFileError):
            SH.read(self.path)


if __name__ == "__main__":
    unittest.main()
