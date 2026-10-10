"""
wms_store.py -- everything configurable lives in the database, not in code.

  * settings        : key/value table (default year, office/city/state, Ref prefixes, numbering prefixes, password hash ...)
  * fiscal_years    : the years offered in the Year drop-down (any 4-digit year can also be typed)
  * warrant number  : a new warrant is pre-filled with the last saved Warrant No. (fully editable, nothing fixed)
  * MDA security    : PBKDF2-hashed password, session unlock with time-out, lock-out after repeated wrong tries,
                      and every MDA / economic-code change goes through functions here that REFUSE to run while locked
  * mda_audit       : who-changed-what log for every MDA edit

No GUI code here, so it can be tested on its own. Call init(connect) once, where `connect` returns a sqlite3
connection (row_factory = sqlite3.Row).
"""
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import time
import uuid
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

import wms_dates as WD
from wms_version import VERSION as APP_VERSION

log = logging.getLogger("wms")
log.addHandler(logging.NullHandler())          # silent unless wms_log.setup() attaches the log file

_connect = None

# First-run seed values. They are copied into the `settings` table ONLY when a key is missing there,
# after which the database value is the one used (and it is editable in the Settings window).
SEED_SETTINGS = {
    "office": "Demo Office of the Accountant-General",
    "city": "Sample City",
    "state": "Example State of Nigeria",
    "state_short": "Example State",
    "addressee": "The Demo Accountant-General",
    "ministry": "Demo Ministry of Finance",
    "ref_prefix:Recurrent": "DEMO/REW",
    "ref_prefix:Capital": "DEMO/CEW",
}

# First-run rule seed: an economic code with 18 DIGITS is a Recurrent code and one with 30 DIGITS is a Capital code.
# After the first run the table `code_length_rules` is the only source.
SEED_CODE_RULES = ((18, "Recurrent"), (30, "Capital"))
SEED_SETTINGS["code_rules_strict"] = "0"       # 1 = also refuse codes whose digit count matches no rule
SEED_SETTINGS["warrant_types"] = "Recurrent,Capital"
SEED_SETTINGS["warrant_no_pattern"] = r"\d{6}/\d{2}"   # regex a Warrant No. must match (Settings): 8 digits as 6 digits / 2 digits; "" = only basic checks
SEED_SETTINGS["warrant_no_example"] = "991006/26"       # shown in the "not correct" message

YEAR_MIN, YEAR_MAX = 2000, 2100
MAX_AMOUNT = 10 ** 15 - 0.01            # no single schedule amount may reach one quadrillion Naira (also the limit of the amount-in-words routines)
PBKDF2_ITERS = 200_000
SESSION_SECONDS = 300          # an unlocked MDA session lasts 5 minutes after the last MDA action
MAX_FAILS = 5
LOCK_SECONDS = 60


class LockedOut(Exception):
    def __init__(self, seconds):
        super().__init__(f"Too many wrong passwords. Try again in {seconds} second(s).")
        self.seconds = seconds


def init(connect):
    global _connect, _PAYEE_UPPER
    _connect = connect
    _PAYEE_UPPER = None                  # re-read the payee-case option for this database
    global startup_warning
    startup_warning = None
    try:
        pre_upgrade_backup()             # copy the database BEFORE any upgrade step touches it
    except Exception as exc:
        # A full disk / unreachable backup folder must not lock the person out of the program. But when a data conversion
        # (dates, MDA repair) is still waiting, running it WITHOUT the safety copy is not acceptable: stop with the reason.
        if _migration_pending():
            raise
        startup_warning = f"The copy of the database before the upgrade could not be made: {exc}"
        log.warning("PRE-UPGRADE BACKUP FAILED (start-up continues, no data conversion pending): %s", exc)
    ensure_schema()
    invalidate_payees()                  # a new connection/database must not inherit another database's cache
    put("app_version", APP_VERSION)


startup_warning = None


def _migration_pending():
    """True when this database still needs a one-time data conversion (so a safety copy must exist first)."""
    c = _c()
    try:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        have = ({r[0] for r in c.execute("SELECT key FROM settings WHERE key IN ('date_format_v4','budget_repair_v1')")}
                if "settings" in tables else set())
        dates_pending = bool({"warrants", "warrant_revisions"} & tables) and "date_format_v4" not in have
        budget_pending = "budget" in tables and "budget_repair_v1" not in have
        return dates_pending or budget_pending
    finally:
        c.close()


def _c():
    return _connect()


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------
def ensure_schema():
    c = _c()
    c.execute("""CREATE TABLE IF NOT EXISTS warrants(
      id INTEGER PRIMARY KEY AUTOINCREMENT, warrant_no TEXT UNIQUE, warrant_date TEXT,
      office TEXT, city TEXT, state TEXT, grand_total REAL, status TEXT, data_json TEXT,
      created_at TEXT, updated_at TEXT, warrant_type TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS warrant_revisions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, warrant_id INTEGER, warrant_no TEXT,
      data_json TEXT, replaced_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rev_warrant ON warrant_revisions(warrant_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rev_no ON warrant_revisions(warrant_no)")
    c.execute("CREATE TABLE IF NOT EXISTS code_length_rules(digits INTEGER PRIMARY KEY, warrant_type TEXT NOT NULL)")
    c.execute("DROP TABLE IF EXISTS code_type_rules")      # V12.1 prefix rules were replaced by digit-count rules
    c.execute("""CREATE TABLE IF NOT EXISTS line_templates(
      id INTEGER PRIMARY KEY AUTOINCREMENT, org_code TEXT NOT NULL, name TEXT NOT NULL, economic_code TEXT NOT NULL, description TEXT NOT NULL,
      created_at TEXT, updated_at TEXT, last_used_at TEXT, use_count INTEGER NOT NULL DEFAULT 0)""")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_tpl_name ON line_templates(org_code, name COLLATE NOCASE)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_tpl_item ON line_templates(org_code, economic_code, description COLLATE NOCASE)")
    c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS fiscal_years(year INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE IF NOT EXISTS payees(id INTEGER PRIMARY KEY AUTOINCREMENT, payee TEXT UNIQUE, last_used_at TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS mda_audit(
      id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, action TEXT, detail TEXT)""")
    for ddl in ("ALTER TABLE warrants ADD COLUMN fiscal_year INTEGER",
                "ALTER TABLE warrants ADD COLUMN warrant_type TEXT",
                "ALTER TABLE warrants ADD COLUMN original_warrant_no TEXT",
                "ALTER TABLE warrants ADD COLUMN uid TEXT"):
        try:
            c.execute(ddl)
        except Exception:
            pass                               # column already there
    if c.execute("SELECT 1 FROM settings WHERE key='wno_format_v1'").fetchone() is None:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('warrant_no_pattern',?)", (SEED_SETTINGS["warrant_no_pattern"],))
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('warrant_no_example',?)", (SEED_SETTINGS["warrant_no_example"],))
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('wno_format_v1','1')")
    # The Warrant No. a warrant was FIRST saved under never changes, so it can always be found by it.
    c.execute("""UPDATE warrants SET original_warrant_no = COALESCE(
                   (SELECT r.warrant_no FROM warrant_revisions r WHERE r.warrant_id=warrants.id ORDER BY r.id ASC LIMIT 1),
                   warrant_no) WHERE original_warrant_no IS NULL""")
    # Every warrant has a permanent identity (uid). It travels inside shared warrant files, so opening a file for a warrant you
    # already have finds your record instead of making a second one.
    for r in c.execute("SELECT id FROM warrants WHERE uid IS NULL OR uid=''").fetchall():
        c.execute("UPDATE warrants SET uid=? WHERE id=?", (new_uid(), r["id"]))
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_warrant_uid ON warrants(uid)")
    _migrate_dates(c)
    if c.execute("SELECT 1 FROM settings WHERE key='budget_repair_v1'").fetchone() is None:
        fixed = repair_budget(c)
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('budget_repair_v1',?)", (datetime.now().isoformat(timespec="seconds"),))
        if any(fixed.values()):
            _audit(c, "REPAIR MDA DATA", ", ".join(f"{k}={v}" for k, v in fixed.items()))
            log.info("MDA data repaired: %s", fixed)
    # Economic-code -> warrant-type rules (number of DIGITS in the full code): seeded once, then editable (never re-seeded).
    if c.execute("SELECT 1 FROM settings WHERE key='code_len_rules_seeded'").fetchone() is None:
        for n, wt in SEED_CODE_RULES:
            c.execute("INSERT OR IGNORE INTO code_length_rules(digits,warrant_type) VALUES(?,?)", (n, wt))
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('code_len_rules_seeded','1')")
    for k, v in SEED_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    # give every older warrant its year (from what it was saved with, else from its date)
    rows = c.execute("SELECT id,warrant_date,data_json FROM warrants WHERE fiscal_year IS NULL").fetchall()
    for r in rows:
        y = None
        try:
            y = parse_year(json.loads(r["data_json"] or "{}").get("year"))
        except Exception:
            pass
        if y is None:
            y = year_from_date(r["warrant_date"])
        if y is not None:
            c.execute("UPDATE warrants SET fiscal_year=? WHERE id=?", (y, r["id"]))
    c.commit()
    c.close()


def new_uid():
    return uuid.uuid4().hex


def _squash(text):
    return " ".join(str(text if text is not None else "").replace("\u00a0", " ").split())


