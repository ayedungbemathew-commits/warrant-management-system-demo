"""Weekly PDF export: every warrant created in a week is written as a PDF into a folder you choose.

How it works
  * A "week" is a calendar week that starts on the day you pick (default Monday) and ends 6 days later.
  * When a week has FINISHED, its warrants are exported once. Warrants are picked by the date they were saved
    (default) or by the date printed on the warrant.
  * Each week gets its own sub-folder  <your folder>\\Week_2026-09-28_to_2026-10-04\\  with one PDF per warrant
    (Warrant_<number>_<type>.pdf) and an Export_Summary.txt listing what was exported and anything that was not.
  * The PDF is built by the same engine, with the same layout and the same checks, as Export PDF / Print.
  * Catch-up: weeks that finished while the program was closed are exported the next time it runs.
  * It runs inside the program (at start-up and then every hour) and, if you create the Windows schedule, also when the
    program is closed:  the program is started with --weekly-export, does the export without a window and exits.
  * Nothing is ever deleted from your folder. Re-exporting a week overwrites the PDF of the same name.

No screen code lives here, so all of it is covered by automated tests.
"""
import copy
import json
import os
import re
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta

import wms_store as S
import wms_dates as WD
import wms_log as L

log = L.get()

K_ON = "weekly_export_on"
K_DIR = "weekly_export_dir"
K_START = "weekly_export_week_start"            # 0 = Monday ... 6 = Sunday
K_BASIS = "weekly_export_basis"                 # "created" | "warrant_date"
K_DONE = "weekly_export_done_through"           # last DATE (ISO) already exported by the automatic run
K_LAST = "weekly_export_last_run"               # text shown in the dialog
K_TASK_DAY = "weekly_export_task_day"
K_TASK_TIME = "weekly_export_task_time"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TASK_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
BASES = {"created": "the date the warrant was saved", "warrant_date": "the date printed on the warrant"}
TASK_NAME = "Warrant System Weekly Export"
LOCK_STALE_SECONDS = 30 * 60

# One PDF is built at a time program-wide (the screen preview takes the same lock), so a background export and the
# preview can never use the PDF engine's shared font tables at the same moment.
BUILD_LOCK = threading.RLock()


class ExportError(Exception):
    """The export could not run at all (folder missing / not writable, PDF engine absent)."""


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
def _parse_iso(text):
    try:
        return date.fromisoformat(str(text or "").strip())
    except ValueError:
        return None


def config():
    ws = str(S.get(K_START, "0") or "0").strip()
    basis = S.get(K_BASIS, "created")
    return {"on": S.get(K_ON, "0") == "1",
            "dir": (S.get(K_DIR, "") or "").strip(),
            "week_start": int(ws) if ws.isdigit() and 0 <= int(ws) <= 6 else 0,
            "basis": basis if basis in BASES else "created",
            "done": _parse_iso(S.get(K_DONE, "")),
            "last": S.get(K_LAST, "") or "",
            "task_day": S.get(K_TASK_DAY, "MON") if S.get(K_TASK_DAY, "MON") in TASK_DAYS else "MON",
            "task_time": S.get(K_TASK_TIME, "07:00") or "07:00"}


def save_config(on, folder, week_start, basis):
    """Stores the weekly-export settings. Switching it ON again starts afresh from the last finished week
    (weeks that passed while it was switched off are not exported behind your back)."""
    folder = (folder or "").strip()
    if on:
        bad = folder_problem(folder)
        if bad:
            raise ValueError(bad)
    if week_start not in range(7):
        raise ValueError("Pick the day a week starts on.")
    if basis not in BASES:
        raise ValueError("Pick how warrants are chosen for a week.")
    was_on = S.get(K_ON, "0") == "1"
    S.put(K_DIR, folder)
    S.put(K_START, int(week_start))
    S.put(K_BASIS, basis)
    S.put(K_ON, "1" if on else "0")
    if on and not was_on:
        S.put(K_DONE, "")


