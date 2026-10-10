"""Release hygiene: version, change log, installer script, build scripts and the app's own tables must agree."""
import ast
import os
import py_compile
import re
import unittest
from tests.common import ROOT
import wms_version as V


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8", errors="replace") as fh:
        return fh.read()


class Release(unittest.TestCase):
    def test_changelog_top_entry_is_this_version(self):
        m = re.search(r"^## (\d+\.\d+\.\d+) \((\d{4}-\d{2}-\d{2})\)", read("CHANGELOG.md"), re.M)
        self.assertEqual(m.group(1), V.VERSION)
        self.assertEqual(m.group(2), V.RELEASE_DATE)

    def test_installer_takes_version_from_wms_version(self):
        iss = read("installer", "WarrantSystem.iss")
        self.assertIn("MyAppVersion", iss)
        self.assertNotIn(V.VERSION, iss)                                  # never hard-coded there
        self.assertIn("wms_version", read("Build Installer.bat"))

    def test_every_module_compiles(self):
        for f in os.listdir(ROOT):
            if f.endswith(".py"):
                compile(read(f), f, "exec")                              # syntax check without writing .pyc files

    def test_files_named_in_build_scripts_exist(self):
        for bat in ("Build Installer.bat", "Build Portable App.bat"):
            text = read(bat)
            for src in re.findall(r'--add-data "([^;"]+);', text):
                self.assertTrue(os.path.exists(os.path.join(ROOT, src)), f"{bat}: {src} is missing")
        self.assertTrue(os.path.isdir(os.path.join(ROOT, "tests")))
        for needed in ("requirements.txt", "warrant_data.db", "payees_seed.txt", "CHANGELOG.md"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, needed)), needed)

    def test_no_month_first_wording_left_in_code(self):
        for f in os.listdir(ROOT):
            if f.endswith(".py") and f not in ("wms_dates.py", "wms_share.py", "wms_store.py"):
                self.assertNotIn("MM/DD", read(f), f)
                self.assertNotIn("%m/%d", read(f), f)

    def test_portable_build_bundles_changelog(self):
        self.assertIn("CHANGELOG.md", read("Build Portable App.bat"))

    def test_fallback_theme_exists(self):
        tree = ast.parse(read("warrant_app_v12.py"))
        themes, fallback = set(), None
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id == "THEMES" and isinstance(node.value, ast.Dict):
                    themes = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
                if node.targets[0].id == "LIGHT_FALLBACK":
                    fallback = node.value.value
        self.assertIn("Carbon", themes)
        self.assertIn(fallback, themes, "LIGHT_FALLBACK must be one of the real themes (System/auto in light mode crashed otherwise)")


if __name__ == "__main__":
    unittest.main()