def repair_budget(c):
    """One-time, safe clean-up of the MDA / economic-code tables (idempotent: a second run changes nothing). Returns counts.
      * a row whose economic code starts with ANOTHER MDA code is moved to the MDA the code names -- but only when that MDA exists or
        the two codes differ just by leading zeros (Excel drops them: 125009001 for 0125009001). Such a row could never be used on a
        warrant ('economic code belongs to a different MDA').
      * an MDA name that is blank takes the name the MDA's other rows carry; double / trailing spaces in names and descriptions go.
    Nothing is deleted and no economic code is renamed."""
    out = {"rehomed": 0, "named": 0, "tidied": 0}
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='budget'").fetchone():
        return out
    rows = c.execute("SELECT rowid,organisation_code,organisation_name,organisation_economic_code,description FROM budget").fetchall()
    seen = Counter()
    for r in rows:
        if _squash(r[2]):
            seen[(r[1], _squash(r[2]))] += 1
    best = {}
    for (org, nm), n in seen.most_common():
        best.setdefault(org, nm)                                   # the most frequent name of each MDA
    orgs = {r[1] for r in rows}
    for rid, org, name, code, dsc in rows:
        org2 = org
        prefix = str(code or "").split("/", 1)[0] if "/" in str(code or "") else None
        if prefix and prefix != org and (prefix in orgs or str(prefix).lstrip("0") == str(org or "").lstrip("0")):
            org2 = prefix
        name2 = best.get(org2) or best.get(org) or _squash(name)
        dsc2 = _squash(dsc)
        if (org2, name2, dsc2) != (org, name, dsc):
            c.execute("UPDATE budget SET organisation_code=?,organisation_name=?,description=? WHERE rowid=?", (org2, name2, dsc2, rid))
            if org2 != org: out["rehomed"] += 1
            elif not _squash(name) and name2: out["named"] += 1
            else: out["tidied"] += 1
    for code, dsc in c.execute("SELECT organisation_economic_code,description FROM economic_codes").fetchall():
        if _squash(dsc) != dsc:
            c.execute("UPDATE economic_codes SET description=? WHERE organisation_economic_code=?", (_squash(dsc), code))
    return out


def _migrate_dates(c):
    """Bring every stored warrant date to DD/MM/YYYY, once (marker date_format_v4). Warrant Nos. are never touched.
    What the dates look like now depends on which version wrote them:
      * markers date_format_v2 / v3 present  -> V12.5 - V12.6: MM/DD/YY or MM/DD/YYYY  -> converted to DD/MM/YYYY
      * no marker at all                     -> a pre-12.5 database: already DD/MM/YYYY   -> only tidied (e.g. 2-digit years)
    A backup is taken before this runs (pre_upgrade_backup)."""
    if c.execute("SELECT 1 FROM settings WHERE key='date_format_v4'").fetchone():
        return
    mdy_era = c.execute("SELECT 1 FROM settings WHERE key IN ('date_format_v2','date_format_v3')").fetchone() is not None
    changed = 0

    def canonical(text):
        if not text:
            return text
        if mdy_era:
            return WD.from_mdy(text) or text
        return WD.normalise(text) or text

    def fix(table, has_col):
        n = 0
        cols = "id,data_json" + (",warrant_date" if has_col else "")
        for r in c.execute(f"SELECT {cols} FROM {table}").fetchall():
            try:
                data = json.loads(r["data_json"] or "{}")
            except ValueError:
                data = None
            old_in = data.get("date") if isinstance(data, dict) else None
            new_in = canonical(old_in) if old_in else old_in
            old_col = r["warrant_date"] if has_col else None
            new_col = canonical(old_col) if has_col and old_col else old_col
            data_changed = isinstance(data, dict) and new_in != old_in
            col_changed = has_col and new_col != old_col
            if not data_changed and not col_changed:
                continue
            if data_changed:
                data["date"] = new_in
            if has_col:
                c.execute(f"UPDATE {table} SET warrant_date=?, data_json=? WHERE id=?",
                          (new_col, json.dumps(data) if data_changed else r["data_json"], r["id"]))
            else:
                if data_changed:
                    c.execute(f"UPDATE {table} SET data_json=? WHERE id=?", (json.dumps(data), r["id"]))
            n += 1
        return n

    changed += fix("warrants", True)
    changed += fix("warrant_revisions", False)
    now = datetime.now().isoformat(timespec="seconds")
    for key in ("date_format_v2", "date_format_v3", "date_format_v4"):
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, now))
    if changed:
        log.info("DATES converted to DD/MM/YYYY: %d record(s)", changed)


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
def get(key, default=None):
    c = _c()
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return r["value"] if r and r["value"] is not None else default


def put(key, value):
    c = _c()
    c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, str(value)))
    c.commit()
    c.close()


def defaults_for_new_warrant(warrant_type):
    return {"office": get("office", ""), "city": get("city", ""), "state": get("state", ""),
            "state_short": get("state_short", ""), "addressee": get("addressee", ""),
            "ref_prefix": get(f"ref_prefix:{warrant_type}", "")}


# --------------------------------------------------------------------------
# years
# --------------------------------------------------------------------------
def parse_year(v):
    """4-digit year between YEAR_MIN and YEAR_MAX as int, else None."""
    s = str(v if v is not None else "").strip()
    if re.fullmatch(r"\d{4}", s) and YEAR_MIN <= int(s) <= YEAR_MAX:
        return int(s)
    return None


def year_from_date(text):
    y = WD.year_of(text)
    return parse_year(str(y)) if y else None


def default_year():
    return parse_year(get("fiscal_year")) or datetime.now().year


def set_default_year(y):
    y = parse_year(y)
    if y is None:
        raise ValueError(f"Year must be 4 digits between {YEAR_MIN} and {YEAR_MAX}.")
    add_year(y)
    put("fiscal_year", y)


def add_year(y):
    y = parse_year(y)
    if y is None:
        return
    c = _c()
    c.execute("INSERT OR IGNORE INTO fiscal_years(year) VALUES(?)", (y,))
    c.commit()
    c.close()


def years():
    """Years for the drop-down: registered years, years that have warrants, the default year, this calendar year and next."""
    ys = {datetime.now().year, datetime.now().year + 1, default_year()}
    c = _c()
    ys |= {r[0] for r in c.execute("SELECT year FROM fiscal_years")}
    ys |= {r[0] for r in c.execute("SELECT DISTINCT fiscal_year FROM warrants WHERE fiscal_year IS NOT NULL")}
    c.close()
    return sorted(y for y in ys if y and YEAR_MIN <= y <= YEAR_MAX)


def date_for_year(year, today=None):
    """The starting date for a NEW warrant: today's month and day in the chosen year, as DD/MM/YYYY (29 Feb -> 28 Feb when needed).
    This is the only place the computer's clock is used for a warrant date, and only for a warrant that does not exist yet."""
    t = today or datetime.now()
    t = t.date() if hasattr(t, "date") else t
    return WD.with_year(WD.fmt(t), year)


def date_with_year(text, year):
    """Same month and day as `text` but in `year`; returns text unchanged if it isn't a valid date."""
    return WD.with_year(text, year)


# --------------------------------------------------------------------------
# warrant number: nothing fixed -- a new warrant simply starts from the last saved one
# --------------------------------------------------------------------------
def last_prefix():
    """The first two digits the person used on the most recent warrants (from the records), or '' when there is no history.
    Used only to PRE-FILL the two digits of a new warrant; the person can change them."""
    c = _c()
    try:
        nos = [r[0] or "" for r in c.execute("SELECT warrant_no FROM warrants ORDER BY id DESC LIMIT 200")]
    finally:
        c.close()
    for n in nos:
        if WD.prefix_of(n):
            return WD.prefix_of(n)
    for n in nos:
        m = re.match(r"\d{2}", n.strip())
        if m:
            return m.group(0)
    return ""


def number_taken(no, exclude_id=None):
    """True when a DIFFERENT saved warrant already has this Warrant No."""
    c = _c()
    try:
        return c.execute("SELECT 1 FROM warrants WHERE lower(trim(warrant_no))=lower(?) AND id IS NOT ?", (str(no or "").strip(), exclude_id)).fetchone() is not None
    finally:
        c.close()


def suggest_prefix(d, preferred="", exclude_id=None):
    """The first two digits for a warrant dated `d`: `preferred` if that number is free, else the next free pair (wrapping 99 -> 00).
    '' when `preferred` is not a usable pair. Pure arithmetic on the warrant's own date: the clock is never read."""
    if not WD.valid_prefix(preferred):
        return ""
    tail = WD.build_no("00", d)[2:]
    c = _c()
    try:
        used = {(r[0] or "").strip().lower() for r in c.execute("SELECT warrant_no FROM warrants WHERE substr(warrant_no,3)=? AND id IS NOT ?", (tail, exclude_id))}
    finally:
        c.close()
    n = int(preferred)
    for i in range(100):
        xx = f"{(n + i) % 100:02d}"
        if WD.build_no(xx, d).lower() not in used:
            return xx
    return preferred


def share_gaps(mda_info):
    """What THIS computer's MDA list lacks for a shared warrant:
    {'mdas': [{code, name, codes:[{code, description}]}], 'codes': [{org, code, description}]}  (both empty = nothing to add)."""
    out = {"mdas": [], "codes": []}
    c = _c()
    try:
        for m in mda_info or []:
            org = str(m.get("code", "")).strip()
            if not org:
                continue
            if not _org_exists(c, org):
                out["mdas"].append({"code": org, "name": m.get("name", ""), "codes": list(m.get("codes") or [])})
                continue
            known = known_codes(cd["code"] for cd in m.get("codes") or [])
            out["codes"] += [{"org": org, **cd} for cd in m.get("codes") or [] if cd["code"] not in known]
    finally:
        c.close()
    return out


def adopt_share_gaps(gaps):
    """Adds the missing MDAs / economic codes of a shared warrant. This changes the MDA tables, so it needs the MDA password
    (PermissionError otherwise). Returns (mdas_added, codes_added)."""
    require()
    mdas = codes = 0
    for m in gaps.get("mdas", []):
        cl = m.get("codes") or []
        if not cl:
            raise ValueError(f"The MDA {m.get('name', '')} ({m.get('code', '')}) has no economic code in the file, so it cannot be added.")
        add_mda(m["code"], m["name"] or m["code"], cl[0]["code"], cl[0]["description"] or cl[0]["code"])
        mdas += 1; codes += 1
        for cd in cl[1:]:
            add_code(m["code"], cd["code"], cd["description"] or cd["code"]); codes += 1
    for cd in gaps.get("codes", []):
        add_code(cd["org"], cd["code"], cd["description"] or cd["code"]); codes += 1
    return mdas, codes


def last_warrant_no():
    """Warrant No. of the most recently saved warrant ('' when none has been saved yet)."""
    c = _c()
    r = c.execute("SELECT warrant_no FROM warrants ORDER BY id DESC LIMIT 1").fetchone()
    c.close()
    return (r["warrant_no"] or "") if r else ""


# --------------------------------------------------------------------------
# Beneficiary / payee list (table `payees`), served from memory so typing never touches the disk
# --------------------------------------------------------------------------
_payee_cache = None            # list of (name, lower-case name, last_used_at)
_payee_cache_at = 0.0
_PAYEE_CACHE_TTL = 1.0          # notice payees saved by another WMS process without reading SQLite on each keystroke


