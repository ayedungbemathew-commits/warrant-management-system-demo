"""Shareable, editable warrant files  (*.wmsw).

A .wmsw file is ONE complete warrant that can travel by WhatsApp, e-mail, a USB stick ... and be opened, edited, saved and printed in any other
copy of the WMS, like an Excel file. It carries:

  warrant     everything saved for the warrant: header, Warrant No., date, year, type, every MDA with its lines (codes, payees, descriptions,
              amounts), and the table / page / letterhead formatting
  uid         the warrant's identity. The same warrant always has the same uid, so opening a file for a warrant you already have
              finds YOUR copy instead of making a second one
  revision    how many times it has been saved (lets the WMS tell which copy is newer)
  summary     Warrant No., date, type, year, grand total and a total per MDA -- re-checked against the lines when the file is opened
  mda_info    the MDAs and economic codes the warrant uses (so a computer that lacks one can be told exactly what to add)
  options     printing options that change how the warrant looks (e.g. UPPERCASE payees)
  style       the text formatting (font sizes, bold) for this warrant type
  checksum    SHA-256 of all of the above: if the file is cut off, garbled by a chat app or edited with another program, it is refused

It is NOT encrypted: anyone who has the file can read it. Share it only with people who should see the warrant.
No screen code lives here, so all of it is covered by automated tests.
"""
import copy
import hashlib
import json
import math
import os
import re
from datetime import datetime
import wms_dates as WD

FORMAT = "WMS-WARRANT"
FORMAT_VERSION = 2        # 1: dates written MM/DD (V12.5 - V12.6)   2: dates written DD/MM/YYYY (an older program refuses a version-2 file rather than misread its dates)
EXT = ".wmsw"
MAX_BYTES = 25 * 1024 * 1024
MAX_LINES = 20000