def folder_problem(folder):
    """None when PDFs can be written into `folder` (it is created if missing), else a clear reason."""
    folder = (folder or "").strip()
    if not folder:
        return "Choose the folder the weekly PDFs should be saved in."
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        return f"The folder cannot be used: {folder}\n({exc.strerror or exc})"
    if not os.path.isdir(folder):
        return f"This is not a folder: {folder}"
    probe = os.path.join(folder, f".wms_write_test_{os.getpid()}.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError as exc:
        return f"The program cannot write into this folder: {folder}\n({exc.strerror or exc})"
    return None


# --------------------------------------------------------------------------
# weeks
# --------------------------------------------------------------------------
def week_start_of(d, week_start=0):
    return d - timedelta(days=(d.weekday() - week_start) % 7)


def week_end_of(d, week_start=0):
    return week_start_of(d, week_start) + timedelta(days=6)


def pending_ranges(today, week_start=0, done=None):
    """[(first_day, last_day)] of finished weeks still to export, oldest first. `done` = last day already exported.
    Never exported before: just the most recently finished week. The running week is never included."""
    last_end = week_start_of(today, week_start) - timedelta(days=1)
    first = week_start_of(last_end, week_start) if done is None else done + timedelta(days=1)
    out, cur = [], first
    while cur <= last_end:
        end = min(week_end_of(cur, week_start), last_end)
        out.append((cur, end))
        cur = end + timedelta(days=1)
    return out


def parse_date(text):
    """A date as people type it: DD/MM/YYYY (or DD/MM/YY), or YYYY-MM-DD."""
    d = WD.parse_input(text)
    if d is None:
        raise ValueError(f"'{str(text or '').strip()}' is not a date. Use DD/MM/YYYY, for example {WD.EXAMPLE}.")
    return d


def is_due(today=None):
    """Cheap check used by the screen's timer: is the automatic export on, set up, and is a finished week waiting?"""
    c = config()
    if not c["on"] or not c["dir"]:
        return False
    return bool(pending_ranges(today or date.today(), c["week_start"], c["done"]))


# --------------------------------------------------------------------------
# choosing the warrants
# --------------------------------------------------------------------------
def _date_of_warrant(text):
    return WD.parse_stored(text)


def warrants_between(start, end, basis="created"):
    """Saved warrants whose date falls in start..end (both days included), oldest first.
    basis 'created'  : the moment it was first saved (older records without that moment fall back to the printed date).
    basis 'warrant_date': the date printed on the warrant."""
    c = S._c()
    try:
        if basis == "warrant_date":
            rows = [r for r in c.execute("SELECT * FROM warrants ORDER BY id")
                    if (d := _date_of_warrant(r["warrant_date"])) and start <= d <= end]
        else:
            lo, hi = start.isoformat(), (end + timedelta(days=1)).isoformat()      # ISO text sorts like time
            rows = list(c.execute("SELECT * FROM warrants WHERE created_at >= ? AND created_at < ? ORDER BY created_at, id", (lo, hi)))
            rows += [r for r in c.execute("SELECT * FROM warrants WHERE created_at IS NULL OR created_at='' ORDER BY id")
                     if (d := _date_of_warrant(r["warrant_date"])) and start <= d <= end]
    finally:
        c.close()
    out = []
    for r in rows:
        try:
            out.append(S._row_dict(r))
        except Exception:                                   # a damaged record is reported, not allowed to stop the week
            out.append({"id": r["id"], "warrant_no": r["warrant_no"], "warrant_type": r["warrant_type"], "data": None,
                        "grand_total": r["grand_total"], "warrant_date": r["warrant_date"]})
    return out


def payload_from_row(row):
    """The record as the screen would print it after opening it (same defaults, same year rule)."""
    if not isinstance(row.get("data"), dict):
        raise ValueError("the saved data of this warrant cannot be read")
    d = copy.deepcopy(row["data"])
    wt = d.get("warrant_type") or row.get("warrant_type") or S.warrant_types()[0]
    d["warrant_type"] = wt
    d["warrant_no"] = (d.get("warrant_no") or row.get("warrant_no") or "").strip()
    d["date"] = d.get("date") or row.get("warrant_date") or ""
    year = S.parse_year(d.get("year")) or S.parse_year(row.get("fiscal_year")) or S.year_from_date(d.get("date")) or S.default_year()
    d["year"] = str(year)
    dflt = S.defaults_for_new_warrant(wt)
    for k in ("ref_prefix", "addressee", "state_short"):
        if not d.get(k):
            d[k] = dflt[k]
    d.setdefault("mdas", [])
    d["payee_upper"] = S.payee_upper_on()                   # same "UPPERCASE payees" choice as the screen
    return d


def pdf_name(row, used):
    """Warrant_<no>_<type>.pdf, safe for Windows ('/' in the number becomes '-'); a clash gets the record id added."""
    no = re.sub(r'[\\/:*?"<>|\s]+', "-", str(row.get("warrant_no") or "").strip()).strip("-.") or f"id{row['id']}"
    wt = re.sub(r"[^A-Za-z0-9_-]+", "", str(row.get("warrant_type") or ""))
    base = f"Warrant_{no}" + (f"_{wt}" if wt else "")
    name = base + ".pdf"
    if name.lower() in used:
        name = f"{base}_id{row['id']}.pdf"
    used.add(name.lower())
    return name


# --------------------------------------------------------------------------
# the export itself
# --------------------------------------------------------------------------
def _looks_like_pdf(path):
    try:
        with open(path, "rb") as fh:
            return os.path.getsize(path) > 500 and fh.read(5) == b"%PDF-"
    except OSError:
        return False


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def export_range(start, end, folder, style, basis="created", progress=None):
    """Exports every warrant of start..end into <folder>\\Week_<start>_to_<end>\\ . Returns a result dict:
    files [(no, file)], skipped [(no, reason)] (fail the same checks as Export PDF), failed [(no, reason)],
    retry True when a disk / folder problem means the range should be tried again later."""
    try:
        import wms_engine as E
    except ImportError as exc:
        raise ExportError("ReportLab is not installed, so PDFs cannot be made. Run:  python -m pip install -r requirements.txt") from exc
    bad = folder_problem(folder)
    if bad:
        raise ExportError(bad)
    week_dir = os.path.join(folder, f"Week_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}")
    try:
        os.makedirs(week_dir, exist_ok=True)
    except OSError as exc:
        raise ExportError(f"Could not create {week_dir}\n({exc.strerror or exc})") from exc
    rows = warrants_between(start, end, basis)
    res = {"start": start, "end": end, "dir": week_dir, "basis": basis, "total": len(rows),
           "files": [], "skipped": [], "failed": [], "retry": False, "rows": {}}
    used = set()
    for i, row in enumerate(rows, 1):
        no = row.get("warrant_no") or f"id{row['id']}"
        tmp = None
        try:
            payload = payload_from_row(row)
            try:
                shared_layout = json.loads(S.get(f"format_layout:{payload['warrant_type']}", "{}") or "{}")
            except (TypeError, ValueError):
                shared_layout = {}
            payload["table_fmt"] = E.norm_layout(payload["warrant_type"], shared_layout)
            errs = S.validate_payload(payload, historical=True)   # a saved warrant is printed as it was saved, whatever changed since
            if errs:
                res["skipped"].append((no, " ".join(errs[:2])))
                continue
            name = pdf_name(row, used)
            final = os.path.join(week_dir, name)
            tmp = final + ".part"
            with BUILD_LOCK:
                E.build_pdf(tmp, payload, style)
            if not _looks_like_pdf(tmp):
                raise ValueError("the PDF was not written correctly")
            os.replace(tmp, final)
            res["files"].append((no, name))
            res["rows"][no] = row
        except OSError as exc:                               # disk full / folder gone / file locked: try this range again later
            res["failed"].append((no, f"{exc.strerror or exc}")); res["retry"] = True
            log.warning("WEEKLY EXPORT: could not write %s: %s", no, exc)
        except Exception as exc:
            res["failed"].append((no, str(exc) or exc.__class__.__name__))
            log.exception("WEEKLY EXPORT: warrant %s could not be made into a PDF", no)
        finally:
            if tmp:
                _remove(tmp)
            if progress:
                try:
                    progress(i, len(rows))
                except Exception:
                    pass
    _write_summary(res)
    log.info("WEEKLY EXPORT %s..%s: %d exported, %d skipped, %d failed -> %s",
             start, end, len(res["files"]), len(res["skipped"]), len(res["failed"]), week_dir)
    return res


def _write_summary(res):
    """Export_Summary.txt next to the PDFs: what is in the folder, and what is missing and why."""
    L_ = [f"WARRANT EXPORT  {WD.fmt(res['start'])} to {WD.fmt(res['end'])}",
          f"Warrants chosen by: {BASES.get(res['basis'], res['basis'])}",
          f"Made on: {datetime.now():%d/%m/%Y %H:%M}",
          "", f"Exported: {len(res['files'])}   Not exported: {len(res['skipped']) + len(res['failed'])}   "
              f"Found for this week: {res['total']}", ""]
    if not res["total"]:
        L_.append("No warrants were created in this week.")
    for no, name in res["files"]:
        row = res["rows"].get(no, {})
        L_.append(f"  {no}  |  {row.get('warrant_type') or ''}  |  {row.get('warrant_date') or ''}  |  "
                  f"\u20a6{float(row.get('grand_total') or 0):,.2f}  |  {name}")
    if res["skipped"]:
        L_ += ["", "NOT EXPORTED (they fail the same checks as Export PDF - open the warrant, correct it, then export again):"]
        L_ += [f"  {no}: {why}" for no, why in res["skipped"]]
    if res["failed"]:
        L_ += ["", "FAILED:"] + [f"  {no}: {why}" for no, why in res["failed"]]
    try:
        with open(os.path.join(res["dir"], "Export_Summary.txt"), "w", encoding="utf-8-sig") as fh:
            fh.write("\n".join(L_) + "\n")
    except OSError as exc:
        log.warning("WEEKLY EXPORT: summary file not written: %s", exc)


# --------------------------------------------------------------------------
# one run at a time (the screen and a scheduled run may start together)
# --------------------------------------------------------------------------
def _lock_file():
    f = S._db_file()
    return os.path.join(os.path.dirname(f) if f else ".", "weekly_export.lock")


def try_lock():
    """Path of the lock when this run owns it, None when another run is working (a lock older than 30 minutes is abandoned)."""
    p = _lock_file()
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}".encode())
            os.close(fd)
            return p
        except FileExistsError:
            try:
                if datetime.now().timestamp() - os.path.getmtime(p) > LOCK_STALE_SECONDS:
                    os.remove(p); continue
            except OSError:
                pass
            return None
        except OSError:
            return None
    return None