def clean_payee(text):
    """Trim and collapse repeated spaces; '' for blank lines and '#' comments."""
    t = " ".join(str(text or "").replace("\u00a0", " ").split())
    return "" if t.startswith("#") else t


def _payees():
    global _payee_cache, _payee_cache_at
    now = time.monotonic()
    if _payee_cache is None or now - _payee_cache_at >= _PAYEE_CACHE_TTL:
        c = _c()
        try:
            _payee_cache = [(r[0], r[0].lower(), r[1] or "") for r in c.execute("SELECT payee,last_used_at FROM payees") if r[0]]
        finally:
            c.close()
        _payee_cache_at = time.monotonic()
    return _payee_cache


def invalidate_payees():
    global _payee_cache, _payee_cache_at
    _payee_cache = None
    _payee_cache_at = 0.0


# --- UPPERCASE option: write every Beneficiary / Payee in capitals (typing, suggestions, schedule, printed warrant) ---------------
_PAYEE_UPPER = None


def reset_option_cache():
    """Forget the remembered 'UPPERCASE payees' choice so it is read again from the database (after a restore or an import)."""
    global _PAYEE_UPPER
    _PAYEE_UPPER = None


def payee_upper_on():
    """True when the 'UPPERCASE payees' option is switched on (read once, then remembered in memory)."""
    global _PAYEE_UPPER
    if _PAYEE_UPPER is None:
        _PAYEE_UPPER = get("payee_uppercase", "0") == "1"
    return _PAYEE_UPPER


def set_payee_upper(flag):
    """Switch the option on/off. Stored names are never rewritten, so switching it off brings back every name as it was typed."""
    global _PAYEE_UPPER
    put("payee_uppercase", "1" if flag else "0")
    _PAYEE_UPPER = bool(flag)


def payee_case(text):
    """The name as it must be shown / printed: capitals when the option is on, otherwise unchanged."""
    return str(text or "").upper() if payee_upper_on() else str(text or "")


def payee_suggest(query="", limit=30):
    """Fast autocomplete: names starting with the text first, then names with a word starting with it, then names
    containing it; inside each group the most recently used first. Empty text = most recently used names."""
    q = " ".join(str(query or "").lower().split())
    rows = _payees()
    if not q:
        ordered = sorted(rows, key=lambda r: r[1])
        ordered.sort(key=lambda r: r[2], reverse=True)     # recently used first, never-used names alphabetically after them
        return _shown([r[0] for r in ordered], limit)
    hits = []
    for name, low, used in rows:
        i = low.find(q)
        if i < 0:
            continue
        tier = 0 if i == 0 else (1 if low[i - 1] == " " else 2)
        hits.append((tier, name, low, used))
    hits.sort(key=lambda h: h[3], reverse=True)          # newest use first ...
    hits.sort(key=lambda h: h[0])                        # ... then the best kind of match (stable sort keeps the order above)
    return _shown([h[1] for h in hits], limit)


def _shown(names, limit):
    """First `limit` names; in UPPERCASE mode they are capitalised and a name that differs only by capitals appears once."""
    if not payee_upper_on():
        return names[:limit]
    out, seen = [], set()
    for n in names:
        u = n.upper()
        if u not in seen:
            seen.add(u); out.append(u)
            if len(out) >= limit:
                break
    return out


def canonical_payee(text):
    """The remembered spelling when the same name (ignoring capitals / extra spaces) is already known, else the cleaned text."""
    t = clean_payee(text)
    low = t.lower()
    for name, l, _ in _payees():
        if l == low:
            return payee_case(name)
    return payee_case(t)


def _remember_in(c, names, now):
    for n in {clean_payee(x) for x in names}:
        if not n:
            continue
        r = c.execute("SELECT id FROM payees WHERE payee=? COLLATE NOCASE", (n,)).fetchone()
        if r:
            c.execute("UPDATE payees SET last_used_at=? WHERE id=?", (now, r[0]))
        else:
            c.execute("INSERT INTO payees(payee,last_used_at) VALUES(?,?)", (n, now))


def remember_payee(value):
    n = clean_payee(value)
    if not n:
        return
    c = _c()
    try:
        _remember_in(c, [n], datetime.now().isoformat(timespec="seconds"))
        c.commit()
    finally:
        c.close()
    invalidate_payees()


def forget_payee(value):
    n = clean_payee(value)
    if not n:
        return
    c = _c()
    try:
        c.execute("DELETE FROM payees WHERE payee=? COLLATE NOCASE", (n,))
        c.commit()
    finally:
        c.close()
    invalidate_payees()


def import_payees(names):
    """Adds names that are not there yet (ignoring capitals). Returns (added, already_there).
    Existing names keep their 'last used' order; nothing is ever removed or changed."""
    c = _c()
    added = dup = 0
    try:
        known = {r[0].lower() for r in c.execute("SELECT payee FROM payees") if r[0]}
        for n in names:
            n = clean_payee(n)
            if not n:
                continue
            if n.lower() in known:
                dup += 1
            else:
                c.execute("INSERT INTO payees(payee,last_used_at) VALUES(?,NULL)", (n,))
                known.add(n.lower()); added += 1
        c.commit()
    finally:
        c.close()
    invalidate_payees()
    return added, dup