class ShareFileError(Exception):
    """The message is written for the person at the keyboard."""


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def checksum(env):
    body = {k: v for k, v in env.items() if k != "checksum"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def same_content(a, b):
    """True when two warrants hold exactly the same information (field order does not matter)."""
    return canonical(a) == canonical(b)


def totals_of(data):
    """{'grand': float, 'mdas': [{'code','name','lines','total'}]} -- always recomputed from the lines, never trusted."""
    mdas, grand = [], 0.0
    for m in data.get("mdas") or []:
        t = round(sum(float(x.get("amount", 0)) for x in m.get("items") or []), 2)
        mdas.append({"code": m.get("code", ""), "name": m.get("name", ""), "lines": len(m.get("items") or []), "total": t})
        grand += t
    return {"grand": round(grand, 2), "mdas": mdas}


def mda_info_of(data):
    """The MDAs and economic codes this warrant uses, with the description each line carries."""
    out = []
    for m in data.get("mdas") or []:
        seen, codes = set(), []
        for x in m.get("items") or []:
            c = str(x.get("code", "")).strip()
            if c and c not in seen:
                seen.add(c); codes.append({"code": c, "description": str(x.get("description", "")).strip()})
        out.append({"code": m.get("code", ""), "name": m.get("name", ""), "codes": codes})
    return out


def build(row, app_version="", style=None, payee_upper=False, exported_at=None):
    """The file contents for one SAVED warrant (a row from the records: columns + parsed `data`)."""
    data = copy.deepcopy(row["data"])
    data["date"] = WD.normalise(data.get("date", "")) or data.get("date", "")
    tot = totals_of(data)
    env = {
        "format": FORMAT, "format_version": FORMAT_VERSION,
        "app": {"name": "Warrant System", "version": str(app_version)},
        "exported_at": exported_at or datetime.now().isoformat(timespec="seconds"),
        "uid": row.get("uid") or "",
        "revision": int(row.get("revision") or 1),
        "summary": {"warrant_no": data.get("warrant_no", ""), "date": data.get("date", ""), "warrant_type": data.get("warrant_type", ""),
                    "year": data.get("year", ""), "grand_total": tot["grand"], "mdas": tot["mdas"]},
        "warrant": data,
        "mda_info": mda_info_of(data),
        "options": {"payee_upper": bool(payee_upper)},
        "style": copy.deepcopy(style) if style else {},
    }
    env["checksum"] = checksum(env)
    return env


def suggest_name(data):
    no = re.sub(r'[\\/:*?"<>|\s]+', "-", str(data.get("warrant_no", "")).strip()).strip("-.") or "warrant"
    return f"Warrant_{no}{EXT}"


def write(path, env):
    """Writes the file (a temporary name first, renamed when complete, so a half-written file never appears)."""
    path = str(path)
    if not path.lower().endswith(EXT):
        path += EXT
    tmp = path + ".part"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(canonical(env))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try: os.remove(tmp)
        except OSError: pass
        raise ShareFileError(f"The file could not be written to {path}\n({exc.strerror or exc})") from exc
    return path


def _need(cond, msg):
    if not cond:
        raise ShareFileError(msg)


def verify(env):
    """Checks a parsed file; raises ShareFileError with a plain explanation, returns env when it is sound."""
    _need(isinstance(env, dict) and env.get("format") == FORMAT, "This is not a WMS warrant file.")
    ver = env.get("format_version")
    _need(isinstance(ver, int), "This warrant file has no version number, so it cannot be trusted.")
    _need(ver <= FORMAT_VERSION, f"This file was made by a newer version of the WMS (file format {ver}). Update this WMS, then open it again.")
    _need(isinstance(env.get("checksum"), str) and env["checksum"] == checksum(env),
          "This warrant file is damaged or was changed outside the WMS (a chat app may have cut it off or altered it). "
          "Ask the sender to send it again - for example as a document, not as a photo or pasted text.")
    d = env.get("warrant")
    _need(isinstance(d, dict), "This warrant file contains no warrant.")
    _need(isinstance(env.get("uid"), str) and env["uid"].strip(), "This warrant file has no identity (uid).")
    for k in ("warrant_no", "date", "warrant_type"):
        _need(isinstance(d.get(k), str), f"This warrant file is incomplete: '{k}' is missing.")
    mdas = d.get("mdas")
    _need(isinstance(mdas, list), "This warrant file is incomplete: it has no MDA list.")
    n = 0
    for m in mdas:
        _need(isinstance(m, dict) and isinstance(m.get("items"), list), "This warrant file is incomplete: an MDA has no lines.")
        for x in m["items"]:
            n += 1
            _need(isinstance(x, dict), "This warrant file has an unreadable line.")
            for k in ("code", "payee", "description"):
                _need(isinstance(x.get(k), str), f"This warrant file has a line without '{k}'.")
            a = x.get("amount")
            _need(isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a), "This warrant file has a line with an amount that is not a number.")
    _need(n <= MAX_LINES, "This warrant file is far larger than any real warrant.")
    claimed = (env.get("summary") or {}).get("grand_total")
    _need(isinstance(claimed, (int, float)) and abs(claimed - totals_of(d)["grand"]) < 0.005,
          "The total recorded in this file does not match its lines, so it cannot be trusted.")
    # Version-1 files (V12.5 - V12.6) carry MM/DD/YY(YY); version 2 carries DD/MM/YYYY. Convert after the original checksum is verified.
    if ver == 1:
        d["date"] = WD.from_mdy(d.get("date", "")) or d.get("date", "")
        env["format_version"] = FORMAT_VERSION
    else:
        d["date"] = WD.normalise(d.get("date", "")) or d.get("date", "")
    summary = env.get("summary")
    if isinstance(summary, dict):
        summary["date"] = d["date"]
    # Keep the normalized in-memory envelope self-consistent for callers that
    # subsequently save or share it again. The original checksum was validated above.
    env["checksum"] = checksum(env)
    return env


def read(path):
    """Opens a .wmsw file and verifies it. Raises ShareFileError (never anything else) when it cannot be used."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise ShareFileError(f"The file cannot be opened:\n{path}\n({exc.strerror or exc})") from exc
    _need(size <= MAX_BYTES, "This file is far too large to be a warrant.")
    _need(size > 0, "This file is empty.")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        env = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShareFileError("This is not a WMS warrant file, or it was cut off or damaged on the way. Ask the sender to send it again.") from exc
    except OSError as exc:
        raise ShareFileError(f"The file cannot be read:\n{path}\n({exc.strerror or exc})") from exc
    return verify(env)


def describe(env):
    """One line for dialogs, with the warrant date shown as DD/MM/YYYY."""
    s = env.get("summary") or {}
    n = len(s.get("mdas") or [])
    return (f"Warrant {s.get('warrant_no', '')}  -  {s.get('warrant_type', '')}  -  {s.get('date', '')}  -  "
            f"\u20a6{float(s.get('grand_total') or 0):,.2f}  ({n} MDA{'s' if n != 1 else ''})")