def unlock(p):
    if p:
        _remove(p)


def describe(res):
    """One readable paragraph for a finished range."""
    s = f"{WD.fmt(res['start'])} - {WD.fmt(res['end'])}: {len(res['files'])} PDF(s) saved"
    if not res["total"]:
        s += " (no warrants were created that week)"
    n_bad = len(res["skipped"]) + len(res["failed"])
    if n_bad:
        s += f"; {n_bad} NOT exported"
    return s


def run_pending(style, today=None, progress=None):
    """The automatic export: every finished week that is still waiting, oldest first.
    Returns {'status': off|idle|busy|done|error, 'results': [...], 'message': text}."""
    c = config()
    if not c["on"]:
        return {"status": "off", "results": [], "message": "Weekly export is switched off."}
    bad = folder_problem(c["dir"])
    if bad:
        _remember(f"Could not run: {bad}")
        return {"status": "error", "results": [], "message": bad}
    ranges = pending_ranges(today or date.today(), c["week_start"], c["done"])
    if not ranges:
        return {"status": "idle", "results": [], "message": "Nothing is waiting: every finished week is already exported."}
    lock = try_lock()
    if lock is None:
        return {"status": "busy", "results": [], "message": "Another export is running."}
    results, status, message = [], "done", ""
    try:
        for a, b in ranges:
            try:
                r = export_range(a, b, c["dir"], style, c["basis"], progress)
            except ExportError as exc:
                status, message = "error", str(exc)
                break
            results.append(r)
            if r["retry"]:
                status, message = "error", "A disk or folder problem stopped the export; it will be tried again."
                break
            S.put(K_DONE, b.isoformat())                      # this week is finished for good
    finally:
        unlock(lock)
    text = " | ".join(describe(r) for r in results) or message
    _remember(text if status == "done" else f"{text} {message}".strip())
    return {"status": status, "results": results, "message": message or text}