def import_payees_file(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return import_payees(fh.read().splitlines())


def seed_payees(path):
    """Loads the bundled payee list into the database once per version of the file
    (so names you later 'Forget' do not keep coming back at every start). Returns (added, dup) or None."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    tag = hashlib.sha256(raw).hexdigest()[:16]
    if get("payees_seed_hash") == tag:
        return None
    res = import_payees(raw.decode("utf-8-sig", errors="replace").splitlines())
    put("payees_seed_hash", tag)
    return res


# --------------------------------------------------------------------------
# MDA password + session
# --------------------------------------------------------------------------
_unlocked_until = 0.0


def _hash(pw, salt, iters):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iters).hex()


def has_password():
    return bool(get("mda_pw"))


def set_password(new, current=None):
    """Create the password (first time) or change it (needs the current one)."""
    if len(new or "") < 6:
        raise ValueError("The password must be at least 6 characters.")
    if has_password() and not _check(current or ""):
        raise PermissionError("The current password is wrong.")
    salt = os.urandom(16)
    put("mda_pw", f"pbkdf2${PBKDF2_ITERS}${salt.hex()}${_hash(new, salt, PBKDF2_ITERS)}")
    put("mda_fails", 0)
    put("mda_locked_until", 0)


def _check(pw):
    stored = get("mda_pw")
    if not stored:
        return False
    try:
        _, iters, salt, h = stored.split("$")
        return hmac.compare_digest(_hash(pw, bytes.fromhex(salt), int(iters)), h)
    except Exception:
        return False


def unlock(pw):
    """True and starts a session when the password is right; False when wrong; raises LockedOut while locked out."""
    global _unlocked_until
    wait = float(get("mda_locked_until", 0) or 0) - time.time()
    if wait > 0:
        raise LockedOut(int(wait) + 1)
    if _check(pw):
        put("mda_fails", 0)
        _unlocked_until = time.time() + SESSION_SECONDS
        return True
    fails = int(get("mda_fails", 0) or 0) + 1
    if fails >= MAX_FAILS:
        put("mda_fails", 0)
        put("mda_locked_until", time.time() + LOCK_SECONDS)
    else:
        put("mda_fails", fails)
    return False


def tries_left():
    return MAX_FAILS - int(get("mda_fails", 0) or 0)


def is_unlocked():
    return has_password() and time.time() < _unlocked_until


def lock():
    global _unlocked_until
    _unlocked_until = 0.0


def require():
    """Every MDA change calls this first: no password session, no change."""
    global _unlocked_until
    if not is_unlocked():
        raise PermissionError("MDA editing is locked. Enter the MDA password to continue.")
    _unlocked_until = time.time() + SESSION_SECONDS


# --------------------------------------------------------------------------
# MDA / economic code administration (all protected)
# --------------------------------------------------------------------------
def _audit(c, action, detail):
    c.execute("INSERT INTO mda_audit(at,action,detail) VALUES(?,?,?)",
              (datetime.now().isoformat(timespec="seconds"), action, detail))


def _valid_org(code):
    if not re.fullmatch(r"[0-9A-Za-z\-]{1,30}", code or ""):
        raise ValueError("The MDA code may contain only letters, digits and '-' (no '/' or spaces).")


def _need(text, what):
    if not (text or "").strip():
        raise ValueError(f"{what} is required.")
    return " ".join(text.split())


def _org_exists(c, code):
    return c.execute("SELECT 1 FROM budget WHERE organisation_code=? LIMIT 1", (code,)).fetchone() is not None


def _check_code_of(org, code):
    if not code.startswith(org + "/") or len(code) <= len(org) + 1:
        raise ValueError(f"The economic code must start with '{org}/' followed by the code, e.g. {org}/21010101.")


def list_mdas(q=""):
    c = _c()
    q = f"%{q.strip()}%"
    rows = c.execute("""SELECT organisation_code code, organisation_name name, COUNT(*) n FROM budget
                        WHERE organisation_code IS NOT NULL AND (organisation_code LIKE ? OR organisation_name LIKE ?)
                        GROUP BY organisation_code, organisation_name ORDER BY organisation_name""", (q, q)).fetchall()
    c.close()
    return rows


def list_codes(org, q=""):
    c = _c()
    q = f"%{q.strip()}%"
    rows = c.execute("""SELECT organisation_economic_code code, description FROM economic_codes
                        WHERE substr(organisation_economic_code,1,?)=? AND
                        (organisation_economic_code LIKE ? OR description LIKE ?)
                        ORDER BY organisation_economic_code""", (len(org) + 1, org + "/", q, q)).fetchall()
    c.close()
    return rows


def mda_usage(org):
    """How many saved warrants include this MDA."""
    c = _c()
    n = 0
    for r in c.execute("SELECT data_json FROM warrants"):
        try:
            if any(m.get("code") == org for m in json.loads(r["data_json"]).get("mdas", [])):
                n += 1
        except Exception:
            pass
    c.close()
    return n


def add_mda(code, name, first_code, first_desc):
    require()
    code, name = _need(code, "MDA code"), _need(name, "MDA name")
    _valid_org(code)
    first_code = _need(first_code, "First economic code")
    first_desc = _need(first_desc, "Description of the first economic code")
    _check_code_of(code, first_code)
    c = _c()
    try:
        if _org_exists(c, code):
            raise ValueError(f"MDA code {code} already exists.")
        if c.execute("SELECT 1 FROM economic_codes WHERE organisation_economic_code=?", (first_code,)).fetchone():
            raise ValueError(f"Economic code {first_code} already exists.")
        c.execute("INSERT INTO budget VALUES(?,?,?,?)", (code, name, first_code, first_desc))
        c.execute("INSERT INTO economic_codes VALUES(?,?)", (first_code, first_desc))
        _audit(c, "ADD MDA", f"{code} | {name} | first code {first_code}")
        c.commit()
    finally:
        c.close()


def rename_mda(code, new_name):
    require()
    new_name = _need(new_name, "MDA name")
    c = _c()
    try:
        old = c.execute("SELECT organisation_name FROM budget WHERE organisation_code=? LIMIT 1", (code,)).fetchone()
        if not old:
            raise ValueError("That MDA no longer exists.")
        c.execute("UPDATE budget SET organisation_name=? WHERE organisation_code=?", (new_name, code))
        _audit(c, "RENAME MDA", f"{code} | '{old[0]}' -> '{new_name}'")
        c.commit()
    finally:
        c.close()


def change_mda_code(old, new):
    """Changes the MDA code AND the prefix of all its economic codes (they stay tied to the MDA)."""
    require()
    new = _need(new, "New MDA code")
    _valid_org(new)
    c = _c()
    try:
        if not _org_exists(c, old):
            raise ValueError("That MDA no longer exists.")
        if new == old:
            raise ValueError("The new code is the same as the old one.")
        if _org_exists(c, new):
            raise ValueError(f"MDA code {new} already exists.")
        ol = len(old) + 1
        c.execute("""UPDATE budget SET organisation_code=?, organisation_economic_code=?||substr(organisation_economic_code,?)
                     WHERE organisation_code=?""", (new, new + "/", ol + 1, old))
        c.execute("""UPDATE economic_codes SET organisation_economic_code=?||substr(organisation_economic_code,?)
                     WHERE substr(organisation_economic_code,1,?)=?""", (new + "/", ol + 1, ol, old + "/"))
        c.execute("""UPDATE line_templates SET org_code=?, economic_code=?||substr(economic_code,?)
                     WHERE org_code=?""", (new, new + "/", ol + 1, old))      # saved line items stay attached to the MDA
        _audit(c, "CHANGE MDA CODE", f"{old} -> {new}")
        c.commit()
    finally:
        c.close()


def delete_mda(code):
    require()
    c = _c()
    try:
        r = c.execute("SELECT organisation_name FROM budget WHERE organisation_code=? LIMIT 1", (code,)).fetchone()
        if not r:
            raise ValueError("That MDA no longer exists.")
        n = c.execute("DELETE FROM budget WHERE organisation_code=?", (code,)).rowcount
        c.execute("DELETE FROM economic_codes WHERE substr(organisation_economic_code,1,?)=?", (len(code) + 1, code + "/"))
        t = c.execute("DELETE FROM line_templates WHERE org_code=?", (code,)).rowcount          # its saved line items go with it
        _audit(c, "DELETE MDA", f"{code} | {r[0]} | {n} economic code(s) and {t} saved item(s) removed")
        c.commit()
        return n
    finally:
        c.close()


def add_code(org, code, description):
    require()
    code, description = _need(code, "Economic code"), _need(description, "Description")
    _check_code_of(org, code)
    c = _c()
    try:
        row = c.execute("SELECT organisation_name FROM budget WHERE organisation_code=? LIMIT 1", (org,)).fetchone()
        if not row:
            raise ValueError("That MDA no longer exists.")
        if c.execute("SELECT 1 FROM economic_codes WHERE organisation_economic_code=?", (code,)).fetchone():
            raise ValueError(f"Economic code {code} already exists.")
        c.execute("INSERT INTO budget VALUES(?,?,?,?)", (org, row[0], code, description))
        c.execute("INSERT INTO economic_codes VALUES(?,?)", (code, description))
        _audit(c, "ADD CODE", f"{code} | {description}")
        c.commit()
    finally:
        c.close()


def update_code(code, description):
    require()
    description = _need(description, "Description")
    c = _c()
    try:
        old = c.execute("SELECT description FROM economic_codes WHERE organisation_economic_code=?", (code,)).fetchone()
        if not old:
            raise ValueError("That economic code no longer exists.")
        c.execute("UPDATE economic_codes SET description=? WHERE organisation_economic_code=?", (description, code))
        c.execute("UPDATE budget SET description=? WHERE organisation_economic_code=?", (description, code))
        _audit(c, "EDIT CODE", f"{code} | '{old[0]}' -> '{description}'")
        c.commit()
    finally:
        c.close()


def delete_code(code):
    require()
    c = _c()
    try:
        old = c.execute("SELECT description FROM economic_codes WHERE organisation_economic_code=?", (code,)).fetchone()
        if not old:
            raise ValueError("That economic code no longer exists.")
        c.execute("DELETE FROM economic_codes WHERE organisation_economic_code=?", (code,))
        c.execute("DELETE FROM budget WHERE organisation_economic_code=?", (code,))
        t = c.execute("DELETE FROM line_templates WHERE economic_code=?", (code,)).rowcount     # a saved item for a code that is gone is useless
        _audit(c, "DELETE CODE", f"{code} | {old[0]}" + (f" | {t} saved item(s) removed" if t else ""))
        c.commit()
    finally:
        c.close()


# --------------------------------------------------------------------------
# Warrant types and the economic-code rule (18 = Recurrent, 30 = Capital -- stored in `code_type_rules`)
# --------------------------------------------------------------------------
def warrant_types():
    # These are the only warrant types with matching legal wording and layout in the PDF engine.
    configured = [t.strip() for t in (get("warrant_types", "Recurrent,Capital") or "").split(",") if t.strip()]
    supported = [t for t in ("Recurrent", "Capital") if t in configured]
    return supported or ["Recurrent", "Capital"]


def code_digits(code):
    """Number of digits in the full economic code, separators ignored: '9900000001/21010101' -> 18."""
    return sum(ch.isdigit() for ch in str(code or ""))


def code_rules():
    """[(digit_count, warrant_type)] from the database."""
    c = _c()
    try:
        rows = [(int(r[0]), r[1]) for r in c.execute("SELECT digits,warrant_type FROM code_length_rules")]
    finally:
        c.close()
    return sorted(rows)


def code_rules_text():
    return ", ".join(f"{n}={t}" for n, t in code_rules())


def parse_code_rules(text):
    """'18=Recurrent, 30=Capital' -> [(18,'Recurrent'), (30,'Capital')]; raises ValueError with a clear message on anything unclear."""
    out, seen = [], {}
    types = {t.lower(): t for t in warrant_types()}
    for part in re.split(r"[,;\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"'{part}' is not in the form DIGITS=Type, e.g. 18=Recurrent.")
        n, wt = (x.strip() for x in part.split("=", 1))
        if not re.fullmatch(r"\d{1,2}", n) or not (1 <= int(n) <= 60):
            raise ValueError(f"'{n}' must be a whole number of digits (1 to 60).")
        if wt.lower() not in types:
            raise ValueError(f"'{wt}' is not a warrant type. Use one of: {', '.join(warrant_types())}.")
        n = int(n)
        if n in seen and seen[n] != types[wt.lower()]:
            raise ValueError(f"A code of {n} digits is given two different types.")
        seen[n] = types[wt.lower()]
        out.append((n, seen[n]))
    return sorted(set(out))


def set_code_rules(text):
    """Replaces the rule list. Needs the unlocked MDA session (same protection as editing MDAs and codes)."""
    require()
    rules = parse_code_rules(text)
    c = _c()
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM code_length_rules")
        c.executemany("INSERT INTO code_length_rules(digits,warrant_type) VALUES(?,?)", rules)
        _audit(c, "CODE RULES", ", ".join(f"{n}={t}" for n, t in rules) or "(none)")
        c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()


def set_strict_unlisted(on):
    require()
    put("code_rules_strict", "1" if on else "0")


def strict_unlisted():
    return get("code_rules_strict", "0") == "1"


def check_code_type(code, warrant_type, rules=None, strict=None):
    """None when the code may be used on this warrant type, else a clear message. The test is the number of digits in the
    full economic code: 18 = Recurrent, 30 = Capital (editable rules). A code whose digit count matches no rule is not checked."""
    rules = code_rules() if rules is None else rules
    strict = strict_unlisted() if strict is None else strict
    n = code_digits(code)
    hit = next((t for d, t in rules if d == n), None)
    if hit is None:
        if strict and rules:
            return (f"Economic code {code} has {n} digits, which matches no Recurrent/Capital rule, so it cannot be used on a {warrant_type} warrant. "
                    "Valid lengths: " + ", ".join(f"{d} digits ({t})" for d, t in rules) + ".")
        return None
    if hit != warrant_type:
        okn = ", ".join(str(d) for d, t in rules if t == warrant_type) or "(none set)"
        return (f"Economic code {code} has {n} digits, so it is a {hit} code and cannot be used on a {warrant_type} warrant. "
                f"{warrant_type} warrants accept economic codes of {okn} digits.")
    return None


# --------------------------------------------------------------------------
# Validation (one implementation, used by Save, PDF export, Print and the data layer itself)
# --------------------------------------------------------------------------
def known_codes(codes):
    """Subset of `codes` that exist in the budget table (one query per 500 codes, not one per line)."""
    codes = sorted({str(x).strip() for x in codes if str(x).strip()})
    found = set()
    c = _c()
    try:
        for i in range(0, len(codes), 500):
            chunk = codes[i:i + 500]
            q = ",".join("?" * len(chunk))
            found |= {r[0] for r in c.execute(f"SELECT organisation_economic_code FROM budget WHERE organisation_economic_code IN ({q})", chunk)}
    finally:
        c.close()
    return found


def describe_code(code):
    c = _c()
    try:
        r = c.execute("SELECT description FROM budget WHERE organisation_economic_code=? LIMIT 1", (str(code).strip(),)).fetchone()
    finally:
        c.close()
    return r[0] if r and r[0] else ""


def check_warrant_no(no):
    """[] when the Warrant No. is correct, else clear reasons why it is 'not correct'.
    Default format (Settings): 8 digits written 6 digits / 2 digits, e.g. 990930/26. Both the pattern and the example are database settings."""
    t = str(no or "").strip()
    if not t:
        return ["Warrant No. is not correct: it is blank."]
    pat = (get("warrant_no_pattern", "") or "").strip()
    ex = (get("warrant_no_example", "") or "").strip()
    if pat:
        try:
            if re.fullmatch(pat, t):
                return []
        except re.error:
            return ["The Warrant No. format in Settings is not a valid pattern; fix or clear it in Settings."]
        why = []
        have = sum(ch.isdigit() for ch in t)
        if ex:
            need = sum(ch.isdigit() for ch in ex)
            if have < need:
                why.append(f"it has only {have} digit{'s' if have != 1 else ''} (needs {need})")
            elif have > need:
                why.append(f"it has {have} digits (needs {need})")
            allowed = set(ch for ch in ex if not ch.isdigit())
            bad = sorted({ch for ch in t if not ch.isdigit() and ch not in allowed})
            if bad:
                why.append("it contains characters that are not allowed (" + " ".join(repr(c) for c in bad) + ")")
            if not why:
                why.append("the digits are not in the right places")
        else:
            why.append("it does not follow the required format")
        return [f"Warrant No. '{t}' is not correct: " + "; ".join(why) + (f". Required format: {ex}." if ex else ".")]
    e = []                                   # no pattern set: basic "unfinished number" checks only
    if re.search(r"\s", t): e.append("it contains a space")
    if re.match(r"^[/\\\-.,]", t) or re.search(r"[/\\\-.,]$", t): e.append("it starts or ends with a separator")
    if re.search(r"[/\\\-.]{2,}", t): e.append("it has an empty part (two separators in a row)")
    if re.search(r"[?*_<>]|(?i:x{2,})", t): e.append("it contains placeholder characters")
    if not re.search(r"\d", t): e.append("it has no digits")
    return [f"Warrant No. '{t}' is not correct: " + "; ".join(e) + "."] if e else []


def check_wno_settings(pattern, example):
    """None when the Warrant No. format and its example are usable, else a message (so a typo in Settings cannot break every save)."""
    pattern, example = (pattern or "").strip(), (example or "").strip()
    if not pattern:
        return None
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"The Warrant No. format is not a valid pattern ({exc}). Example of a correct one: \\d{{6}}/\\d{{2}}"
    if example and not rx.fullmatch(example):
        return f"The Warrant No. example '{example}' does not match the Warrant No. format, so every warrant would be refused. Fix one of them."
    return None


def validate_payload(d, historical=False):
    """List of clear error messages ([] = the warrant may be saved / validated / printed).

    historical=True is for a warrant that is ALREADY SAVED and unchanged (reprint, PDF export, share, weekly export). It is checked for
    what makes the printed page wrong (year, date, a number, MDAs, payees, amounts) but NOT against today's MDA / economic-code tables,
    Warrant No. format or code-length rules: those may have been changed since the warrant was saved, and that must never stop an
    old warrant from being printed again. Saving (new or edited) always uses the full check."""
    e = []
    if parse_year(d.get("year")) is None:
        e.append("Financial year must be 4 digits.")
    if WD.parse_input(d.get("date", "")) is None:
        e.append(f"Date must be DD/MM/YYYY (for example {WD.EXAMPLE}).")
    if historical:
        if not str(d.get("warrant_no", "")).strip():
            e.append("Warrant No. is not correct: it is blank.")
    else:
        e += check_warrant_no(d.get("warrant_no", ""))
    wt = d.get("warrant_type")
    supported_types = {"Recurrent", "Capital"}
    if wt not in supported_types or (not historical and wt not in warrant_types()):
        e.append(f"Warrant type must be one of: {', '.join(warrant_types())}.")
    mdas = d.get("mdas") or []
    if not mdas:
        e.append("Add at least one MDA / Organisation.")
    known = set() if historical else known_codes(r.get("code", "") for m in mdas for r in m.get("items", []))
    rules, strict = ([], False) if historical else (code_rules(), strict_unlisted())
    for mi, m in enumerate(mdas, 1):
        name = m.get("name", "")
        if not m.get("items"):
            e.append(f"MDA {mi} ({name}) has no schedule lines.")
        for li, r in enumerate(m.get("items", []), 1):
            code = str(r.get("code", "")).strip()
            where = f"{name}, line {li}"
            if historical:
                if not code:
                    e.append(f"{where}: missing economic code.")
            else:
                if not code.startswith(str(m.get("code", "")).strip() + "/"):
                    e.append(f"{where}: economic code belongs to a different MDA.")
                if code not in known:
                    e.append(f"{where}: code not found: {code}")
                if wt in warrant_types():
                    msg = check_code_type(code, wt, rules, strict)
                    if msg:
                        e.append(f"{where}: {msg}")
            if not str(r.get("payee", "")).strip():
                e.append(f"{where}: missing payee.")
            try:
                amt = float(r.get("amount", 0))
                if not math.isfinite(amt):
                    e.append(f"{where}: amount is not a number.")
                elif amt <= 0:
                    e.append(f"{where}: amount must be > 0.")
                elif amt > MAX_AMOUNT:
                    e.append(f"{where}: amount is too large.")
            except (TypeError, ValueError):
                e.append(f"{where}: amount is not a number.")
    return e


# --------------------------------------------------------------------------
# Saved warrants: transactional save / update, reload, search
# --------------------------------------------------------------------------
class WarrantError(Exception):
    """Base class: the message is written for the person at the keyboard."""


class ValidationFailed(WarrantError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


class DuplicateWarrantNo(WarrantError):
    pass


class ConflictError(WarrantError):
    pass


class NotFound(WarrantError):
    pass


class DuplicateWarrantUid(WarrantError):
    pass


def _row_dict(r):
    d = dict(r)
    d["data"] = json.loads(d.get("data_json") or "{}")
    return d


def load_warrant(warrant_id):
    """Fresh read of one saved warrant straight from the database (row columns + parsed `data`), or None."""
    c = _c()
    try:
        r = c.execute("SELECT * FROM warrants WHERE id=?", (int(warrant_id),)).fetchone()
        rev = (c.execute("SELECT COUNT(*) FROM warrant_revisions WHERE warrant_id=?", (int(warrant_id),)).fetchone()[0] + 1) if r else None
    finally:
        c.close()
    if not r:
        return None
    d = _row_dict(r)
    d["revision"] = rev                         # 1 = saved once; +1 for every later save
    return d


def find_by_uid(uid):
    """The saved warrant with this identity, or None (this is how a shared file finds the copy you already have)."""
    uid = str(uid or "").strip()
    if not uid:
        return None
    c = _c()
    try:
        r = c.execute("SELECT id FROM warrants WHERE uid=?", (uid,)).fetchone()
    finally:
        c.close()
    return load_warrant(r["id"]) if r else None


def warrant_by_number(no):
    """The saved warrant that currently has this Warrant No. (ignoring capitals), or None."""
    t = str(no or "").strip().lower()
    if not t:
        return None
    c = _c()
    try:
        r = c.execute("SELECT id FROM warrants WHERE lower(trim(warrant_no))=?", (t,)).fetchone()
    finally:
        c.close()
    return load_warrant(r["id"]) if r else None


def save_warrant(payload, warrant_id=None, expected_updated_at=None, uid=None):
    """Creates (warrant_id None) or UPDATES IN PLACE (same row, never a duplicate) one warrant, in ONE transaction:
    validate -> duplicate-number check -> keep the replaced version -> write -> read back and compare -> commit.
    Anything wrong rolls everything back and raises; only after commit is the row re-read from the database and returned
    as {'verb','row'} so the screen can show exactly what is stored.

    expected_updated_at: the `updated_at` the screen loaded; if the record has changed since (another window / computer),
    the save is refused instead of silently overwriting the newer version."""
    errs = validate_payload(payload)
    if errs:
        raise ValidationFailed(errs)
    d = json.loads(json.dumps(payload, default=str))
    no = str(d["warrant_no"]).strip()
    d["warrant_no"] = no
    d["date"] = WD.normalise(d.get("date", "")) or d.get("date", "")     # always stored as DD/MM/YYYY, whatever form it was typed in
    year = parse_year(d.get("year"))
    total = round(sum(float(x["amount"]) for m in d["mdas"] for x in m["items"]), 2)
    now = datetime.now().isoformat(timespec="microseconds")
    blob = json.dumps(d)
    c = _c()
    try:
        c.execute("BEGIN IMMEDIATE")                                   # take the write lock now: no interleaved writers
        if warrant_id is None and str(uid or "").strip() and c.execute("SELECT 1 FROM warrants WHERE uid=?", (str(uid).strip(),)).fetchone():
            raise DuplicateWarrantUid("This warrant is already in your records (it was opened from a shared file before). "
                                      "Open it from Saved Warrants instead of saving it again. Nothing was saved.")
        dup = c.execute("SELECT id FROM warrants WHERE lower(trim(warrant_no))=lower(?) AND id IS NOT ?", (no, warrant_id)).fetchone()
        if dup:
            raise DuplicateWarrantNo(f"Warrant No. {no} already exists as a different saved warrant. Open that warrant from Saved Warrants "
                                     "to edit it, or give this one another Warrant No. Nothing was saved.")
        if warrant_id is not None:
            old = c.execute("SELECT warrant_no,data_json,updated_at FROM warrants WHERE id=?", (warrant_id,)).fetchone()
            if old is None:
                raise NotFound("This warrant no longer exists in the records, so it could not be updated. Nothing was saved.")
            if expected_updated_at is not None and (old["updated_at"] or "") != (expected_updated_at or ""):
                raise ConflictError("This warrant was changed by someone else (or another window) after you opened it. "
                                    "Nothing was saved. Re-open it from Saved Warrants and make your changes again.")
            if old["data_json"]:
                c.execute("INSERT INTO warrant_revisions(warrant_id,warrant_no,data_json,replaced_at) VALUES(?,?,?,?)",
                          (warrant_id, old["warrant_no"], old["data_json"], now))
            n = c.execute("""UPDATE warrants SET warrant_no=?,warrant_date=?,office=?,city=?,state=?,grand_total=?,status=?,data_json=?,
                             updated_at=?,warrant_type=?,fiscal_year=?,original_warrant_no=COALESCE(original_warrant_no,?),
                             uid=COALESCE(NULLIF(uid,''),?) WHERE id=?""",
                          (no, d["date"], d.get("office", ""), d.get("city", ""), d.get("state", ""), total, "Saved", blob, now,
                           d["warrant_type"], year, old["warrant_no"], new_uid(), warrant_id)).rowcount
            if n != 1:
                raise WarrantError("The database did not update exactly one record. Nothing was saved.")
            new_id, verb = warrant_id, "updated"
        else:
            wuid = str(uid or "").strip() or new_uid()
            cur = c.execute("""INSERT INTO warrants(warrant_no,warrant_date,office,city,state,grand_total,status,data_json,created_at,updated_at,
                               warrant_type,fiscal_year,original_warrant_no,uid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (no, d["date"], d.get("office", ""), d.get("city", ""), d.get("state", ""), total, "Saved", blob, now, now,
                             d["warrant_type"], year, no, wuid))
            new_id, verb = cur.lastrowid, "saved"
        _remember_in(c, [x["payee"] for m in d["mdas"] for x in m["items"]], now)
        back = c.execute("SELECT warrant_no,data_json,grand_total FROM warrants WHERE id=?", (new_id,)).fetchone()
        if back is None or back["warrant_no"] != no or back["data_json"] != blob:
            raise WarrantError("The saved copy did not match what was sent to the database. Nothing was saved.")
        c.commit()
    except sqlite3.IntegrityError as exc:
        c.rollback()
        log.warning("SAVE REFUSED (duplicate Warrant No.) no=%s", no)
        raise DuplicateWarrantNo(f"Warrant No. {no} already exists as a different saved warrant. Nothing was saved.") from exc
    except Exception as exc:
        c.rollback()
        if isinstance(exc, WarrantError):
            log.warning("SAVE REFUSED no=%s id=%s: %s", no, warrant_id, str(exc).splitlines()[0])
        else:
            log.exception("SAVE FAILED, rolled back: no=%s id=%s", no, warrant_id)
        raise
    finally:
        c.close()
    log.info("WARRANT %s id=%s no=%s total=%.2f type=%s", verb.upper(), new_id, no, total, d.get("warrant_type"))
    invalidate_payees()
    add_year(year)
    row = load_warrant(new_id)                                         # what is really on disk now
    if row is None or row["data_json"] != blob:
        raise WarrantError("The warrant could not be read back after saving. Please check the Saved Warrants list before relying on it.")
    return {"verb": verb, "row": row}


def find_warrant_ids(text):
    """Ids of warrants whose CURRENT or ORIGINAL Warrant No., or any earlier number it was saved under, equals `text` (ignoring capitals)."""
    t = str(text or "").strip().lower()
    if not t:
        return []
    c = _c()
    try:
        cur = [r[0] for r in c.execute("""SELECT id FROM warrants WHERE lower(trim(warrant_no))=? OR lower(trim(original_warrant_no))=?
                                          ORDER BY (lower(trim(warrant_no))=?) DESC, id DESC""", (t, t, t))]
        old = [r[0] for r in c.execute("SELECT DISTINCT warrant_id FROM warrant_revisions WHERE lower(trim(warrant_no))=?", (t,))]
    finally:
        c.close()
    return cur + [i for i in old if i not in cur]


def search_warrants(no="", mda="", date="", year=None, limit=500):
    """Saved-warrant list. The Warrant No. filter matches the current number, the number it was first saved under,
    and any earlier number (so a renamed warrant is still found by its original number). Exact matches come first."""
    no = str(no or "").strip().lower(); mda = str(mda or "").strip().lower(); date = str(date or "").strip()
    parsed_date = WD.parse_input(date)
    if parsed_date:
        date = WD.fmt(parsed_date)  # a short-year query (DD/MM/YY) still finds the full-year record
    year = None if year in (None, "", "All") else str(year)
    sql = ["SELECT id,warrant_no,original_warrant_no,warrant_type,warrant_date,grand_total,updated_at,fiscal_year,data_json FROM warrants WHERE 1=1"]
    args = []
    if no:
        sql.append("""AND (instr(lower(warrant_no),?)>0 OR instr(lower(COALESCE(original_warrant_no,'')),?)>0
                      OR id IN (SELECT warrant_id FROM warrant_revisions WHERE instr(lower(warrant_no),?)>0))""")
        args += [no, no, no]
    if date:
        sql.append("AND instr(COALESCE(warrant_date,''),?)>0"); args.append(date)
    if year:
        sql.append("AND CAST(fiscal_year AS TEXT)=?"); args.append(year)
    sql.append("ORDER BY (lower(warrant_no)=? OR lower(COALESCE(original_warrant_no,''))=?) DESC, updated_at DESC, id DESC")
    args += [no, no]
    if not mda:
        sql.append("LIMIT ?"); args.append(int(limit))
    c = _c()
    try:
        rows = c.execute(" ".join(sql), args).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        try:
            names = "; ".join(m.get("name", "") for m in json.loads(r["data_json"]).get("mdas", []))
        except Exception:
            names = ""
        if mda and mda not in names.lower():
            continue
        out.append({"id": r["id"], "warrant_no": r["warrant_no"], "original_warrant_no": r["original_warrant_no"], "type": r["warrant_type"] or "Recurrent",
                    "year": r["fiscal_year"], "date": r["warrant_date"], "total": r["grand_total"] or 0, "saved": r["updated_at"] or "", "mdas": names})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# Backups, restore, and moving settings between computers / financial years
# --------------------------------------------------------------------------
SEED_SETTINGS["backup_keep"] = "20"               # automatic copies (start-up) to keep; upgrade / restore / import / manual copies are kept separately
_PROTECTED_REASONS = ("before_upgrade", "before_restore", "before_import", "manual")
_PROTECTED_KEEP = 15


def _db_file():
    c = _c()
    try:
        for r in c.execute("PRAGMA database_list"):
            if r[1] == "main" and r[2]:
                return r[2]
    finally:
        c.close()
    return None


def backup_dir():
    d = (get("backup_dir", "") or "").strip()
    if not d:
        f = _db_file()
        d = os.path.join(os.path.dirname(f) if f else ".", "backups")
    os.makedirs(d, exist_ok=True)
    return d


def _reason_of(name):
    m = re.match(r"^warrant_data_\d{8}_\d{6}_([a-z_]+)(?:-\d+)?\.db$", name)
    return m.group(1) if m else "auto"


def list_backups():
    """[{path,name,reason,size,mtime}] newest first (the older one-per-day copies are listed too)."""
    d = backup_dir()
    out = []
    for n in os.listdir(d):
        if n.startswith("warrant_data_") and n.endswith(".db"):
            p = os.path.join(d, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({"path": p, "name": n, "reason": _reason_of(n), "size": st.st_size, "mtime": st.st_mtime})
    return sorted(out, key=lambda b: b["mtime"], reverse=True)


def verify_backup(path):
    """None when the file is a healthy WMS database, else the reason it is not."""
    try:
        c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)      # as_uri() quotes '#', '%', '?' and spaces correctly
        try:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return "the file failed the database integrity check"
            tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            c.close()
    except Exception as exc:
        return f"the file cannot be opened as a database ({exc})"
    need = {"budget", "economic_codes"}
    if not need <= tabs:
        return "it is not a Warrant System database (MDA / economic-code tables are missing)"
    return None


def prune_backups():
    keep_auto = max(1, int(get("backup_keep", "20") or 20))
    autos = [b for b in list_backups() if b["reason"] not in _PROTECTED_REASONS]
    prot = [b for b in list_backups() if b["reason"] in _PROTECTED_REASONS]
    for b in autos[keep_auto:] + prot[_PROTECTED_KEEP:]:
        try:
            os.remove(b["path"])
        except OSError:
            pass


def backup_now(reason="manual"):
    """Consistent copy of the live database (SQLite online-backup API, safe while the program is running), checked after writing.
    Returns the file path; raises with a readable message if the copy could not be made or does not verify."""
    reason = re.sub(r"[^a-z_]", "", str(reason).lower()) or "manual"
    d = backup_dir()
    stamp = f"warrant_data_{datetime.now():%Y%m%d_%H%M%S}_{reason}"
    final, n = os.path.join(d, stamp + ".db"), 1
    while os.path.exists(final):                       # two copies in the same second must never overwrite each other
        n += 1
        final = os.path.join(d, f"{stamp}-{n}.db")
    tmp = final + ".part"
    src = _c()
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    except Exception as exc:
        try: os.remove(tmp)
        except OSError: pass
        raise RuntimeError(f"Backup failed: {exc}") from exc
    finally:
        src.close()
    bad = verify_backup(tmp)
    if bad:
        try: os.remove(tmp)
        except OSError: pass
        raise RuntimeError(f"Backup failed: the new copy was not healthy ({bad}).")
    os.replace(tmp, final)
    prune_backups()
    log.info("BACKUP made (%s): %s", reason, os.path.basename(final))
    return final


def restore_backup(path):
    """Replaces the live database with a backup (after checking it, and after taking a 'before_restore' copy of the current data).
    The backup's own schema is brought up to date afterwards. Returns the path of the safety copy."""
    bad = verify_backup(path)
    if bad:
        raise RuntimeError(f"This backup cannot be restored: {bad}. Nothing was changed.")
    safety = backup_now("before_restore")
    src = sqlite3.connect(path)
    dst = _c()
    try:
        src.backup(dst)
    except Exception as exc:
        raise RuntimeError(f"Restore failed: {exc}. Your data was not changed; a safety copy is at {safety}.") from exc
    finally:
        src.close(); dst.close()
    invalidate_payees()
    reset_option_cache()
    ensure_schema()
    log.warning("RESTORE done from %s (safety copy %s)", os.path.basename(path), os.path.basename(safety))
    return safety


def pre_upgrade_backup():
    """Called once at start-up: if the program version changed since the database was last opened, copy the database FIRST."""
    c = _c()
    try:
        has = c.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone() and c.execute("SELECT 1 FROM sqlite_master WHERE name='budget'").fetchone()
        seen = c.execute("SELECT value FROM settings WHERE key='app_version'").fetchone() if has else None
    finally:
        c.close()
    if has and (seen[0] if seen else None) != APP_VERSION:
        backup_now("before_upgrade")


# ---- settings export / import -------------------------------------------------
_EXPORT_SKIP_EXACT = {"payees_seed_hash", "code_rules_seeded", "code_len_rules_seeded", "wno_format_v1", "app_version", "backup_dir",
                      "date_format_v2", "date_format_v3", "date_format_v4", "budget_repair_v1"}      # this database's own migration markers never travel
EXPORT_PARTS = ("settings", "rules", "years", "payees", "mdas", "templates")
_EXPORT_SKIP_PREFIX = ("mda_", "weekly_export_")     # the MDA password / lock state, and this computer's weekly-export folder + progress


def _body_digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def export_settings(path):
    """Writes settings, Recurrent/Capital rules, financial years, payees and the MDA / economic-code tables to one file.
    Never includes the MDA password, lock state, warrants or backups. Returns a dict of counts."""
    c = _c()
    try:
        body = {
            "settings": {r[0]: r[1] for r in c.execute("SELECT key,value FROM settings")
                         if not r[0].startswith(_EXPORT_SKIP_PREFIX) and r[0] not in _EXPORT_SKIP_EXACT},
            "rules": [[int(r[0]), r[1]] for r in c.execute("SELECT digits,warrant_type FROM code_length_rules ORDER BY digits")],
            "years": [r[0] for r in c.execute("SELECT year FROM fiscal_years ORDER BY year")],
            "payees": [[r[0], r[1]] for r in c.execute("SELECT payee,last_used_at FROM payees ORDER BY payee")],
            "budget": [list(map(lambda v: "" if v is None else str(v), r)) for r in
                       c.execute("SELECT organisation_code,organisation_name,organisation_economic_code,description FROM budget")],
            "economic_codes": [["" if v is None else str(v) for v in r] for r in
                               c.execute("SELECT organisation_economic_code,description FROM economic_codes")],
            "templates": [[r[0], r[1], r[2], r[3], r[4]] for r in
                          c.execute("SELECT org_code,name,economic_code,description,use_count FROM line_templates ORDER BY org_code,name")],
        }
    finally:
        c.close()
    doc = {"format": "wms-config", "version": 1, "app_version": APP_VERSION,
           "exported_at": datetime.now().isoformat(timespec="seconds"), "checksum": _body_digest(body), "body": body}
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)
    os.replace(tmp, path)
    return {"settings": len(body["settings"]), "rules": len(body["rules"]), "years": len(body["years"]), "payees": len(body["payees"]),
            "mdas": len({r[0] for r in body["budget"]}), "codes": len(body["economic_codes"]), "templates": len(body["templates"])}


