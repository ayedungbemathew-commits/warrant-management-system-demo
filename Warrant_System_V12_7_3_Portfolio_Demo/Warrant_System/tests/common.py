"""Shared test helpers: every test works on a throw-away copy of the bundled database, never on real data."""
import copy
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import wms_engine as E          # noqa: E402
import wms_log as L             # noqa: E402
import wms_store as S           # noqa: E402

BUNDLED_DB = os.path.join(ROOT, "warrant_data.db")
ORG = "9900000001"


def make_payload(no="991006/26", date="06/10/2026", wtype="Recurrent", amounts=(100.0,), org=ORG, name="Demo Department of Community Services", payee="Sample Beneficiary"):
    items = [{"code": f"{org}/21010101", "payee": f"{payee} {i}", "description": "Salary", "amount": a} for i, a in enumerate(amounts, 1)]
    return {"warrant_no": no, "date": date, "office": "Demo Office", "city": "Sample City", "state": "Example State", "warrant_type": wtype,
            "year": date[-4:], "ref_prefix": "DEMO/REW", "addressee": "The Demo Accountant-General", "state_short": "Example State",
            "table_fmt": E.default_layout(wtype), "mdas": [{"code": org, "name": name, "items": items}]}


def default_style():
    return copy.deepcopy(E.DEFAULT_STYLE)


class DbTestCase(unittest.TestCase):
    """A fresh copy of the bundled database, initialised through wms_store."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wms_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db_path = os.path.join(self.tmp, "warrant_data.db")
        shutil.copy(BUNDLED_DB, self.db_path)
        L.setup(self.tmp, "test")
        S.reset_option_cache()
        S.init(self.connect)

    def connect(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA synchronous=FULL")
        return c

    def q(self, sql, *args):
        c = self.connect()
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()