def run_manual(start, end, folder, style, basis="created", progress=None):
    """Export any dates now (does not change the automatic schedule). Raises ExportError / ValueError."""
    if end < start:
        raise ValueError("The 'to' date is before the 'from' date.")
    lock = try_lock()
    if lock is None:
        raise ExportError("Another weekly export is running right now. Try again in a minute.")
    try:
        return export_range(start, end, folder, style, basis, progress)
    finally:
        unlock(lock)


def _remember(text):
    try:
        S.put(K_LAST, f"{datetime.now():%d/%m/%Y %H:%M}  -  {text}")
    except Exception:
        pass


def problems_text(results):
    """The part of a run the person must act on (skipped / failed warrants); '' when all went well."""
    out = []
    for r in results:
        for no, why in r["skipped"]:
            out.append(f"{no} (week {WD.fmt(r['start'])} - {WD.fmt(r['end'])}): not exported - {why}")
        for no, why in r["failed"]:
            out.append(f"{no} (week {WD.fmt(r['start'])} - {WD.fmt(r['end'])}): failed - {why}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Windows schedule (so it also runs when the program is closed)
# --------------------------------------------------------------------------
def task_command(script):
    """The program + arguments the schedule starts: the installed .exe, or pythonw + this script (no console window)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--weekly-export"]
    exe = sys.executable
    pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.name == "nt" and os.path.exists(pw):
        exe = pw
    return [exe, str(script), "--weekly-export"]


def valid_task_time(text):
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(text or "").strip()))


def schtasks_create_args(command, day, hhmm):
    if day not in TASK_DAYS:
        raise ValueError("Pick a day for the schedule.")
    if not valid_task_time(hhmm):
        raise ValueError("Enter the time as HH:MM, for example 07:00.")
    return ["schtasks", "/Create", "/F", "/SC", "WEEKLY", "/D", day, "/ST", hhmm.strip(), "/TN", TASK_NAME,
            "/TR", subprocess.list2cmdline(command)]


def schtasks_delete_args():
    return ["schtasks", "/Delete", "/F", "/TN", TASK_NAME]


def schtasks_query_args():
    return ["schtasks", "/Query", "/TN", TASK_NAME]


def run_schtasks(args, timeout=25):
    """(ok, text). Windows only; never raises."""
    if os.name != "nt":
        return False, "The Windows schedule is only available on Windows."
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode == 0, out
    except Exception as exc:
        return False, str(exc)