def read_export(path):
    """Loads and checks an export file; raises ValueError with a readable reason if it is not a good one."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception as exc:
        raise ValueError(f"This is not a readable settings file ({exc}).")
    if not isinstance(doc, dict) or doc.get("format") != "wms-config" or not isinstance(doc.get("body"), dict):
        raise ValueError("This file is not a Warrant System settings export.")
    if doc.get("version") != 1:
        raise ValueError(f"This settings file is version {doc.get('version')}, which this program cannot read.")
    if doc.get("checksum") != _body_digest(doc["body"]):
        raise ValueError("The settings file is damaged or was edited (its checksum does not match). Nothing was imported.")
    b = doc["body"]
    for k, t in (("settings", dict), ("rules", list), ("years", list), ("payees", list), ("budget", list), ("economic_codes", list)):
        if not isinstance(b.get(k), t):
            raise ValueError(f"The settings file is incomplete ('{k}' is missing or wrong).")
    return doc


def import_settings(path, parts=EXPORT_PARTS):
    """Merges an export into this database in ONE transaction, after taking a 'before_import' backup. Needs the unlocked MDA session.
    settings / rules: the file's values replace the current ones. years, payees, MDAs, economic codes: missing ones are added,
    nothing is deleted or changed. The MDA password and saved warrants are never touched. Returns counts."""
    require()
    doc = read_export(path)
    b = doc["body"]
    parts = set(parts)
    if "settings" in parts and "warrant_types" in b["settings"]:
        configured = [x.strip() for x in str(b["settings"]["warrant_types"] or "").split(",") if x.strip()]
        if not configured or len(set(configured)) != len(configured) or any(x not in ("Recurrent", "Capital") for x in configured):
            raise ValueError("The settings file contains unsupported warrant types. WMS can print only Recurrent and Capital warrants. Nothing was imported.")
    rules = []
    if "rules" in parts:
        types = {t.lower(): t for t in [x.strip() for x in str(b["settings"].get("warrant_types", get("warrant_types", "Recurrent,Capital"))).split(",") if x.strip()]}
        for n, wt in b["rules"]:
            if not isinstance(n, int) or not (1 <= n <= 60) or str(wt).lower() not in types:
                raise ValueError(f"The settings file contains a bad code rule ({n}={wt}). Nothing was imported.")
            rules.append((n, types[str(wt).lower()]))
    backup_now("before_import")
    added = {"settings": 0, "rules": 0, "years": 0, "payees": 0, "mdas": 0, "codes": 0, "templates": 0}
    c = _c()
    try:
        c.execute("BEGIN IMMEDIATE")
        if "settings" in parts:
            for k, v in b["settings"].items():
                if str(k).startswith(_EXPORT_SKIP_PREFIX) or k in _EXPORT_SKIP_EXACT:
                    continue
                c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (str(k), None if v is None else str(v)))
                added["settings"] += 1
        if "rules" in parts:
            c.execute("DELETE FROM code_length_rules")
            c.executemany("INSERT INTO code_length_rules(digits,warrant_type) VALUES(?,?)", rules)
            added["rules"] = len(rules)
        if "years" in parts:
            for y in b["years"]:
                if parse_year(y) is not None:
                    added["years"] += c.execute("INSERT OR IGNORE INTO fiscal_years(year) VALUES(?)", (parse_year(y),)).rowcount
        if "payees" in parts:
            known = {r[0].lower() for r in c.execute("SELECT payee FROM payees") if r[0]}
            for name, used in b["payees"]:
                n = clean_payee(name)
                if n and n.lower() not in known:
                    c.execute("INSERT INTO payees(payee,last_used_at) VALUES(?,?)", (n, used)); known.add(n.lower()); added["payees"] += 1
        if "mdas" in parts:
            have_b = {(r[0], r[1]) for r in c.execute("SELECT organisation_code,organisation_economic_code FROM budget")}
            have_orgs = {r[0] for r in c.execute("SELECT DISTINCT organisation_code FROM budget")}
            new_orgs = set()
            for org, name, code, dsc in b["budget"]:
                if not org or not code or (org, code) in have_b:
                    continue
                c.execute("INSERT INTO budget(organisation_code,organisation_name,organisation_economic_code,description) VALUES(?,?,?,?)", (org, name, code, dsc))
                have_b.add((org, code))
                if org not in have_orgs: new_orgs.add(org)
            have_c = {r[0] for r in c.execute("SELECT organisation_economic_code FROM economic_codes")}
            for code, dsc in b["economic_codes"]:
                if code and code not in have_c:
                    c.execute("INSERT INTO economic_codes(organisation_economic_code,description) VALUES(?,?)", (code, dsc)); have_c.add(code); added["codes"] += 1
            added["mdas"] = len(new_orgs)
        if "templates" in parts:                    # saved line items: missing ones are added, an existing one is never overwritten
            nowt = datetime.now().isoformat(timespec="seconds")
            for org, name, code, dsc, used in (b.get("templates") or []):
                if not (org and name and code and dsc):
                    continue
                same = c.execute("SELECT 1 FROM line_templates WHERE org_code=? AND (name=? COLLATE NOCASE OR (economic_code=? AND description=? COLLATE NOCASE))",
                                 (org, name, code, dsc)).fetchone()
                if not same:
                    c.execute("INSERT INTO line_templates(org_code,name,economic_code,description,created_at,updated_at,use_count) VALUES(?,?,?,?,?,?,?)",
                              (org, name, code, dsc, nowt, nowt, int(used or 0))); added["templates"] += 1
        _audit(c, "IMPORT SETTINGS", f"{os.path.basename(path)} exported {doc.get('exported_at')}: " + ", ".join(f"{k}={v}" for k, v in added.items()))
        c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()
    invalidate_payees()
    reset_option_cache()
    log.info("IMPORT settings from %s: %s", os.path.basename(path), added)
    return added


# --------------------------------------------------------------------------
# Saved line items (reusable MDA + Economic Code + Description), per MDA
# --------------------------------------------------------------------------
class TemplateError(WarrantError):
    """A saved line item could not be created / changed / used; the message is written for the person at the keyboard."""


def _one_line(text):
    return " ".join(str(text or "").replace("\u00a0", " ").split())


def template_type(code):
    """'Recurrent' / 'Capital' from the code's digit count (the same rules the warrant validation uses), or None when no rule applies."""
    n = code_digits(code)
    return next((t for d, t in code_rules() if d == n), None)


def _tpl_row(r, org_names):
    d = dict(r)
    d["org_name"] = org_names.get(d["org_code"], d["org_code"])
    d["type"] = template_type(d["economic_code"])
    return d


def _org_names(c):
    return {r[0]: r[1] for r in c.execute("SELECT organisation_code,MAX(organisation_name) FROM budget GROUP BY organisation_code")}


def validate_template(org_code, name, code, description):
    """List of clear problems with a proposed saved item ([] = fine)."""
    e = []
    org_code, name, code, description = str(org_code or "").strip(), _one_line(name), str(code or "").strip(), _one_line(description)
    if not org_code:
        e.append("Choose the MDA this saved item belongs to.")
    if not name:
        e.append("Give the saved item a name (for example: PV - Common Services).")
    elif len(name) > 80:
        e.append("The name is too long (80 characters at most).")
    if not code:
        e.append("Enter or search the economic code.")
    elif org_code and not code.startswith(org_code + "/"):
        e.append(f"Economic code {code} does not belong to this MDA ({org_code}).")
    elif code not in known_codes([code]):
        e.append(f"Economic code {code} is not in the database. Use Search Code to pick an existing one.")
    if not description:
        e.append("Enter the description.")
    elif len(description) > 500:
        e.append("The description is too long (500 characters at most).")
    return e


def save_template(org_code, name, code, description, template_id=None):
    """Creates (template_id None) or edits one saved item. Never overwrites another item: a name or a code+description that
    already exists for the same MDA is refused with a message pointing at the existing item. Returns the stored row."""
    org_code, name, code, description = str(org_code or "").strip(), _one_line(name), str(code or "").strip(), _one_line(description)
    errs = validate_template(org_code, name, code, description)
    if errs:
        raise TemplateError("\n".join(errs))
    now = datetime.now().isoformat(timespec="seconds")
    c = _c()
    try:
        c.execute("BEGIN IMMEDIATE")
        if template_id is not None:
            cur = c.execute("SELECT org_code FROM line_templates WHERE id=?", (template_id,)).fetchone()
            if cur is None:
                raise TemplateError("This saved item no longer exists (it may have been removed). Nothing was changed.")
            if cur["org_code"] != org_code:
                raise TemplateError("A saved item cannot be moved to another MDA. Create a new one for that MDA instead.")
        dup = c.execute("SELECT name FROM line_templates WHERE org_code=? AND name=? COLLATE NOCASE AND id IS NOT ?", (org_code, name, template_id)).fetchone()
        if dup:
            raise TemplateError(f"This MDA already has a saved item named '{dup['name']}'. Edit that one, or choose a different name. Nothing was saved.")
        dup = c.execute("SELECT name FROM line_templates WHERE org_code=? AND economic_code=? AND description=? COLLATE NOCASE AND id IS NOT ?",
                        (org_code, code, description, template_id)).fetchone()
        if dup:
            raise TemplateError(f"This code and description are already saved for this MDA as '{dup['name']}'. Nothing was saved.")
        if template_id is None:
            cur = c.execute("INSERT INTO line_templates(org_code,name,economic_code,description,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                            (org_code, name, code, description, now, now))
            tid, verb = cur.lastrowid, "CREATED"
        else:
            c.execute("UPDATE line_templates SET name=?,economic_code=?,description=?,updated_at=? WHERE id=?", (name, code, description, now, template_id))
            tid, verb = template_id, "UPDATED"
        c.commit()
    except sqlite3.IntegrityError as exc:
        c.rollback()
        raise TemplateError("This saved item clashes with an existing one (same name, or same code and description). Nothing was saved.") from exc
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    log.info("SAVED ITEM %s id=%s mda=%s name=%s", verb, tid, org_code, name)
    return get_template(tid)


def get_template(template_id):
    c = _c()
    try:
        r = c.execute("SELECT * FROM line_templates WHERE id=?", (int(template_id),)).fetchone()
        return _tpl_row(r, _org_names(c)) if r else None
    finally:
        c.close()


def list_templates(org_code=None, warrant_type=None, search=""):
    """Saved items, most recently / most often used first. org_code limits to one MDA; warrant_type hides items whose code is not allowed
    on that type (so a Capital warrant never offers a Recurrent item); search matches name, code or description."""
    c = _c()
    try:
        names = _org_names(c)
        sql, args = "SELECT * FROM line_templates WHERE 1=1", []
        if org_code:
            sql += " AND org_code=?"; args.append(str(org_code).strip())
        q = _one_line(search).lower()
        if q:
            sql += " AND (instr(lower(name),?)>0 OR instr(lower(economic_code),?)>0 OR instr(lower(description),?)>0)"; args += [q, q, q]
        sql += " ORDER BY COALESCE(last_used_at,'') DESC, use_count DESC, name COLLATE NOCASE"
        rows = [_tpl_row(r, names) for r in c.execute(sql, args)]
    finally:
        c.close()
    if warrant_type:
        rules, strict = code_rules(), strict_unlisted()
        rows = [r for r in rows if check_code_type(r["economic_code"], warrant_type, rules, strict) is None]
    return rows


def template_org_codes():
    """MDA codes that have at least one saved item, with their names: [(code, name)]."""
    c = _c()
    try:
        names = _org_names(c)
        return sorted(((r[0], names.get(r[0], r[0])) for r in c.execute("SELECT DISTINCT org_code FROM line_templates")), key=lambda x: x[1].lower())
    finally:
        c.close()


def delete_template(template_id):
    """Removes a saved item. Warrants that already used it are not affected (their lines hold their own copy). Returns True if it existed."""
    c = _c()
    try:
        r = c.execute("SELECT name,org_code FROM line_templates WHERE id=?", (int(template_id),)).fetchone()
        n = c.execute("DELETE FROM line_templates WHERE id=?", (int(template_id),)).rowcount
        c.commit()
    finally:
        c.close()
    if n:
        log.info("SAVED ITEM DELETED id=%s mda=%s name=%s", template_id, r["org_code"], r["name"])
    return bool(n)


def parse_amount(text):
    """'1,250,000.50' / 'N1000' / '\u20a6 2,500' -> 1250000.5 ...; raises TemplateError with a clear message when it is not a positive amount."""
    t = str(text or "").replace("\u20a6", "").replace(" ", "").strip()
    if t[:1] in ("N", "n"):
        t = t[1:]
    if "_" in t or re.search(r"[eE]", t) or re.search(r"\.[^,]*,", t):      # 1_000, 1e3 and European 1.250,50 are refused, never guessed
        raise TemplateError("Enter the amount as a number, for example 1,250,000.00.")
    if "," in t:
        integer, dot, fraction = t.partition(".")
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+", integer) or (dot and not re.fullmatch(r"\d+", fraction)):
            raise TemplateError("Enter the amount as a number, for example 1,250,000.00.")
    elif not re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)", t):
        raise TemplateError("Enter the amount as a number, for example 1,250,000.00.")
    t = t.replace(",", "")
    try:
        v = float(t)
    except ValueError:
        raise TemplateError("Enter the amount as a number, for example 1,250,000.00.")
    if v != v or v in (float("inf"), float("-inf")):
        raise TemplateError("Enter the amount as a number, for example 1,250,000.00.")
    if v <= 0:
        raise TemplateError("The amount must be greater than zero.")
    if v > MAX_AMOUNT:
        raise TemplateError("That amount is too large.")
    return round(v, 2)


def line_from_template(template_id, warrant_type, payee, amount):
    """Builds ONE schedule line from a saved item + payee + amount, applying the same checks as a hand-typed line
    (code in the database, Recurrent/Capital rule for this warrant, payee present, amount > 0) and remembering the payee and the item's use.
    Returns {'code','payee','description','amount'} exactly as a normal line; raises TemplateError with a clear message otherwise."""
    t = get_template(template_id)
    if t is None:
        raise TemplateError("This saved item no longer exists. Choose another one.")
    if t["economic_code"] not in known_codes([t["economic_code"]]):
        raise TemplateError(f"The economic code {t['economic_code']} of this saved item is no longer in the database. Edit or remove the saved item.")
    if warrant_type:
        msg = check_code_type(t["economic_code"], warrant_type)
        if msg:
            raise TemplateError(msg)
    name = canonical_payee(payee)
    if not name:
        raise TemplateError("Enter or choose the payee / beneficiary.")
    amt = parse_amount(amount)
    c = _c()
    try:
        c.execute("UPDATE line_templates SET use_count=use_count+1,last_used_at=? WHERE id=?", (datetime.now().isoformat(timespec="microseconds"), t["id"]))
        _remember_in(c, [name], datetime.now().isoformat(timespec="seconds"))
        c.commit()
    finally:
        c.close()
    invalidate_payees()
    return {"code": t["economic_code"], "payee": name, "description": t["description"], "amount": amt}


class LineError(TemplateError):
    """The lines typed in the Add Line window cannot be added; the message lists every problem."""


def lines_for_code(org_code, warrant_type, code, description, pairs):
    """The Add Line window: ONE economic code + description with SEVERAL beneficiaries and amounts -> a list of normal schedule lines.
    `pairs` = [(payee, amount_text_or_number), ...]. The description defaults to the code's own description in the database.
    Same checks as a hand-typed line: code belongs to the MDA and exists, Recurrent/Capital rule, payee present, amount > 0.
    Raises LineError listing every problem; otherwise remembers each accepted payee and returns
    [{'code','payee','description','amount'}]."""
    org_code, code = str(org_code or "").strip(), str(code or "").strip()
    desc = _one_line(description) or describe_code(code)
    errs = []
    if not code:
        errs.append("Enter or search the economic code.")
    elif org_code and not code.startswith(org_code + "/"):
        errs.append(f"Economic code {code} does not belong to this MDA ({org_code}).")
    elif code not in known_codes([code]):
        errs.append(f"Economic code {code} is not in the database. Use Search Code to pick an existing one.")
    elif warrant_type:
        msg = check_code_type(code, warrant_type)
        if msg:
            errs.append(msg)
    if code and not desc:
        errs.append("Enter the description.")
    if not pairs:
        errs.append("Add at least one beneficiary and amount.")
    lines = []
    for i, (payee, amount) in enumerate(pairs, 1):
        who = canonical_payee(payee)
        tag = f"Beneficiary {i}" + (f" ({who})" if who else "")
        if not who:
            errs.append(f"{tag}: enter the payee / beneficiary.")
        try:
            amt = amount if isinstance(amount, (int, float)) and not isinstance(amount, bool) else parse_amount(amount)
            if amt <= 0:
                raise TemplateError("The amount must be greater than zero.")
        except TemplateError as exc:
            errs.append(f"{tag}: {exc}"); continue
        if who:
            lines.append({"code": code, "payee": who, "description": desc, "amount": round(float(amt), 2)})
    if errs:
        raise LineError("\n".join(errs))
    c = _c()
    try:
        _remember_in(c, [line["payee"] for line in lines], datetime.now().isoformat(timespec="seconds"))
        c.commit()
    except Exception as exc:
        c.rollback()
        raise LineError(f"The payee could not be remembered: {exc}") from exc
    finally:
        c.close()
    invalidate_payees()
    return lines


def remember_code_description(org_code, code, description):
    """Keeps this MDA + code + description for next time (a saved item named after the description). Never overwrites or duplicates:
    when it is already saved (same name or same code + description) nothing happens. Returns the new item or None."""
    org_code, code, desc = str(org_code or "").strip(), str(code or "").strip(), _one_line(description)
    base = desc[:80] or code
    for name in (base, f"{base[:60]} ({code.rsplit('/', 1)[-1][:16]})"):
        try:
            return save_template(org_code, name, code, desc)
        except TemplateError as exc:
            if "already saved" in str(exc):
                return None                               # this code + description is already a saved item
            if "named" in str(exc):
                continue                                  # name taken by something else: try the longer name
            return None
    return None

