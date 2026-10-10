"""
Warrant Management System Portfolio Demo  --  V12

The page layout / pagination / PDF engine is in wms_engine.py (same folder).
This file is the screen: data entry, saved-warrant records, format controls.
"""
import json, sqlite3, os, sys, shutil, tempfile, copy, threading, time, subprocess
from pathlib import Path
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    import wms_engine as E
    ENGINE_OK = True
except ImportError:            # ReportLab missing
    E = None
    ENGINE_OK = False

import wms_store as S
import wms_log as L
import wms_wheel as W
import wms_weekly as WK
import wms_dates as WD
import wms_share as SH
from wms_version import VERSION, RELEASE_DATE, APP_NAME

try:
    import wms_print as PRT
except ImportError:
    PRT = None

try:
    import wms_branding as B
except ImportError:
    B = None

try:
    import fitz  # PyMuPDF - used only to rasterize the generated PDF for the on-screen preview
except ImportError:
    fitz = None

def _data_dir():
    """The shared data folder (ProgramData). It is checked for write access; on a PC where that is not possible the current user's own
    folder is used instead, so the program starts instead of failing. A newly created folder is opened to all local users, so a second
    Windows account on the same PC can use the same warrants."""
    name = "WarrantSystemPortfolioDemo"
    options = [Path(os.environ.get("PROGRAMDATA") or Path.home()) / name, Path(os.environ.get("LOCALAPPDATA") or Path.home()) / name]
    for i, d in enumerate(options):
        fresh = not d.exists()
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / f".write_test_{os.getpid()}"
            probe.write_text("x"); probe.unlink()
        except OSError:
            continue
        if i == 0 and fresh and os.name == "nt":
            try:                                                   # best effort: Users = modify, inherited by the files created later
                subprocess.run(["icacls", str(d), "/grant", "*S-1-5-32-545:(OI)(CI)M", "/Q"], capture_output=True, timeout=20, creationflags=0x08000000)
            except Exception:
                pass
        return d
    return options[-1]


APP_DIR = _data_dir()
BUNDLED_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
BUNDLED_DB = BUNDLED_BASE / "warrant_data.db"
DB = APP_DIR / "warrant_data.db"
LOG_PATH = L.setup(APP_DIR, VERSION)            # one rotating log in <data folder>\logs\wms.log
if not DB.exists() and BUNDLED_DB.exists():
    shutil.copy2(BUNDLED_DB, DB)

STYLE_PATH = APP_DIR / "format_settings.json"
UI_PATH = APP_DIR / "ui_settings.json"

# Colour themes. Pick one with the "Theme" box in the header (or "System (auto)" to follow Windows light/dark mode);
# the choice is remembered. Every colour the screen uses comes from these palettes.
_LIGHT = dict(surface="#ffffff", ink="#1f2933", muted="#5b6b78", err="#b00020", head="#ffffff", shadow="#b7bec6",
              dis_bg="#f0f2f4", dis_fg="#9aa5b1", abg="#a9b7b5", afg="#eef2f2", is_dark=False)
THEMES = {
    # Carbon: near-black chrome, crisp white type, and a vivid green active-state accent.
    "Carbon":  dict(primary="#107C41", hi="#21A366", dark="#6DD58C", tint="#242424", sel="#174D31", bg="#101010", border="#454545",
                    tab="#1B1B1B", stripe="#21A366", canvas="#080808", surface="#1D1D1D", ink="#F5F5F5", muted="#C3C3C3", err="#FF7474",
                    head="#090909", shadow="#000000", dis_bg="#292929", dis_fg="#858585", abg="#174D31", afg="#B6E6C7",
                    logo="dark", is_dark=True),
    "Emerald": dict(_LIGHT, primary="#0f766e", hi="#14908a", dark="#0b5a54", tint="#e2f1ef", sel="#cfe8e4", bg="#f5f8f8", border="#d3dcdc",
                    tab="#e6eded", stripe="#0f766e", canvas="#dde4e4", logo="emerald"),
    "Ocean":   dict(_LIGHT, primary="#2563eb", hi="#3b82f6", dark="#1e40af", tint="#e6eefc", sel="#d2e2fb", bg="#f6f8fc", border="#d6dded",
                    tab="#e8edf7", stripe="#2563eb", canvas="#dde3ee", logo="ocean"),
    "Classic": dict(_LIGHT, primary="#334155", hi="#475569", dark="#1e293b", tint="#eef0f4", sel="#f1e6c6", bg="#f7f7f5", border="#dcdcd6",
                    tab="#ececE8", stripe="#b7791f", canvas="#e0e0da", logo="classic"),
}
SYSTEM = "System (auto)"
DEFAULT_THEME = "Carbon"
LIGHT_FALLBACK = "Emerald"        # light look used when Windows is in light mode
INK, MUTED, WHITE, ERR = "#1f2933", "#5b6b78", "#ffffff", "#b00020"     # re-pointed by set_palette() for the active theme


def system_is_dark():
    """True when Windows apps are set to dark mode (Settings > Personalisation > Colours)."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def resolve_theme(choice):
    """The palette name to draw with for a choice made in the Theme box."""
    if choice == SYSTEM:
        return "Carbon" if system_is_dark() else LIGHT_FALLBACK
    return choice if choice in THEMES else LIGHT_FALLBACK


def set_palette(T):
    global INK, MUTED, WHITE, ERR
    INK, MUTED, WHITE, ERR = T["ink"], T["muted"], T["surface"], T["err"]


def load_ui_settings():
    try:
        return json.loads(UI_PATH.read_text())
    except Exception:
        return {}


def save_ui_settings(d):
    try:
        UI_PATH.write_text(json.dumps(d, indent=2))
    except Exception:
        pass

PRINT_PATH = APP_DIR / "print_settings.json"


def load_print_settings():
    try:
        return json.loads(PRINT_PATH.read_text())
    except Exception:
        return {}


def save_print_settings(d):
    try:
        PRINT_PATH.write_text(json.dumps(d, indent=2))
    except Exception:
        pass


def load_style():
    style = {k: dict(v) for k, v in E.DEFAULT_STYLE.items()}
    try:
        if STYLE_PATH.exists():
            saved = json.loads(STYLE_PATH.read_text())
            for t in style:
                if t in saved:
                    style[t].update({k: v for k, v in saved[t].items() if k in style[t]})
                    for k in ("font_grand", "size_grand"):                 # saved before Grand Total had its own setting: start from the Total row's
                        if k not in saved[t]:
                            style[t][k] = style[t][k.replace("grand", "totals")]
    except Exception:
        pass
    E.unify_fonts(style)                       # fonts are one setting for all warrants
    return E.share_text_style(style, "Recurrent")  # migrate old per-type text preferences to one shared setting


def save_style(style):
    try:
        STYLE_PATH.write_text(json.dumps(style, indent=2))
    except Exception:
        pass


def load_layout_settings():
    layouts = {}
    for wt in S.warrant_types():
        try:
            saved = json.loads(S.get(f"format_layout:{wt}", "{}") or "{}")
        except (TypeError, ValueError):
            saved = {}
        layouts[wt] = E.norm_layout(wt, saved)
    return layouts


def save_layout_settings(layouts):
    for wt, layout in layouts.items():
        S.put(f"format_layout:{wt}", json.dumps(E.norm_layout(wt, layout)))


def db():
    c = sqlite3.connect(DB, timeout=20)                      # wait for another window / the weekly export instead of failing at once
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA synchronous=FULL")     # a saved warrant is on disk before Save returns
    return c


def run_job(owner, fn, done, tick=None, poll_ms=250):
    """Runs fn() in a background thread so the screen never freezes, then calls done(result, error) on the screen thread."""
    box = {}

    def work():
        try:
            box["r"] = fn()
        except BaseException as exc:
            box["e"] = exc
            try: L.get().error("background job failed: %s", exc, exc_info=True)
            except Exception: pass

    t = threading.Thread(target=work, daemon=True); t.start()

    def poll():
        try:
            if t.is_alive():
                if tick: tick()
                owner.after(poll_ms, poll)
            else:
                done(box.get("r"), box.get("e"))
        except tk.TclError:
            pass                                             # the window was closed while the job ran

    owner.after(poll_ms, poll)


def init_db():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS warrants(
      id INTEGER PRIMARY KEY AUTOINCREMENT, warrant_no TEXT UNIQUE, warrant_date TEXT,
      office TEXT, city TEXT, state TEXT, grand_total REAL, status TEXT, data_json TEXT,
      created_at TEXT, updated_at TEXT, warrant_type TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS payees(
      id INTEGER PRIMARY KEY AUTOINCREMENT, payee TEXT UNIQUE, last_used_at TEXT)""")
    # Every time a saved warrant is updated, the version it replaces is kept here.
    c.execute("""CREATE TABLE IF NOT EXISTS warrant_revisions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, warrant_id INTEGER, warrant_no TEXT,
      data_json TEXT, replaced_at TEXT)""")
    try:
        c.execute("ALTER TABLE warrants ADD COLUMN warrant_type TEXT")
    except Exception:
        pass  # column already exists on an older database file
    c.commit()
    c.close()


def organisations(query=""):
    c=db(); q=f"%{query.strip()}%"
    rows=c.execute("""SELECT DISTINCT organisation_code, organisation_name FROM budget
                     WHERE organisation_code IS NOT NULL AND TRIM(organisation_name) <> ''
                     AND (organisation_code LIKE ? OR organisation_name LIKE ?)
                     ORDER BY organisation_name""",(q,q)).fetchall(); c.close(); return rows

def desc(code):
    c=db(); r=c.execute("SELECT description FROM budget WHERE organisation_economic_code=? LIMIT 1",(code.strip(),)).fetchone(); c.close()
    return r["description"] if r else ""

def code_belongs_to(code,org_code): return code.strip().startswith(org_code.strip()+"/")

def payee_suggestions(query="",limit=30): return S.payee_suggest(query,limit)

def remember_payee(value): S.remember_payee(value)

def forget_payee_dialog(parent, name):
    """Asks, then removes a payee from the remembered list. True when it was removed. Saved warrants are never touched."""
    name = " ".join(str(name or "").split())
    if not name:
        messagebox.showinfo("Nothing to forget", "Type or select the payee name you want to remove first.", parent=parent); return False
    if not messagebox.askyesno("Forget payee", f'Remove "{name}" from the remembered payees list?\n\nThis only removes it from suggestions -- it will not affect any warrant that already used this name.', parent=parent):
        return False
    forget_payee(name)
    return True


def forget_payee(value):
    """Permanently removes a name from the remembered-payees list (typo saved by mistake)."""
    S.forget_payee(value)

def search_codes(q="",org_code=None):
    c=db(); q=f"%{q.strip()}%"
    if org_code:
        rows=c.execute("""SELECT organisation_economic_code code, description FROM economic_codes
                          WHERE organisation_economic_code LIKE ? AND
                          (organisation_economic_code LIKE ? OR description LIKE ?)
                          ORDER BY organisation_economic_code LIMIT 150""",(org_code.strip()+"/%",q,q)).fetchall()
    else:
        rows=c.execute("SELECT organisation_economic_code code, description FROM economic_codes WHERE organisation_economic_code LIKE ? OR description LIKE ? ORDER BY organisation_economic_code LIMIT 150",(q,q)).fetchall()
    c.close(); return rows
class OrganisationPicker(tk.Toplevel):
    def __init__(self,parent):
        super().__init__(parent); self.title("Select MDA / Organisation"); self.geometry("900x560"); self.transient(parent); self.grab_set(); self.result=None
        self.q=tk.StringVar(); f=ttk.Frame(self,padding=14); f.pack(fill="both",expand=True)
        ttk.Label(f,text="Search MDA / Organisation by name or organisation code",font=("Segoe UI",10,"bold")).pack(anchor="w")
        e=ttk.Entry(f,textvariable=self.q); e.pack(fill="x",pady=7); e.bind("<KeyRelease>",self.refresh)
        self.t=ttk.Treeview(f,columns=("code","name"),show="headings"); self.t.heading("code",text="Organisation Code"); self.t.heading("name",text="MDA / Organisation")
        self.t.column("code",width=180); self.t.column("name",width=650); self.t.pack(fill="both",expand=True); self.t.bind("<Double-1>",lambda e:self.choose())
        ttk.Button(f,text="Use Selected MDA",command=self.choose).pack(anchor="e",pady=8); e.focus_set(); self.refresh()
    def refresh(self,e=None):
        for x in self.t.get_children():self.t.delete(x)
        for r in organisations(self.q.get()):self.t.insert("","end",values=(r["organisation_code"],r["organisation_name"]))
    def choose(self):
        s=self.t.selection()
        if s:self.result=self.t.item(s[0],"values"); self.destroy()

class SecretDialog(tk.Toplevel):
    """Small modal asking for one or two hidden (password) fields. .result is a list of strings, or None if cancelled."""

    def __init__(self, parent, title, message, labels):
        super().__init__(parent)
        self.title(title); self.resizable(False, False); self.transient(parent); self.result = None
        f = ttk.Frame(self, padding=18); f.pack(fill="both", expand=True)
        ttk.Label(f, text=message, wraplength=380, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.vars, first = [], None
        for i, lab in enumerate(labels, 1):
            ttk.Label(f, text=lab, font=("Segoe UI", 10, "bold")).grid(row=i, column=0, sticky="w", pady=5)
            v = tk.StringVar(); self.vars.append(v)
            e = ttk.Entry(f, textvariable=v, show="\u25cf", width=30); e.grid(row=i, column=1, padx=(10, 0), pady=5)
            first = first or e
        bar = ttk.Frame(f); bar.grid(row=len(labels) + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="OK", style="Accent.TButton", command=self.ok).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self.ok()); self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set(); first.focus_set()
        self.update_idletasks()
        self.geometry(f"+{parent.winfo_rootx() + 200}+{parent.winfo_rooty() + 160}")
        self.wait_window(self)

    def ok(self):
        self.result = [v.get() for v in self.vars]; self.destroy()


def mda_gate(parent):
    """Asks for the MDA password (or creates it the first time). True only when MDA editing is unlocked."""
    if S.is_unlocked():
        return True
    if not S.has_password():
        d = SecretDialog(parent, "Create MDA Password",
                         "MDA editing is protected by a password, and none has been set yet.\n"
                         "Choose one now (at least 6 characters). Only people who know it can add, change or delete MDAs and codes.",
                         ["New password", "Confirm password"])
        if not d.result:
            return False
        if d.result[0] != d.result[1]:
            messagebox.showerror("Passwords differ", "The two passwords are not the same.", parent=parent); return False
        try:
            S.set_password(d.result[0]); S.unlock(d.result[0])
        except ValueError as exc:
            messagebox.showerror("Password", str(exc), parent=parent); return False
        messagebox.showinfo("Password set", "The MDA password has been created. Keep it safe.", parent=parent)
        return True
    while True:
        d = SecretDialog(parent, "MDA Editing \u2013 Password Required",
                         "Enter the MDA password to add, change or delete MDA records and codes.", ["Password"])
        if not d.result:
            return False
        try:
            if S.unlock(d.result[0]):
                return True
        except S.LockedOut as exc:
            messagebox.showerror("Locked", str(exc), parent=parent); return False
        messagebox.showerror("Wrong password", f"That password is not correct. {S.tries_left()} attempt(s) left before a short lock-out.", parent=parent)


def change_password_flow(parent):
    if not S.has_password():
        mda_gate(parent); return
    d = SecretDialog(parent, "Change MDA Password", "Enter the current password, then the new one (at least 6 characters).",
                     ["Current password", "New password", "Confirm new password"])
    if not d.result:
        return
    cur, new, conf = d.result
    if new != conf:
        messagebox.showerror("Passwords differ", "The new passwords are not the same.", parent=parent); return
    try:
        S.set_password(new, cur)
    except (ValueError, PermissionError) as exc:
        messagebox.showerror("Password not changed", str(exc), parent=parent); return
    S.lock()
    messagebox.showinfo("Password changed", "The MDA password was changed. Use the new one from now on.", parent=parent)


class MDAAdminDialog(tk.Toplevel):
    """Password-protected MDA / economic-code administration: add, rename, re-code, delete MDAs; add, edit, delete codes.
    Every action calls wms_store, which itself refuses to run unless the password session is open."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Edit MDA \u2013 Authorized Users Only"); self.geometry("1250x720"); self.transient(parent); self.changed = False
        self.q_m = tk.StringVar(); self.q_c = tk.StringVar(); self.cur = None
        f = ttk.Frame(self, padding=12); f.pack(fill="both", expand=True)
        top = ttk.Frame(f); top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="Changes here affect future warrants. Warrants already saved keep the MDA names and codes they were issued with.",
                  foreground=MUTED).pack(side="left")
        ttk.Button(top, text="Lock now", command=self.lock_now).pack(side="right")
        ttk.Button(top, text="Change password", command=lambda: change_password_flow(self)).pack(side="right", padx=6)
        pw = ttk.PanedWindow(f, orient="horizontal"); pw.pack(fill="both", expand=True)
        # ---- left: MDAs
        L = ttk.LabelFrame(pw, text="MDAs / Organisations", padding=8); pw.add(L, weight=1)
        e = ttk.Entry(L, textvariable=self.q_m); e.pack(fill="x", pady=(0, 6)); e.bind("<KeyRelease>", lambda ev: self.load_mdas())
        self.tm = ttk.Treeview(L, columns=("code", "name", "n"), show="headings", selectmode="browse")
        for c, h, w in [("code", "Code", 110), ("name", "MDA name", 300), ("n", "Codes", 55)]:
            self.tm.heading(c, text=h); self.tm.column(c, width=w, anchor="e" if c == "n" else "w")
        self.tm.pack(fill="both", expand=True); self.tm.bind("<<TreeviewSelect>>", self.on_mda)
        b = ttk.Frame(L); b.pack(fill="x", pady=(8, 0))
        for t, cmd in [("Add MDA", self.add_mda), ("Rename", self.rename), ("Change code", self.recode), ("Delete MDA", self.delete_mda)]:
            ttk.Button(b, text=t, command=cmd).pack(side="left", padx=(0, 5))
        # ---- right: economic codes of the selected MDA
        R = ttk.LabelFrame(pw, text="Economic codes of the selected MDA", padding=8); pw.add(R, weight=2)
        self.sel_lbl = ttk.Label(R, text="Select an MDA on the left.", font=("Segoe UI", 10, "bold")); self.sel_lbl.pack(anchor="w")
        e2 = ttk.Entry(R, textvariable=self.q_c); e2.pack(fill="x", pady=6); e2.bind("<KeyRelease>", lambda ev: self.load_codes())
        self.tc = ttk.Treeview(R, columns=("code", "desc"), show="headings", selectmode="browse")
        self.tc.heading("code", text="Economic code"); self.tc.heading("desc", text="Description")
        self.tc.column("code", width=230); self.tc.column("desc", width=520)
        self.tc.pack(fill="both", expand=True); self.tc.bind("<Double-1>", lambda ev: self.edit_code())
        b2 = ttk.Frame(R); b2.pack(fill="x", pady=(8, 0))
        for t, cmd in [("Add code", self.add_code), ("Edit description", self.edit_code), ("Delete code", self.delete_code)]:
            ttk.Button(b2, text=t, command=cmd).pack(side="left", padx=(0, 5))
        self.status = tk.StringVar(); ttk.Label(f, textvariable=self.status, foreground=MUTED).pack(anchor="w", pady=(6, 0))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.load_mdas(); self.grab_set(); e.focus_set()

    # ---- helpers
    def _do(self, fn, *args):
        """Run a protected store action; ask for the password again if the session timed out."""
        for _ in range(2):
            try:
                return True, fn(*args)
            except PermissionError:
                if not mda_gate(self):
                    return False, None
            except ValueError as exc:
                messagebox.showerror("Cannot do that", str(exc), parent=self); return False, None
            except Exception as exc:
                messagebox.showerror("Error", str(exc), parent=self); return False, None
        return False, None

    def lock_now(self):
        S.lock(); self.status.set("MDA editing is locked again. The password is needed for the next change.")

    def close(self):
        S.lock(); self.destroy()

    def load_mdas(self, keep=None):
        for x in self.tm.get_children():
            self.tm.delete(x)
        for r in S.list_mdas(self.q_m.get()):
            self.tm.insert("", "end", iid=r["code"], values=(r["code"], r["name"], r["n"]))
        if keep and self.tm.exists(keep):
            self.tm.selection_set(keep); self.tm.see(keep)
        else:
            self.cur = None; self.sel_lbl.config(text="Select an MDA on the left."); self.load_codes()

    def on_mda(self, e=None):
        s = self.tm.selection()
        if s:
            self.cur = s[0]; self.sel_lbl.config(text=f"{self.tm.item(s[0], 'values')[1]}   ({self.cur})"); self.load_codes()

    def load_codes(self):
        for x in self.tc.get_children():
            self.tc.delete(x)
        if self.cur:
            for r in S.list_codes(self.cur, self.q_c.get()):
                self.tc.insert("", "end", iid=r["code"], values=(r["code"], r["description"]))

    def _need_mda(self):
        if not self.cur:
            messagebox.showinfo("Select an MDA", "Select an MDA on the left first.", parent=self); return False
        return True

    def _need_code(self):
        s = self.tc.selection()
        if not s:
            messagebox.showinfo("Select a code", "Select an economic code first.", parent=self); return None
        return s[0]

    # ---- MDA actions
    def add_mda(self):
        d = _FormDialog(self, "Add MDA", [("MDA code", ""), ("MDA name", ""), ("First economic code (full, e.g. <MDA code>/21010101)", ""), ("Description of that code", "")])
        if not d.result:
            return
        ok, _ = self._do(S.add_mda, *[x.strip() for x in d.result])
        if ok:
            self.changed = True; self.q_m.set(""); self.load_mdas(d.result[0].strip()); self.status.set(f"MDA {d.result[0].strip()} added.")

    def rename(self):
        if not self._need_mda():
            return
        cur_name = self.tm.item(self.cur, "values")[1]
        d = _FormDialog(self, "Rename MDA", [("New name", cur_name)])
        if d.result and d.result[0].strip() != cur_name:
            ok, _ = self._do(S.rename_mda, self.cur, d.result[0])
            if ok:
                self.changed = True; self.load_mdas(self.cur); self.status.set("MDA renamed.")

    def recode(self):
        if not self._need_mda():
            return
        d = _FormDialog(self, "Change MDA code", [("New MDA code", self.cur)],
                        note="All economic codes of this MDA are renumbered to the new code. Saved warrants keep their old code and can still be viewed and reprinted; to edit a line of an old warrant you must pick the new code.")
        if d.result and d.result[0].strip() != self.cur:
            new = d.result[0].strip()
            ok, _ = self._do(S.change_mda_code, self.cur, new)
            if ok:
                self.changed = True; self.load_mdas(new); self.status.set(f"MDA code changed to {new}.")

    def delete_mda(self):
        if not self._need_mda():
            return
        name = self.tm.item(self.cur, "values")[1]; used = S.mda_usage(self.cur)
        msg = f"Delete the MDA \u201c{name}\u201d ({self.cur}) and ALL its economic codes?"
        if used:
            msg += f"\n\n{used} saved warrant(s) already use this MDA. They keep their data and can still be reprinted, but the MDA will no longer be selectable, and a line of an old warrant can only be edited after choosing another MDA / code."
        if not messagebox.askyesno("Delete MDA", msg + "\n\nThis cannot be undone.", icon="warning", parent=self):
            return
        ok, n = self._do(S.delete_mda, self.cur)
        if ok:
            self.changed = True; self.load_mdas(); self.status.set(f"MDA deleted ({n} code(s) removed).")

    # ---- code actions
    def add_code(self):
        if not self._need_mda():
            return
        d = _FormDialog(self, "Add economic code", [("Economic code", self.cur + "/"), ("Description", "")])
        if d.result:
            ok, _ = self._do(S.add_code, self.cur, d.result[0].strip(), d.result[1])
            if ok:
                self.changed = True; self.load_codes(); self.status.set("Economic code added.")

    def edit_code(self):
        code = self._need_code() if self.cur else None
        if not code:
            return
        d = _FormDialog(self, "Edit description", [("Description", self.tc.item(code, "values")[1])], note=f"Code {code}")
        if d.result:
            ok, _ = self._do(S.update_code, code, d.result[0])
            if ok:
                self.changed = True; self.load_codes(); self.status.set("Description updated.")

    def delete_code(self):
        code = self._need_code() if self.cur else None
        if code and messagebox.askyesno("Delete code", f"Delete economic code {code}?\n\nThis cannot be undone.", icon="warning", parent=self):
            ok, _ = self._do(S.delete_code, code)
            if ok:
                self.changed = True; self.load_codes(); self.load_mdas(self.cur); self.status.set("Economic code deleted.")


class _FormDialog(tk.Toplevel):
    """Generic small form: list of (label, initial value). .result = list of strings or None."""

    def __init__(self, parent, title, fields, note=""):
        super().__init__(parent)
        self.title(title); self.transient(parent); self.resizable(False, False); self.result = None
        f = ttk.Frame(self, padding=16); f.pack(fill="both", expand=True); self.vars = []; first = None
        if note:
            ttk.Label(f, text=note, foreground=MUTED, wraplength=520, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for i, (lab, init) in enumerate(fields, 1):
            ttk.Label(f, text=lab, wraplength=230, justify="left").grid(row=i, column=0, sticky="w", pady=5)
            v = tk.StringVar(value=init); self.vars.append(v)
            e = ttk.Entry(f, textvariable=v, width=58); e.grid(row=i, column=1, padx=(10, 0), pady=5); first = first or e
        bar = ttk.Frame(f); bar.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(bar, text="OK", style="Accent.TButton", command=self.ok).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self.ok()); self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set(); first.focus_set(); first.icursor("end")
        self.wait_window(self)

    def ok(self):
        self.result = [v.get() for v in self.vars]; self.destroy()


class AboutDialog(tk.Toplevel):
    """Version, where the data and the log are, and the change log."""
    def __init__(self, parent):
        super().__init__(parent); self.title("About & log"); self.transient(parent); self.geometry("760x600")
        f = ttk.Frame(self, padding=14); f.pack(fill="both", expand=True)
        head = ttk.Frame(f); head.pack(fill="x")
        self._logo = B.full_logo(150) if B else None
        if self._logo is not None: tk.Label(head, image=self._logo, bg="#ffffff", borderwidth=0).pack(side="left", padx=(0, 14))
        info = ttk.Frame(head); info.pack(side="left", anchor="n")
        ttk.Label(info, text=f"{APP_NAME}  \u2013  version {VERSION}", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(info, text=f"Released {RELEASE_DATE}\nData folder: {APP_DIR}\nLog file: {LOG_PATH}", foreground=MUTED, justify="left").pack(anchor="w", pady=(2, 8))
        ttk.Label(f, text="Change log", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        box = ttk.Frame(f); box.pack(fill="both", expand=True, pady=4)
        t = tk.Text(box, wrap="word", font=("Segoe UI", 10), height=16); sb = ttk.Scrollbar(box, command=t.yview); t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        try: t.insert("1.0", (BUNDLED_BASE / "CHANGELOG.md").read_text(encoding="utf-8"))
        except Exception: t.insert("1.0", "The change log file (CHANGELOG.md) was not found.")
        t.configure(state="disabled")
        bar = ttk.Frame(f); bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Open log folder", command=lambda: self.open_dir(Path(LOG_PATH).parent)).pack(side="right", padx=8)
        ttk.Button(bar, text="Open data folder", command=lambda: self.open_dir(APP_DIR)).pack(side="right")
        self.grab_set()

    def open_dir(self, d):
        try:
            if os.name == "nt": os.startfile(str(d))
            else: messagebox.showinfo("Folder", str(d), parent=self)
        except Exception as exc:
            messagebox.showerror("Folder", f"{d}\n\n{exc}", parent=self)


class BackupDialog(tk.Toplevel):
    """List of database backups with: back up now, restore the selected one, how many start-up copies to keep."""
    REASONS = {"start": "Start-up", "before_upgrade": "Before upgrade", "before_restore": "Before a restore", "before_import": "Before an import",
               "manual": "Made by hand", "auto": "Daily copy (older version)"}

    def __init__(self, parent, app):
        super().__init__(parent); self.app = app; self.title("Backups & restore"); self.transient(parent); self.geometry("720x430")
        f = ttk.Frame(self, padding=14); f.pack(fill="both", expand=True)
        ttk.Label(f, text="A copy of the database is made every time the program starts, before every upgrade, and before any restore or import.",
                  foreground=MUTED, wraplength=680, justify="left").pack(anchor="w")
        self.t = ttk.Treeview(f, columns=("when", "kind", "size"), show="headings", height=11, selectmode="browse")
        for c_, w_, h_ in (("when", 210, "Date and time"), ("kind", 200, "Made"), ("size", 100, "Size")):
            self.t.heading(c_, text=h_); self.t.column(c_, width=w_, anchor="w")
        self.t.pack(fill="both", expand=True, pady=8)
        row = ttk.Frame(f); row.pack(fill="x")
        ttk.Label(row, text="Start-up copies to keep:").pack(side="left")
        self.keep = tk.StringVar(value=S.get("backup_keep", "20")); ttk.Spinbox(row, from_=1, to=200, width=5, textvariable=self.keep).pack(side="left", padx=6)
        ttk.Button(row, text="Save", command=self.save_keep).pack(side="left")
        ttk.Label(row, text=S.backup_dir(), foreground=MUTED).pack(side="left", padx=12)
        bar = ttk.Frame(f); bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Restore selected\u2026", command=self.restore).pack(side="right", padx=8)
        ttk.Button(bar, text="Back up now", style="Accent.TButton", command=self.now).pack(side="right")
        self.load(); self.grab_set()

    def load(self):
        for x in self.t.get_children(): self.t.delete(x)
        try: rows = S.list_backups()
        except Exception as exc:
            messagebox.showerror("Backups", str(exc), parent=self); return
        for b in rows:
            self.t.insert("", "end", iid=b["path"], values=(datetime.fromtimestamp(b["mtime"]).strftime("%d/%m/%Y  %H:%M:%S"), self.REASONS.get(b["reason"], b["reason"]), f"{b['size'] / 1024 / 1024:.1f} MB"))

    def save_keep(self):
        try: n = int(self.keep.get()); assert 1 <= n <= 200
        except Exception:
            messagebox.showerror("Backups", "Enter a number from 1 to 200.", parent=self); return
        S.put("backup_keep", str(n)); S.prune_backups(); self.load()

    def now(self):
        try: S.backup_now("manual")
        except Exception as exc:
            messagebox.showerror("Backup failed", str(exc), parent=self); return
        self.load(); messagebox.showinfo("Backup", "Backup made.", parent=self)

    def restore(self):
        sel = self.t.selection()
        if not sel:
            messagebox.showinfo("Restore", "Select a backup in the list first.", parent=self); return
        if not mda_gate(self): return
        when = self.t.item(sel[0], "values")[0]
        if not messagebox.askyesno("Restore backup", f"Replace ALL current data with the backup from {when}?\n\nEverything saved after that time (warrants, payees, settings) will be gone from the program. "
                                   "A safety copy of the current data is made first, so this can be undone by restoring that copy.\n\nThe program will close afterwards; open it again.", icon="warning", parent=self):
            return
        try: S.restore_backup(sel[0])
        except Exception as exc:
            messagebox.showerror("Restore failed", str(exc), parent=self); return
        messagebox.showinfo("Restored", "The backup was restored. The program will now close; open it again.", parent=self)
        self.app.destroy()


class WeeklyExportDialog(tk.Toplevel):
    """Weekly PDF export: switch it on, choose the folder, export any dates now, and (Windows) let a schedule run it
    even when the program is closed. The work itself lives in wms_weekly.py."""

    def __init__(self, parent, app):
        super().__init__(parent); self.app = app; self.title("Weekly PDF export"); self.transient(parent); self.resizable(False, False)
        c = WK.config(); self._busy = False; self._prog = (0, 0); self._btns = []
        self.on = tk.BooleanVar(value=c["on"]); self.folder = tk.StringVar(value=c["dir"])
        self.wstart = tk.StringVar(value=WK.DAY_NAMES[c["week_start"]]); self.basis = tk.StringVar(value=c["basis"])
        self.t_day = tk.StringVar(value=WK.DAY_NAMES[WK.TASK_DAYS.index(c["task_day"])]); self.t_time = tk.StringVar(value=c["task_time"])
        self.d_from = tk.StringVar(); self.d_to = tk.StringVar(); self.msg = tk.StringVar(); self.task_msg = tk.StringVar()
        f = ttk.Frame(self, padding=18); f.pack(fill="both", expand=True)
        ttk.Checkbutton(f, text="Export every finished week's warrants to PDF automatically", variable=self.on).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(f, text="Save the PDFs in this folder", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(10, 4))
        ttk.Entry(f, textvariable=self.folder, width=62).grid(row=1, column=1, columnspan=2, sticky="w", padx=8, pady=(10, 4))
        ttk.Button(f, text="Browse\u2026", command=self.browse).grid(row=1, column=3, pady=(10, 4))
        ttk.Label(f, text="A week starts on").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.wstart, values=WK.DAY_NAMES, state="readonly", width=14).grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(f, text="Warrants are picked by", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="nw", pady=(6, 0))
        rb = ttk.Frame(f); rb.grid(row=3, column=1, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        ttk.Radiobutton(rb, text="the date the warrant was saved (created) \u2013 recommended", variable=self.basis, value="created").pack(anchor="w")
        ttk.Radiobutton(rb, text="the date printed on the warrant", variable=self.basis, value="warrant_date").pack(anchor="w")
        ttk.Label(f, text="Each finished week gets its own folder, for example  Week_2026-09-28_to_2026-10-04,  with one PDF per warrant "
                          "(Warrant_<number>_<type>.pdf) and an Export_Summary.txt that lists what was exported and anything that could not be. "
                          "The PDFs are made exactly as Export PDF makes them, with the same checks. While the program is open it looks every hour; "
                          "weeks that finished while it was closed are exported the next time it runs. Nothing in the folder is ever deleted.",
                  foreground=MUTED, wraplength=690, justify="left").grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Separator(f).grid(row=5, column=0, columnspan=4, sticky="ew", pady=12)
        ttk.Label(f, text="Also run when the program is closed (Windows schedule)", font=("Segoe UI", 10, "bold")).grid(row=6, column=0, columnspan=4, sticky="w")
        sched = ttk.Frame(f); sched.grid(row=7, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(sched, text="Every").pack(side="left")
        ttk.Combobox(sched, textvariable=self.t_day, values=WK.DAY_NAMES, state="readonly", width=12).pack(side="left", padx=6)
        ttk.Label(sched, text="at (HH:MM)").pack(side="left")
        ttk.Entry(sched, textvariable=self.t_time, width=7).pack(side="left", padx=6)
        self.b_make = ttk.Button(sched, text="Create / update schedule", command=self.make_schedule); self.b_make.pack(side="left", padx=(10, 0))
        self.b_drop = ttk.Button(sched, text="Remove schedule", command=self.drop_schedule); self.b_drop.pack(side="left", padx=8)
        ttk.Label(f, textvariable=self.task_msg, foreground=MUTED, wraplength=690, justify="left").grid(row=8, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Separator(f).grid(row=9, column=0, columnspan=4, sticky="ew", pady=12)
        ttk.Label(f, text="Export now", font=("Segoe UI", 10, "bold")).grid(row=10, column=0, sticky="w")
        now = ttk.Frame(f); now.grid(row=11, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(now, text="From").pack(side="left"); ttk.Entry(now, textvariable=self.d_from, width=12).pack(side="left", padx=6)
        ttk.Label(now, text="to").pack(side="left"); ttk.Entry(now, textvariable=self.d_to, width=12).pack(side="left", padx=6)
        ttk.Label(now, text="(DD/MM/YYYY)", foreground=MUTED).pack(side="left", padx=(0, 10))
        for text, cmd in (("Export these dates", self.export_dates), ("Last week", self.export_last_week), ("This week so far", self.export_this_week)):
            b = ttk.Button(now, text=text, command=cmd); b.pack(side="left", padx=3); self._btns.append(b)
        ttk.Label(f, textvariable=self.msg, wraplength=690, justify="left").grid(row=12, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.last_var = tk.StringVar(value=("Last automatic run: " + c["last"]) if c["last"] else "The automatic export has not run yet.")
        ttk.Label(f, textvariable=self.last_var, foreground=MUTED, wraplength=690, justify="left").grid(row=13, column=0, columnspan=4, sticky="w", pady=(4, 0))
        bar = ttk.Frame(f); bar.grid(row=14, column=0, columnspan=4, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="Save settings", style="Accent.TButton", command=lambda: self.save(close=True)).pack(side="right")
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right", padx=8)
        ttk.Button(bar, text="Open the folder", command=self.open_folder).pack(side="right")
        if os.name != "nt":
            self.b_make.state(["disabled"]); self.b_drop.state(["disabled"])
            self.task_msg.set("The Windows schedule is only available on Windows.")
        else:
            self.refresh_task()
        self.grab_set()

    # ---- settings
    def browse(self):
        d = filedialog.askdirectory(parent=self, title="Folder for the weekly PDFs", initialdir=self.folder.get() or None)
        if d: self.folder.set(os.path.normpath(d))

    def open_folder(self, path=None):
        path = path or self.folder.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showinfo("Folder", "Choose the folder first (and save).", parent=self); return
        try:
            if os.name == "nt": os.startfile(path)
            else: messagebox.showinfo("Folder", path, parent=self)
        except Exception as exc:
            messagebox.showerror("Folder", f"{path}\n\n{exc}", parent=self)

    def save(self, close=False):
        try:
            WK.save_config(self.on.get(), self.folder.get(), WK.DAY_NAMES.index(self.wstart.get()), self.basis.get())
            if WK.valid_task_time(self.t_time.get()):
                S.put(WK.K_TASK_DAY, WK.TASK_DAYS[WK.DAY_NAMES.index(self.t_day.get())]); S.put(WK.K_TASK_TIME, self.t_time.get().strip())
        except ValueError as exc:
            messagebox.showerror("Weekly PDF export", str(exc), parent=self); return False
        if self.on.get():
            self.app.weekly_check()                          # a finished week is waiting? start exporting it now, in the background
        if close:
            messagebox.showinfo("Weekly PDF export", "Saved." + (" Every finished week will be exported to the folder automatically; "
                                "if a week is already waiting it is being exported now." if self.on.get() else " Automatic export is switched off."), parent=self)
            self.destroy()
        return True

    # ---- Windows schedule
    def refresh_task(self):
        ok, text = WK.run_schtasks(WK.schtasks_query_args())
        self.task_msg.set("A Windows schedule is set up (it starts the program without a window, exports any finished week, and exits)." if ok
                          else "No Windows schedule yet. Without it, exports happen whenever the program is open.")

    def make_schedule(self):
        if not self.on.get():
            messagebox.showinfo("Schedule", "Tick \u201cExport every finished week\u2019s warrants to PDF automatically\u201d first.", parent=self); return
        if not self.save(): return
        try:
            args = WK.schtasks_create_args(WK.task_command(Path(__file__).resolve()), WK.TASK_DAYS[WK.DAY_NAMES.index(self.t_day.get())], self.t_time.get())
        except ValueError as exc:
            messagebox.showerror("Schedule", str(exc), parent=self); return
        ok, text = WK.run_schtasks(args)
        if ok: messagebox.showinfo("Schedule", f"Done. Windows will run the weekly export every {self.t_day.get()} at {self.t_time.get().strip()}, "
                                   "even when this program is closed (you must be signed in to Windows at that time).", parent=self)
        else: messagebox.showerror("Schedule", f"Windows did not accept the schedule:\n\n{text}", parent=self)
        self.refresh_task()

    def drop_schedule(self):
        ok, text = WK.run_schtasks(WK.schtasks_delete_args())
        messagebox.showinfo("Schedule", "The Windows schedule was removed." if ok else f"Nothing was removed:\n\n{text}", parent=self)
        self.refresh_task()

    # ---- export now
    def export_last_week(self):
        ws = WK.DAY_NAMES.index(self.wstart.get()); a = WK.week_start_of(datetime.now().date(), ws) - timedelta(days=7)
        self._run(a, a + timedelta(days=6))

    def export_this_week(self):
        ws = WK.DAY_NAMES.index(self.wstart.get()); today = datetime.now().date()
        self._run(WK.week_start_of(today, ws), today)

    def export_dates(self):
        try: a, b = WK.parse_date(self.d_from.get()), WK.parse_date(self.d_to.get())
        except ValueError as exc:
            messagebox.showerror("Export now", str(exc), parent=self); return
        self._run(a, b)

    def _run(self, a, b):
        if self._busy: return
        if not ENGINE_OK:
            messagebox.showerror("Missing dependency", "Run python -m pip install -r requirements.txt", parent=self); return
        folder = self.folder.get().strip(); bad = WK.folder_problem(folder)
        if bad:
            messagebox.showerror("Export now", bad, parent=self); return
        self._busy = True; self._prog = (0, 0)
        for b_ in self._btns: b_.state(["disabled"])
        self.msg.set("Working\u2026")
        style, basis = copy.deepcopy(self.app.doc_style), self.basis.get()
        def progress(i, n): self._prog = (i, n)
        run_job(self.app, lambda: WK.run_manual(a, b, folder, style, basis, progress), self._finished,
                tick=lambda: self.msg.set(f"Working\u2026 {self._prog[0]} of {self._prog[1]}") if self.winfo_exists() else None)

    def _finished(self, res, err):
        self._busy = False
        if not self.winfo_exists(): return
        for b_ in self._btns: b_.state(["!disabled"])
        if err is not None:
            self.msg.set(""); messagebox.showerror("Export now", str(err), parent=self); return
        self.msg.set(WK.describe(res))
        bad = WK.problems_text([res])
        if bad:
            messagebox.showwarning("Export now", f"{WK.describe(res)}\n\nThese were NOT exported:\n\n{bad[:1500]}\n\nThe list is also in Export_Summary.txt in the week's folder.", parent=self)
        elif res["files"] and messagebox.askyesno("Export now", f"{WK.describe(res)}\n\nOpen the folder?", parent=self):
            self.open_folder(res["dir"])


class SettingsDialog(tk.Toplevel):
    """Everything configurable, stored in the database: default year, header defaults, Ref prefixes, warrant-number prefixes."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app; self.title("Settings"); self.transient(app); self.resizable(False, False); self.saved = False
        f = ttk.Frame(self, padding=18); f.pack(fill="both", expand=True)
        y = S.default_year()
        self.v = {"year": tk.StringVar(value=str(y))}
        ttk.Label(f, text="Default financial year for new warrants", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
        ttk.Combobox(f, textvariable=self.v["year"], values=[str(x) for x in S.years()], width=10).grid(row=0, column=1, sticky="w", padx=10)
        ttk.Label(f, text="(pick, or type any 4-digit year)", foreground=MUTED).grid(row=0, column=2, sticky="w")
        rows = [("Addressee", "addressee"), ("Office", "office"), ("City", "city"), ("State (full)", "state"),
                ("State (short, used in the authorization text)", "state_short"),
                ("Ref prefix \u2013 Recurrent", "ref_prefix:Recurrent"), ("Ref prefix \u2013 Capital", "ref_prefix:Capital"),
                ("Warrant No. format (pattern; default 8 digits 6/2)", "warrant_no_pattern"), ("Warrant No. example (shown in error)", "warrant_no_example")]
        for i, (lab, key) in enumerate(rows, 1):
            ttk.Label(f, text=lab).grid(row=i, column=0, sticky="w", pady=4)
            self.v[key] = tk.StringVar(value=S.get(key, "")); ttk.Entry(f, textvariable=self.v[key], width=44).grid(row=i, column=1, columnspan=2, sticky="w", padx=10)
        n0 = len(rows) + 1
        ttk.Separator(f).grid(row=n0, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Button(f, text="Change MDA password\u2026", command=lambda: change_password_flow(self)).grid(row=n0 + 1, column=0, sticky="w")
        ttk.Button(f, text="Import payees from a text file\u2026", command=self.import_payees).grid(row=n0 + 1, column=1, columnspan=2, sticky="w", padx=10)
        self.up_var = tk.BooleanVar(value=S.payee_upper_on())
        ttk.Checkbutton(f, text="Write every Beneficiary / Payee in UPPERCASE (typing, suggestions, schedule, printed warrant)", variable=self.up_var).grid(row=n0 + 4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(f, text="Recurrent / Capital code rules\u2026", command=self.code_rules).grid(row=n0 + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        tools = ttk.Frame(f); tools.grid(row=n0 + 3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(tools, text="Backups & restore\u2026", command=self.backups).pack(side="left")
        ttk.Button(tools, text="Export settings\u2026", command=self.export_cfg).pack(side="left", padx=8)
        ttk.Button(tools, text="Import settings\u2026", command=self.import_cfg).pack(side="left")
        ttk.Button(tools, text="About & log\u2026", command=lambda: AboutDialog(self)).pack(side="left", padx=8)
        ttk.Button(tools, text="Saved codes & descriptions\u2026", command=lambda: TemplateManager(self)).pack(side="left")
        ttk.Button(tools, text="Weekly PDF export\u2026", command=lambda: WeeklyExportDialog(self, self.app)).pack(side="left", padx=8)
        bar = ttk.Frame(f); bar.grid(row=n0 + 5, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="Save settings", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        self.grab_set()

    def backups(self):
        BackupDialog(self, self.app)

    def export_cfg(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json", filetypes=[("Warrant System settings", "*.json")],
                                            initialfile=f"WMS_settings_{datetime.now():%Y%m%d}.json", title="Export settings, rules, MDAs and payees")
        if not path: return
        try: n = S.export_settings(path)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc), parent=self); return
        messagebox.showinfo("Exported", f"Saved to {path}\n\n{n['settings']} settings, {n['rules']} code rules, {n['years']} years, {n['payees']} payees, "
                            f"{n['mdas']} MDAs, {n['codes']} economic codes.\n\nThe MDA password and saved warrants are not included.", parent=self)

    def import_cfg(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Warrant System settings", "*.json")], title="Import settings file")
        if not path: return
        try: S.read_export(path)
        except ValueError as exc:
            messagebox.showerror("Import refused", str(exc), parent=self); return
        if not mda_gate(self): return
        if not messagebox.askyesno("Import settings", "Settings and code rules in this file will REPLACE the current ones. Missing years, payees, MDAs and economic codes "
                                   "will be ADDED; nothing is deleted. The MDA password and saved warrants are not touched.\n\nA backup is taken first. Continue?", parent=self):
            return
        try: n = S.import_settings(path)
        except Exception as exc:
            messagebox.showerror("Import failed", f"{exc}\n\nNothing was changed.", parent=self); return
        messagebox.showinfo("Imported", f"{n['settings']} settings, {n['rules']} code rules, {n['years']} years, {n['payees']} new payees, "
                            f"{n['mdas']} new MDAs, {n['codes']} new economic codes, {n.get('templates', 0)} new saved line items.\n\nRestart the program to apply everything.", parent=self)

    def code_rules(self):
        if not mda_gate(self): return
        d = _FormDialog(self, "Economic code rules", [("Digits in the code = warrant type", S.code_rules_text()),
                        ("Also refuse codes of any other length? (yes / no)", "yes" if S.strict_unlisted() else "no")],
                        note="The test is the number of DIGITS in the whole economic code (slashes ignored), written DIGITS=Type, e.g.  18=Recurrent, 30=Capital.  "
                             "9900000001/21010101 has 18 digits (Recurrent); 9900000001/23050104/081001240058 has 30 digits (Capital). "
                             "A code of 18 digits is refused on a Capital warrant and a code of 30 digits on a Recurrent warrant. Other lengths are not checked unless you answer yes below.")
        if not d.result: return
        try:
            S.set_code_rules(d.result[0]); S.set_strict_unlisted(d.result[1].strip().lower() in ("yes", "y", "1", "true"))
        except (ValueError, PermissionError) as exc:
            messagebox.showerror("Economic code rules", str(exc), parent=self); return
        messagebox.showinfo("Economic code rules", "Saved. They apply to new and edited warrants from now on.", parent=self)

    def import_payees(self):
        path = filedialog.askopenfilename(parent=self, title="Choose a text file with one payee per line", filetypes=[("Text files", "*.txt *.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            added, dup = S.import_payees_file(path)
        except Exception as exc:
            messagebox.showerror("Import payees", f"Could not read the file:\n\n{exc}", parent=self); return
        messagebox.showinfo("Import payees", f"{added} new payee(s) added.\n{dup} were already in the list.", parent=self)

    def save(self):
        y = S.parse_year(self.v["year"].get())
        if y is None:
            messagebox.showerror("Year", f"Enter a 4-digit year between {S.YEAR_MIN} and {S.YEAR_MAX}.", parent=self); return
        bad = S.check_wno_settings(self.v["warrant_no_pattern"].get(), self.v["warrant_no_example"].get())
        if bad:
            messagebox.showerror("Warrant No. format", bad, parent=self); return
        S.set_default_year(y)
        for key, var in self.v.items():
            if key != "year":
                S.put(key, var.get().strip())
        S.set_payee_upper(self.up_var.get())
        self.saved = True; self.destroy()


class FormatPanel(ttk.Frame):
    """Live format controls (docked beside the Print Preview). There is no 'Apply' step: 0.35 s after any value
    changes, the whole warrant is re-paginated and the Print Preview redrawn.

    * Page / letterhead / table settings belong to the warrant that is open
      (they are stored inside that warrant when you Save it).
    * Text styling and page/table defaults are shared across both types."""
    BOLD_MODES = [("off", "Not bold"), ("lead", "First word bold"), ("full", "Whole paragraph bold")]
    COL_LABELS = ["No.", "Economic Code", "Beneficiary/Payee", "Description", "Amount"]

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._loading = True
        self._job = None
        self.v = {}
        self.col_vars = []
        outer = ttk.Frame(self); outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0, width=440, background=app.T["bg"]); self.canvas = canvas
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        f = ttk.Frame(canvas, padding=14)
        f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=f, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")

        self.info = tk.StringVar()
        box = ttk.LabelFrame(f, text="Pagination result", padding=8); box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, textvariable=self.info, wraplength=340, justify="left", font=("Segoe UI", 9, "bold")).pack(anchor="w")

        # ---------------- page & letterhead ----------------
        pg = ttk.LabelFrame(f, text="Page and letterhead  (all warrants)", padding=10); pg.pack(fill="x", pady=(0, 10))
        rows = [("Left margin", "margin_left_mm", 0, 60, 1, "mm"), ("Right margin", "margin_right_mm", 0, 60, 1, "mm"),
                ("Extra top margin (above the letterhead space, page 1)", "margin_top_mm", 0, 40, 1, "mm"),
                ("Top margin of pages 2, 3, ... (WARRANT NO on first row)", "cont_top_mm", 5, 40, 1, "mm"),
                ("Letterhead space: blank lines on PAGE 1 (default 10)", "letterhead_lines", 0, 14, 1, "lines"),
                ("Bottom stamping / signing band", "margin_bottom_mm", 16, 70, 1, "mm"),
                ("...of which lines the table (rows and Totals) may use", "bottom_flex_lines", 0, 12, 1, "lines")]
        for r, (lab, key, lo, hi, inc, unit) in enumerate(rows):
            self._spin(pg, r, lab, key, lo, hi, inc, unit)
        lh_all = tk.BooleanVar(); self.v["letterhead_all_pages"] = lh_all; lh_all.trace_add("write", self._changed)
        self._check(pg, len(rows), "Also reserve the letterhead space on pages 2, 3, ... (normally off)", lh_all)
        self.lh_lbl = ttk.Label(pg, text="", foreground=MUTED, wraplength=330, justify="left"); self.lh_lbl.grid(row=len(rows) + 1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # ---------------- schedule table ----------------
        tb = ttk.LabelFrame(f, text="Schedule table  (all warrants; type-specific column widths)", padding=10); tb.pack(fill="x", pady=(0, 10))
        ttk.Label(tb, text="Column widths (pixels as measured in the Print Preview):", wraplength=330, justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        for i, lab in enumerate(self.COL_LABELS):
            ttk.Label(tb, text=lab).grid(row=i + 1, column=0, sticky="w", pady=2)
            var = tk.DoubleVar(); self.col_vars.append(var)
            ttk.Spinbox(tb, from_=10, to=400, increment=5, textvariable=var, width=8).grid(row=i + 1, column=1, sticky="w", padx=8)
            var.trace_add("write", self._changed)
        r = len(self.COL_LABELS) + 1
        self.width_lbl = ttk.Label(tb, text="", font=("Segoe UI", 9, "bold"), wraplength=330, justify="left"); self.width_lbl.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 2))
        ttk.Label(tb, text="Set total table width (px):", wraplength=230, justify="left").grid(row=r + 1, column=0, sticky="w")
        self.total_target = tk.DoubleVar(value=730)
        ttk.Spinbox(tb, from_=300, to=800, increment=10, textvariable=self.total_target, width=8).grid(row=r + 1, column=1, sticky="w", padx=8)
        ttk.Button(tb, text="Apply", command=self.apply_total_width).grid(row=r + 1, column=2, sticky="w")
        more = [("Row height (minimum; 0 = fit the text)", "row_height_mm", 0, 40, 0.5, "mm"),
                ("Row padding (lower = tighter rows)", "row_padding", 0, 20, 0.5, "pt"),
                ("Table font size", "table_size", 6, 18, 0.5, "pt"),
                ("Space between one MDA table and the next", "mda_gap_mm", 0, 30, 0.5, "mm"),
                ("Body text line spacing (added to font size)", "body_line_extra", 0, 12, 0.5, "pt")]
        for j, (lab, key, lo, hi, inc, unit) in enumerate(more):
            self._spin(tb, r + 2 + j, lab, key, lo, hi, inc, unit)
        k = r + 2 + len(more)
        for j, (lab, key) in enumerate([("Schedule lines in BOLD (untick = normal)", "table_bold"),
                                        ("Total / Grand Total rows in BOLD (untick = normal)", "total_bold"),
                                        ("Repeat a column-heading row (No. / Code / Payee / Description / Amount)", "col_headings"),
                                        ("Split MDA: show the MDA's FULL total on its last page (untick = each page totals only its own rows)", "final_total_full")]):
            var = tk.BooleanVar(); self.v[key] = var; var.trace_add("write", self._changed)
            self._check(tb, k + j, lab, var)

        # ---------------- pagination rules ----------------
        pr = ttk.LabelFrame(f, text="MDA pagination  (all warrants)", padding=10); pr.pack(fill="x", pady=(0, 10))
        self._spin(pr, 0, "Minimum rows that must fit under an MDA name at the bottom of a page (else the MDA moves on)", "min_head_rows", 1, 6, 1, "rows")
        self._spin(pr, 1, "Rows that travel with an MDA's Total when it splits (minimum 1)", "min_tail_rows", 1, 6, 1, "rows")
        ttk.Label(pr, text="An MDA is one table block. When it must continue, every row that fits stays on the page and the page ends with that MDA's Total for the rows shown; "
                           "the last page carries the final Total. A Total is never alone (at least 1 row above it) and the MDA name repeats on each page.",
                  foreground=MUTED, wraplength=330, justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(f, text="Reset page & table layout to standard", command=self.reset_layout).pack(anchor="w", pady=(0, 10))

        # ---------------- text styling per type ----------------
        ttk.Separator(f).pack(fill="x", pady=6)
        top = ttk.Frame(f); top.pack(fill="x", pady=(4, 10))
        ttk.Label(top, text="Text styling for both warrant types:", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.type_var = tk.StringVar(value=app.warrant_type.get())
        ttk.Combobox(top, textvariable=self.type_var, values=S.warrant_types(), state="readonly", width=16).pack(side="left", padx=8)
        self.type_var.trace_add("write", lambda *a: self.load_text_fields())
        sizes = ttk.LabelFrame(f, text="Font sizes (points)", padding=10); sizes.pack(fill="x", pady=(0, 10))
        self.title_size = tk.DoubleVar(); self.body_size = tk.DoubleVar()
        for i, (lab, var) in enumerate([("Title", self.title_size), ("Body text (Ref/Recipient/Authorization/Provided/And)", self.body_size)]):
            ttk.Label(sizes, text=lab).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Spinbox(sizes, from_=7, to=18, increment=0.5, textvariable=var, width=6).grid(row=i, column=1, sticky="w", padx=8)
            var.trace_add("write", self._changed)
        bolds = ttk.LabelFrame(f, text="Bold / normal", padding=10); bolds.pack(fill="x", pady=(0, 10))
        self.title_bold = tk.BooleanVar(); self.ref_bold = tk.BooleanVar(); self.recipient_bold = tk.BooleanVar(); self.mda_bold = tk.BooleanVar()
        for i, (lab, var) in enumerate([("Title", self.title_bold), ("Ref line", self.ref_bold),
                                        ("Recipient block (Accountant-General/Office/City/State)", self.recipient_bold), ("MDA names", self.mda_bold)]):
            ttk.Checkbutton(bolds, text=lab, variable=var).grid(row=i, column=0, sticky="w", pady=2); var.trace_add("write", self._changed)
        modes = ttk.LabelFrame(f, text="Paragraph bolding", padding=10); modes.pack(fill="x", pady=(0, 10))
        self.auth_mode = tk.StringVar(); self.prov_mode = tk.StringVar(); self.and_mode = tk.StringVar()
        for i, (lab, var) in enumerate([("Authorization (\u201cYou are hereby...\u201d)", self.auth_mode), ("PROVIDED paragraph", self.prov_mode), ("AND paragraph", self.and_mode)]):
            ttk.Label(modes, text=lab).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Combobox(modes, textvariable=var, values=[m[1] for m in self.BOLD_MODES], state="readonly", width=22).grid(row=i, column=1, sticky="w", padx=8)
            var.trace_add("write", self._changed)
        # ---------------- fonts: one font (and optional size) for each part of the warrant ----------------
        ff = ttk.LabelFrame(f, text="Fonts \u2013 one for each part, the SAME on every warrant (Recurrent and Capital)", padding=10); ff.pack(fill="x", pady=(0, 10))
        self.font_choices = E.available_fonts()
        size_choices = ["Auto"] + [("%g" % (x / 2)) for x in range(14, 49)]
        ttk.Label(ff, text="Part", foreground=MUTED).grid(row=0, column=0, sticky="w"); ttk.Label(ff, text="Font", foreground=MUTED).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(ff, text="Size (pt)", foreground=MUTED).grid(row=0, column=2, sticky="w")
        self.font_vars, self.fsize_vars = {}, {}
        for i, (role, lab) in enumerate(E.FONT_ROLES, 1):
            ttk.Label(ff, text=lab, wraplength=150, justify="left").grid(row=i, column=0, sticky="w", pady=2)
            fv = tk.StringVar(); sv = tk.StringVar(value="Auto"); self.font_vars[role] = fv; self.fsize_vars[role] = sv
            ttk.Combobox(ff, textvariable=fv, values=self.font_choices, state="readonly", width=17).grid(row=i, column=1, sticky="w", padx=8, pady=2)
            ttk.Combobox(ff, textvariable=sv, values=size_choices, width=6).grid(row=i, column=2, sticky="w", pady=2)
            fv.trace_add("write", self._changed); sv.trace_add("write", self._changed)
        n = len(E.FONT_ROLES) + 1
        allrow = ttk.Frame(ff); allrow.grid(row=n, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.all_font = tk.StringVar(value=E.DEFAULT_FONT)
        ttk.Label(allrow, text="Use one font for every part:").pack(side="left")
        ttk.Combobox(allrow, textvariable=self.all_font, values=self.font_choices, state="readonly", width=17).pack(side="left", padx=6)
        ttk.Button(allrow, text="Apply to all", command=self.apply_font_to_all).pack(side="left")
        ttk.Label(ff, text="Text sizes, bold choices, paragraph styles and fonts apply to both Capital and Recurrent warrants. \u201cAuto\u201d follows the Title / Body / Table sizes above. Only fonts installed on this PC (plus the built-in look-alikes of "
                           "Times New Roman, Calibri, Arial and Courier New) are listed.", foreground=MUTED, wraplength=330, justify="left").grid(row=n + 1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        btns = ttk.Frame(f); btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Reset text styling to defaults", command=self.reset_text).pack(side="left")
        ttk.Button(btns, text="Hide panel", command=lambda: self.app.toggle_format_panel(False)).pack(side="right")
        self.refresh()

    # ---- building helpers ----
    def _check(self, parent, row, text, var):
        """Tick box with a label that wraps inside the narrow side panel."""
        cb = ttk.Checkbutton(parent, variable=var); cb.grid(row=row, column=0, sticky="nw", pady=2)
        lb = ttk.Label(parent, text=text, wraplength=290, justify="left"); lb.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
        lb.bind("<Button-1>", lambda e: var.set(not var.get()))

    def _spin(self, parent, row, label, key, lo, hi, inc, unit=""):
        ttk.Label(parent, text=label, wraplength=230, justify="left").grid(row=row, column=0, sticky="w", pady=2)
        base = E.LAYOUT_COMMON[key]
        var = tk.IntVar() if (isinstance(base, int) and not isinstance(base, bool)) else tk.DoubleVar()
        ttk.Spinbox(parent, from_=lo, to=hi, increment=inc, textvariable=var, width=8).grid(row=row, column=1, sticky="w", padx=8)
        ttk.Label(parent, text=unit, foreground=MUTED).grid(row=row, column=2, sticky="w")
        var.trace_add("write", self._changed)
        self.v[key] = var

    # ---- moving values between the dialog and the app ----
    def refresh(self):
        """(Re)load every field from the app's current warrant."""
        self._loading = True
        L = self.app.table_fmt
        for key, var in self.v.items():
            var.set(L[key])
        for var, w in zip(self.col_vars, L["col_px"]):
            var.set(w)
        self.type_var.set(self.app.warrant_type.get())
        self.load_text_fields()
        self._loading = False
        self._show_info(L)

    def _collect(self):
        L = {}
        try:
            for key, var in self.v.items():
                L[key] = var.get()
            L["col_px"] = [v.get() for v in self.col_vars]
        except tk.TclError:
            return None                      # a box is half-typed; wait for the next change
        return E.norm_layout(self.app.warrant_type.get(), L)

    def _mode_label(self, key): return next(lbl for k, lbl in self.BOLD_MODES if k == key)
    def _mode_key(self, label): return next(k for k, lbl in self.BOLD_MODES if lbl == label)

    def load_text_fields(self):
        was = self._loading; self._loading = True
        s = self.app.doc_style[self.type_var.get()]
        self.title_size.set(s["title_size"]); self.body_size.set(s["body_size"])
        self.title_bold.set(s["title_bold"]); self.ref_bold.set(s["ref_bold"]); self.recipient_bold.set(s["recipient_bold"]); self.mda_bold.set(s["mda_bold"])
        self.auth_mode.set(self._mode_label(s["authorization_bold"])); self.prov_mode.set(self._mode_label(s["provided_bold"])); self.and_mode.set(self._mode_label(s["and_bold"]))
        for role, _lab in E.FONT_ROLES:
            fam = s.get(f"font_{role}") or E.DEFAULT_FONT
            self.font_vars[role].set(fam if fam in self.font_choices else E.DEFAULT_FONT)
            sz = float(s.get(f"size_{role}", 0) or 0)
            self.fsize_vars[role].set("Auto" if sz < 5 else ("%g" % sz))
        self._loading = was

    def _sync_text_style(self):
        s = self.app.doc_style[self.type_var.get()]
        try:
            s["title_size"] = float(self.title_size.get()); s["body_size"] = float(self.body_size.get())
        except tk.TclError:
            return False
        s["title_bold"] = bool(self.title_bold.get()); s["ref_bold"] = bool(self.ref_bold.get())
        s["recipient_bold"] = bool(self.recipient_bold.get()); s["mda_bold"] = bool(self.mda_bold.get())
        for role, _lab in E.FONT_ROLES:
            fam = self.font_vars[role].get()
            if fam in self.font_choices: s[f"font_{role}"] = fam; E.share_fonts(self.app.doc_style, self.type_var.get())
            raw = self.fsize_vars[role].get().strip()
            try: s[f"size_{role}"] = 0.0 if raw.lower() in ("", "auto") else min(max(float(raw.replace(",", ".")), 5.0), 40.0)
            except ValueError: pass                               # half-typed size: keep the previous one until it is a number
        try:
            s["authorization_bold"] = self._mode_key(self.auth_mode.get()); s["provided_bold"] = self._mode_key(self.prov_mode.get()); s["and_bold"] = self._mode_key(self.and_mode.get())
        except StopIteration:
            pass
        # Text-style choices in this panel apply to both warrant types.
        E.share_text_style(self.app.doc_style, self.type_var.get())
        return True

    def _show_info(self, L):
        t = sum(L["col_px"]); w = t * E.PX_MM
        printable = E.PAGE_W_MM - L["margin_left_mm"] - L["margin_right_mm"]
        warn = "   \u26a0 WIDER THAN THE PAPER" if w > E.PAGE_W_MM - 4 else ""
        self.width_lbl.config(text=f"Table width: {t:.0f} px  =  {w:.0f} mm   (writing area between margins: {printable:.0f} mm; table is centred){warn}",
                              foreground=ERR if warn else INK)
        self.lh_lbl.config(text=f"Letterhead space = {L['letterhead_lines']} lines \u00d7 {E.LINE_MM:.2f} mm = {L['letterhead_lines'] * E.LINE_MM:.1f} mm "
                                f"(plus {L['margin_top_mm']:.0f} mm extra top margin). " + ("Applied to every page." if L["letterhead_all_pages"] else "Page 1 only; pages 2+ start at their own top margin with WARRANT NO on the first row."))
        self.info.set(E.describe_summary(self.app.summary) if self.app.summary else "")

    # ---- live apply ----
    def _changed(self, *a):
        if self._loading:
            return
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(350, self._apply)

    def _apply(self):
        self._job = None
        if not self.winfo_exists():
            return
        L = self._collect()
        if L is None or not self._sync_text_style():
            return
        if L != self.app.table_fmt:
            self.app.layout_defaults = E.share_layout_settings(self.app.layout_defaults, self.app.warrant_type.get(), L)
            save_layout_settings(self.app.layout_defaults)
            self.app.table_fmt = self.app.layout_defaults[self.app.warrant_type.get()]
            self.app.table_fmt_custom = True
        save_style(self.app.doc_style)
        self.app.on_format_changed()
        self._show_info(L)

    def apply_total_width(self):
        try:
            cur = sum(v.get() for v in self.col_vars); target = float(self.total_target.get())
        except tk.TclError:
            return
        if cur <= 0 or target <= 0:
            return
        k = target / cur
        for v in self.col_vars:
            v.set(round(v.get() * k * 2) / 2.0)

    def reset_layout(self):
        self.app.layout_defaults = {t: E.default_layout(t) for t in self.app.layout_defaults}
        save_layout_settings(self.app.layout_defaults)
        self.app.table_fmt = self.app.layout_defaults[self.app.warrant_type.get()]; self.app.table_fmt_custom = False
        self.refresh(); self.app.on_format_changed(); self._show_info(self.app.table_fmt)

    def apply_font_to_all(self):
        fam = self.all_font.get()
        if fam not in self.font_choices: return
        self._loading = True
        for role, _lab in E.FONT_ROLES: self.font_vars[role].set(fam)
        self._loading = False
        self._changed()

    def reset_text(self):
        t = self.type_var.get()
        fonts = {k: v for k, v in self.app.doc_style[t].items() if k.startswith("font_")}      # fonts are shared by all warrants: reset keeps them
        reset = dict(E.DEFAULT_STYLE[t]); reset.update(fonts)
        self.app.doc_style[t] = reset
        E.share_text_style(self.app.doc_style, t)
        save_style(self.app.doc_style); self.load_text_fields()
        self.app.on_format_changed(); self._show_info(self.app.table_fmt)


class PrintDialog(tk.Toplevel):
    """Print straight to a printer: choose printer, copies and pages, press Print.
    The pages sent are exactly the ones the Print Preview shows."""

    def __init__(self, app, pdf_path, n_pages, title):
        super().__init__(app)
        self.app, self.pdf_path, self.n_pages, self.doc_title = app, pdf_path, n_pages, title
        self.title("Print Warrant")
        self.resizable(False, False)
        self.transient(app)
        f = ttk.Frame(self, padding=16); f.pack(fill="both", expand=True)
        try:
            names = PRT.list_printers()
        except Exception as exc:
            names = []
            messagebox.showerror("Printers", f"Could not list the printers:\n\n{exc}", parent=self)
        saved = load_print_settings()
        pick = saved.get("printer") if saved.get("printer") in names else (PRT.default_printer() if PRT.default_printer() in names else (names[0] if names else ""))
        self.printer = tk.StringVar(value=pick)
        self.copies = tk.IntVar(value=1)
        self.mode = tk.StringVar(value="all")
        self.range_text = tk.StringVar(value=f"1-{n_pages}" if n_pages > 1 else "1")
        ttk.Label(f, text=f"Warrant No. {title}   \u2013   {n_pages} page{'s' if n_pages != 1 else ''} (A4)", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(f, text="Printer").grid(row=1, column=0, sticky="w", pady=4)
        self.cb = ttk.Combobox(f, textvariable=self.printer, values=names, state="readonly", width=46); self.cb.grid(row=1, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Label(f, text="Copies").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=1, to=99, textvariable=self.copies, width=6).grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(f, text="Pages").grid(row=3, column=0, sticky="nw", pady=4)
        pf = ttk.Frame(f); pf.grid(row=3, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Radiobutton(pf, text="All pages", variable=self.mode, value="all").pack(anchor="w")
        rr = ttk.Frame(pf); rr.pack(anchor="w")
        ttk.Radiobutton(rr, text="Pages:", variable=self.mode, value="range").pack(side="left")
        e = ttk.Entry(rr, textvariable=self.range_text, width=14); e.pack(side="left", padx=6)
        e.bind("<FocusIn>", lambda ev: self.mode.set("range"))
        ttk.Label(rr, text="e.g. 1-3, 5", foreground=MUTED).pack(side="left")
        self.msg = tk.StringVar(value="" if names else "No printer found. Install/connect a printer, or use Export PDF.")
        ttk.Label(f, textvariable=self.msg, foreground=ERR, wraplength=430, justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        bar = ttk.Frame(f); bar.grid(row=5, column=0, columnspan=3, sticky="e", pady=(14, 0))
        self.btn = ttk.Button(bar, text="Print", command=self.do_print, state="normal" if names else "disabled"); self.btn.pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Return>", lambda ev: self.do_print()); self.bind("<Escape>", lambda ev: self.destroy())
        self.grab_set(); self.btn.focus_set()

    def do_print(self):
        try:
            pages = PRT.parse_pages("all" if self.mode.get() == "all" else self.range_text.get(), self.n_pages)
            copies = int(self.copies.get())
            if not 1 <= copies <= 99:
                raise ValueError("Copies must be between 1 and 99.")
        except (ValueError, tk.TclError) as exc:
            self.msg.set(str(exc) if isinstance(exc, ValueError) else "Copies must be a number."); return
        name = self.printer.get()
        if not name:
            self.msg.set("Choose a printer."); return
        self.btn.config(state="disabled"); self.msg.set("Sending to the printer\u2026"); self.update_idletasks()
        try:
            sheets = PRT.print_pdf(self.pdf_path, name, pages, copies, title=f"Warrant {self.doc_title}")
        except Exception as exc:
            self.btn.config(state="normal"); self.msg.set(f"Printing failed: {exc}\n\nYou can still use Export PDF and print the file.")
            return
        save_print_settings({"printer": name})
        self.app.status.set(f"Sent to {name}: {sheets} sheet(s)")
        self.destroy()
        messagebox.showinfo("Print", f"Sent {sheets} sheet(s) to {name}.\nCheck the printer queue if nothing comes out.")


class SavedWarrantsPanel(ttk.Frame):
    """Permanent record of every saved warrant: search by Warrant No. (or MDA / date),
    open it, edit it, update it (same record), reprint it."""

    def __init__(self, parent, app, initial_no=""):
        super().__init__(parent)
        self.app = app
        f = ttk.Frame(self, padding=12); f.pack(fill="both", expand=True)
        ttk.Label(f, text="Every warrant you Save is stored permanently. Type a Warrant No. to find it, then open it to view, edit, "
                          "update (Save replaces the same record \u2013 no new warrant is created) or reprint.",
                  foreground=MUTED, wraplength=1000, justify="left").pack(anchor="w", pady=(0, 8))
        sf = ttk.Frame(f); sf.pack(fill="x", pady=(0, 8))
        self.q_no = tk.StringVar(value=initial_no); self.q_mda = tk.StringVar(); self.q_date = tk.StringVar(); self.q_year = tk.StringVar(value="All")
        first = None
        for lab, var, w_ in [("Warrant No.", self.q_no, 22), ("MDA", self.q_mda, 30), ("Date", self.q_date, 14)]:
            ttk.Label(sf, text=lab, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 4))
            e = ttk.Entry(sf, textvariable=var, width=w_); e.pack(side="left", padx=(0, 14)); e.bind("<Return>", lambda ev: self.open_selected())
            first = first or e
        self.first = first
        ttk.Label(sf, text="Year", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 4))
        self.year_cb = ttk.Combobox(sf, textvariable=self.q_year, values=["All"] + [str(y) for y in S.years()], state="readonly", width=7); self.year_cb.pack(side="left", padx=(0, 14))
        ttk.Button(sf, text="Clear", command=self.clear).pack(side="left")
        self.count = tk.StringVar(); ttk.Label(sf, textvariable=self.count, foreground=MUTED).pack(side="right")
        cols = [("no", "Warrant No.", 150), ("type", "Type", 90), ("year", "Year", 60), ("date", "Date", 100), ("mdas", "MDA(s)", 470), ("total", "Grand Total", 150), ("saved", "Last saved", 160)]
        self.t = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings")
        for c, h, wd in cols:
            self.t.heading(c, text=h); self.t.column(c, width=wd, anchor="e" if c == "total" else "w")
        self.t.pack(fill="both", expand=True)
        self.t.bind("<Double-1>", lambda e: self.open_selected()); self.t.bind("<Return>", lambda e: self.open_selected())
        bar = ttk.Frame(f); bar.pack(fill="x", pady=8)
        ttk.Button(bar, text="Refresh", command=self.load).pack(side="right")
        ttk.Button(bar, text="Open and Reprint", command=lambda: self.open_selected(reprint=True)).pack(side="right", padx=8)
        ttk.Button(bar, text="Open (view / edit / update)", command=self.open_selected).pack(side="right")
        self._deb = None
        for v in (self.q_no, self.q_mda, self.q_date, self.q_year):
            v.trace_add("write", lambda *a: self._soon())
        self.load()

    def _soon(self):
        """Typing in the search boxes searches once, shortly after the last key, not on every keystroke."""
        if self._deb is not None:
            self.after_cancel(self._deb)
        self._deb = self.after(220, self._run_load)

    def _run_load(self):
        self._deb = None
        self.load()

    def focus_search(self, warrant_no=None):
        if warrant_no is not None:
            self.q_no.set(warrant_no)
        self.load(); self.first.focus_set(); self.first.select_range(0, "end")

    def clear(self):
        self.q_no.set(""); self.q_mda.set(""); self.q_date.set(""); self.q_year.set("All")

    def load(self):
        for x in self.t.get_children():
            self.t.delete(x)
        self.year_cb["values"] = ["All"] + [str(y) for y in S.years()]
        try:
            found = S.search_warrants(self.q_no.get(), self.q_mda.get(), self.q_date.get(), self.q_year.get())
        except Exception as exc:
            self.count.set(f"Search failed: {exc}"); return
        for r in found:
            no = r["warrant_no"] if not r["original_warrant_no"] or r["original_warrant_no"] == r["warrant_no"] else f"{r['warrant_no']}  (was {r['original_warrant_no']})"
            self.t.insert("", "end", iid=str(r["id"]), values=(no, r["type"], r["year"] or "", r["date"], r["mdas"], f"\u20a6{r['total']:,.2f}", r["saved"][:19].replace("T", " ")))
        self.count.set(f"{len(found)} warrant(s) found")
        kids = self.t.get_children()
        if kids:
            self.t.selection_set(kids[0]); self.t.focus(kids[0])

    def open_selected(self, reprint=False):
        s = self.t.selection()
        if not s:
            return
        if self.app.open_warrant(int(s[0])):
            if reprint:
                self.app.print_warrant()

class CodePicker(tk.Toplevel):
    def __init__(self,parent,org_code="",value=""):
        super().__init__(parent); self.title("Economic Code Search"); self.geometry("980x560"); self.transient(parent); self.grab_set(); self.result=None; self.org_code=org_code; self.q=tk.StringVar(value=value)
        f=ttk.Frame(self,padding=14); f.pack(fill="both",expand=True); title="Search economic code or description" + (f" — restricted to MDA code {org_code}" if org_code else "")
        ttk.Label(f,text=title,font=("Segoe UI",10,"bold")).pack(anchor="w"); e=ttk.Entry(f,textvariable=self.q); e.pack(fill="x",pady=7); e.bind("<KeyRelease>",self.refresh)
        self.t=ttk.Treeview(f,columns=("code","description"),show="headings"); self.t.heading("code",text="Economic Code"); self.t.heading("description",text="Economic Line Item Description"); self.t.column("code",width=250); self.t.column("description",width=680); self.t.pack(fill="both",expand=True); self.t.bind("<Double-1>",lambda e:self.choose())
        ttk.Button(f,text="Use Selected Code",command=self.choose).pack(anchor="e",pady=8); e.focus_set(); self.refresh()
    def refresh(self,e=None):
        for x in self.t.get_children():self.t.delete(x)
        for r in search_codes(self.q.get(),self.org_code):self.t.insert("","end",values=(r["code"],r["description"]))
    def choose(self):
        s=self.t.selection()
        if s:self.result=self.t.item(s[0],"values")[0]; self.destroy()

class PayeeEntry(ttk.Frame):
    """Type-ahead box for the Beneficiary / Payee. Suggestions come from memory (never from disk while typing), are capped,
    and are refreshed ~120 ms after the last keystroke, so typing stays instant even with thousands of remembered names.
    Down/Up move in the list, Enter or a click picks, Esc closes; any new name is remembered when the line is saved."""
    DELAY_MS = 120
    ROWS = 8

    def __init__(self, parent, textvariable, width=59):
        super().__init__(parent)
        self.var = textvariable; self._job = None; self._pop = None; self._lb = None
        self.entry = ttk.Entry(self, textvariable=self.var, width=width); self.entry.pack(side="left", fill="x", expand=True)
        ttk.Button(self, text="\u25be", width=2, command=self.toggle).pack(side="left", padx=(2, 0))
        self.entry.bind("<KeyRelease>", self._key); self.entry.bind("<Down>", self._down); self.entry.bind("<Up>", self._up)
        self.entry.bind("<Escape>", self._esc); self.entry.bind("<Return>", self._enter)
        self.entry.bind("<FocusOut>", lambda e: self.after(150, self._hide_if_unfocused)); self.bind("<Destroy>", lambda e: self._hide())
        self.entry.bind("<Delete>", self._delete_key)                  # Delete on a highlighted suggestion = forget that payee
        self._casing = False; self.var.trace_add("write", self._capitals)

    def _capitals(self, *a):
        """'UPPERCASE payees' option: whatever is typed or pasted turns into capitals at once."""
        if self._casing or not S.payee_upper_on():
            return
        v = self.var.get(); u = v.upper()
        if v != u:
            self._casing = True
            try: self.var.set(u)
            finally: self._casing = False

    def _delete_key(self, e):
        if self._lb is not None and self._lb.curselection():
            self.forget_selected(); return "break"

    def forget_selected(self):
        cur = self._lb.curselection() if self._lb is not None else ()
        if not cur: return
        name = self._lb.get(cur[0])
        if forget_payee_dialog(self.winfo_toplevel(), name):
            self._refresh()

    def _show(self, names):
        if not names:
            self._hide(); return
        if self._pop is None:
            self._pop = tk.Toplevel(self); self._pop.wm_overrideredirect(True); self._pop.attributes("-topmost", True)
            self._lb = tk.Listbox(self._pop, height=min(len(names), self.ROWS), activestyle="dotbox", exportselection=False, font=("Segoe UI", 10))
            sb = ttk.Scrollbar(self._pop, orient="vertical", command=self._lb.yview); self._lb.configure(yscrollcommand=sb.set)
            self._lb.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
            self._lb.bind("<ButtonRelease-1>", lambda e: self._choose()); self._lb.bind("<Return>", self._enter)
            self._lb.bind("<Delete>", lambda e: (self.forget_selected(), "break")[1])
        self._lb.delete(0, "end"); self._lb.insert("end", *names); self._lb.configure(height=min(len(names), self.ROWS))
        self.update_idletasks()
        self._pop.geometry(f"{max(self.entry.winfo_width(), 380)}x{self._lb.winfo_reqheight()}+{self.entry.winfo_rootx()}+{self.entry.winfo_rooty() + self.entry.winfo_height()}")

    def _hide(self):
        if self._job:
            try: self.after_cancel(self._job)
            except Exception: pass
            self._job = None
        if self._pop is not None:
            try: self._pop.destroy()
            except Exception: pass
            self._pop = None; self._lb = None

    def _hide_if_unfocused(self):
        try:
            if self.focus_get() is not self._lb: self._hide()
        except Exception: self._hide()

    def toggle(self):
        if self._pop is not None: self._hide()
        else:
            self.entry.focus_set(); self._show(S.payee_suggest(self.var.get()))

    def _key(self, e):
        if e.keysym in ("Down", "Up", "Return", "Escape", "Tab", "Shift_L", "Shift_R", "Control_L", "Control_R", "Left", "Right"): return
        if self._job: self.after_cancel(self._job)
        self._job = self.after(self.DELAY_MS, self._refresh)

    def _refresh(self):
        self._job = None
        self._show(S.payee_suggest(self.var.get()))

    def _move(self, step):
        if self._lb is None or not self._lb.size(): return
        cur = self._lb.curselection(); i = (cur[0] + step) if cur else (0 if step > 0 else 0)
        i = min(max(i, 0), self._lb.size() - 1)
        self._lb.selection_clear(0, "end"); self._lb.selection_set(i); self._lb.see(i)

    def _down(self, e):
        if self._pop is None: self._show(S.payee_suggest(self.var.get()))
        self._move(1); return "break"

    def _up(self, e):
        self._move(-1); return "break"

    def _esc(self, e):
        if self._pop is not None: self._hide(); return "break"

    def _enter(self, e):
        if self._lb is not None and self._lb.curselection():
            self._choose(); return "break"
        self._hide()

    def _choose(self):
        cur = self._lb.curselection() if self._lb is not None else ()
        if cur: self.var.set(self._lb.get(cur[0]))
        self._hide(); self.entry.focus_set(); self.entry.icursor("end")


class ItemDialog(tk.Toplevel):
    def __init__(self,parent,org_code,item=None,warrant_type=""):
        super().__init__(parent); self.title("MDA Schedule Line"); self.geometry("760x430"); self.transient(parent); self.grab_set(); self.result=None; item=item or {}; self.org_code=org_code; self.warrant_type=warrant_type
        self.code=tk.StringVar(value=item.get("code","")); self.payee=tk.StringVar(value=item.get("payee","")); self.description=tk.StringVar(value=item.get("description","")); self.amount=tk.StringVar(value=str(item.get("amount","")))
        f=ttk.Frame(self,padding=20); f.pack(fill="both",expand=True)
        ttk.Label(f,text="Economic Code",font=("Segoe UI",10,"bold")).grid(row=0,column=0,sticky="w",pady=9); ttk.Entry(f,textvariable=self.code,width=62).grid(row=0,column=1,pady=9); ttk.Button(f,text="Search Code",command=self.pick).grid(row=0,column=2,padx=8)
        ttk.Label(f,text="Beneficiary / Payee",font=("Segoe UI",10,"bold")).grid(row=1,column=0,sticky="w",pady=9)
        self.payee_box=PayeeEntry(f,self.payee,width=59); self.payee_box.grid(row=1,column=1,columnspan=2,pady=9,sticky="w")
        ttk.Button(f,text="Forget This Payee",command=self.forget_current_payee).grid(row=1,column=3,padx=8,sticky="w")
        ttk.Label(f,text="Description",font=("Segoe UI",10,"bold")).grid(row=2,column=0,sticky="w",pady=9); ttk.Entry(f,textvariable=self.description,width=62).grid(row=2,column=1,pady=9); ttk.Button(f,text="Lookup",command=self.lookup).grid(row=2,column=2,padx=8)
        ttk.Label(f,text="Amount (\u20a6)",font=("Segoe UI",10,"bold")).grid(row=3,column=0,sticky="w",pady=9); ttk.Entry(f,textvariable=self.amount,width=30).grid(row=3,column=1,sticky="w",pady=9)
        rule = S.code_rules_text()
        note = f"This line must belong to MDA code {org_code}" + (f" and to a {warrant_type} warrant (economic code length: {rule})" if warrant_type and rule else "")
        ttk.Label(f,text=note,foreground=MUTED,wraplength=560,justify="left").grid(row=4,column=1,columnspan=2,sticky="w",pady=5); ttk.Button(f,text="Save Line",command=self.save).grid(row=5,column=2,sticky="e",pady=18)
    def forget_current_payee(self):
        if forget_payee_dialog(self, self.payee.get()):
            self.payee.set("")
    def pick(self):
        w=CodePicker(self,self.org_code,self.code.get()); self.wait_window(w)
        if w.result:self.code.set(w.result); self.lookup()
    def lookup(self):
        d=desc(self.code.get())
        if d:self.description.set(d)
    def save(self):
        code=self.code.get().strip()
        if not code_belongs_to(code,self.org_code):messagebox.showerror("Wrong MDA","The selected economic code does not belong to this MDA.",parent=self); return
        if not desc(code):messagebox.showerror("Unknown code",f"Economic code {code} is not in the database. Use Search Code to pick an existing one.",parent=self); return
        if self.warrant_type:
            msg=S.check_code_type(code,self.warrant_type)
            if msg:messagebox.showerror("Wrong warrant type",msg,parent=self); return
        payee=S.canonical_payee(self.payee.get())
        if not payee:messagebox.showerror("Missing payee","Enter the beneficiary / payee.",parent=self); return
        try:a=S.parse_amount(self.amount.get())
        except S.TemplateError as exc:messagebox.showerror("Invalid amount",str(exc),parent=self); return
        self.result={"code":code,"payee":payee,"description":self.description.get().strip() or desc(code),"amount":a}; self.destroy()

class TemplateForm(tk.Toplevel):
    """Create or edit one saved line item (MDA + economic code + description). .result = the stored row, or None."""
    def __init__(self, parent, org_code=None, template=None, defaults=None):
        super().__init__(parent); self.title("Edit saved item" if template else "New saved item"); self.transient(parent); self.resizable(False, False)
        self.result = None; self.template = template; d = defaults or {}
        t = template or {}
        self.orgs = [(r["organisation_code"], r["organisation_name"]) for r in organisations("")]
        self.org_label = {c_: f"{n_}  [{c_}]" for c_, n_ in self.orgs}
        self.org = tk.StringVar(); self.name = tk.StringVar(value=t.get("name", d.get("name", "")))
        self.code = tk.StringVar(value=t.get("economic_code", d.get("code", ""))); self.desc = tk.StringVar(value=t.get("description", d.get("description", "")))
        self.kind = tk.StringVar()
        f = ttk.Frame(self, padding=16); f.pack(fill="both", expand=True)
        ttk.Label(f, text="MDA", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=6)
        fixed = org_code or t.get("org_code")
        if fixed:
            self.org_code = fixed
            ttk.Label(f, text=self.org_label.get(fixed, fixed), wraplength=520, justify="left").grid(row=0, column=1, columnspan=2, sticky="w", pady=6)
        else:
            self.org_code = None
            self.org_cb = ttk.Combobox(f, textvariable=self.org, values=[self.org_label[c_] for c_, _ in self.orgs], width=70, state="readonly")
            self.org_cb.grid(row=0, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Label(f, text="Name", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(f, textvariable=self.name, width=62).grid(row=1, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Label(f, text="Economic Code", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(f, textvariable=self.code, width=48).grid(row=2, column=1, sticky="w", pady=6); ttk.Button(f, text="Search Code", command=self.pick).grid(row=2, column=2, padx=8)
        ttk.Label(f, text="Description", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(f, textvariable=self.desc, width=48).grid(row=3, column=1, sticky="w", pady=6); ttk.Button(f, text="Lookup", command=self.lookup).grid(row=3, column=2, padx=8)
        ttk.Label(f, textvariable=self.kind, foreground=MUTED).grid(row=4, column=1, columnspan=2, sticky="w")
        ttk.Label(f, text="Name it the way you will look for it, for example \u201cPV \u2013 Common Services\u201d. When the item is chosen under \u201cAdd lines\u201d, the code and "
                          "description fill in by themselves; you only type the payee and the amount.", foreground=MUTED, wraplength=560, justify="left").grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        bar = ttk.Frame(f); bar.grid(row=6, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0)); ttk.Button(bar, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        self.code.trace_add("write", lambda *a: self._kind()); self._kind(); self.grab_set()

    def _org(self):
        if self.org_code: return self.org_code
        lab = self.org.get()
        return next((c_ for c_, l_ in self.org_label.items() if l_ == lab), "")

    def _kind(self):
        k = S.template_type(self.code.get()) if self.code.get().strip() else None
        self.kind.set(f"This is a {k} economic code (use it on {k} warrants only)." if k else "")

    def pick(self):
        org = self._org()
        if not org:
            messagebox.showinfo("Saved item", "Choose the MDA first.", parent=self); return
        w = CodePicker(self, org, self.code.get()); self.wait_window(w)
        if w.result:
            self.code.set(w.result)
            if not self.desc.get().strip(): self.lookup()

    def lookup(self):
        d = desc(self.code.get())
        if d: self.desc.set(d)

    def save(self):
        try:
            self.result = S.save_template(self._org(), self.name.get(), self.code.get(), self.desc.get(), self.template["id"] if self.template else None)
        except S.TemplateError as exc:
            messagebox.showerror("Saved item not saved", str(exc), parent=self); return
        except Exception as exc:
            messagebox.showerror("Saved item not saved", f"{exc}\n\nNothing was changed.", parent=self); return
        self.destroy()


class TemplateManager(tk.Toplevel):
    """List of saved line items with New / Edit / Delete. Opened for one MDA (from a warrant) or for all MDAs (from Settings)."""
    def __init__(self, parent, org_code=None, warrant_type=""):
        super().__init__(parent); self.title("Saved codes & descriptions"); self.transient(parent); self.geometry("1020x560"); self.changed = False
        self.warrant_type = warrant_type
        f = ttk.Frame(self, padding=14); f.pack(fill="both", expand=True)
        ttk.Label(f, text="Saved line items (MDA + economic code + description). They stay in the database and are offered on every future warrant.", foreground=MUTED).pack(anchor="w")
        top = ttk.Frame(f); top.pack(fill="x", pady=8)
        ttk.Label(top, text="MDA:").pack(side="left")
        self.orgs = {"All MDAs": None}
        for c_, n_ in S.template_org_codes(): self.orgs[f"{n_}  [{c_}]"] = c_
        if org_code and org_code not in self.orgs.values():
            nm = next((n_ for c_, n_ in [(r["organisation_code"], r["organisation_name"]) for r in organisations("")] if c_ == org_code), org_code); self.orgs[f"{nm}  [{org_code}]"] = org_code
        self.mda = tk.StringVar(value=next((n_ for n_, c_ in self.orgs.items() if c_ == org_code), "All MDAs"))
        self.cb = ttk.Combobox(top, textvariable=self.mda, values=list(self.orgs), state="readonly", width=48); self.cb.pack(side="left", padx=6); self.cb.bind("<<ComboboxSelected>>", lambda e: self.load())
        ttk.Label(top, text="Search:").pack(side="left", padx=(14, 0)); self.q = tk.StringVar(); e = ttk.Entry(top, textvariable=self.q, width=28); e.pack(side="left", padx=6); e.bind("<KeyRelease>", lambda ev: self.load())
        self.t = ttk.Treeview(f, columns=("mda", "name", "code", "desc", "type", "used"), show="headings", selectmode="browse")
        for c_, w_, h_ in (("mda", 200, "MDA"), ("name", 170, "Name"), ("code", 190, "Economic Code"), ("desc", 290, "Description"), ("type", 80, "For"), ("used", 50, "Used")):
            self.t.heading(c_, text=h_); self.t.column(c_, width=w_, anchor="w" if c_ != "used" else "e")
        self.t.pack(fill="both", expand=True); self.t.bind("<Double-1>", lambda ev: self.edit())
        self.count = tk.StringVar(); ttk.Label(f, textvariable=self.count, foreground=MUTED).pack(anchor="w", pady=(4, 0))
        bar = ttk.Frame(f); bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Delete", command=self.delete).pack(side="right", padx=8)
        ttk.Button(bar, text="Edit\u2026", command=self.edit).pack(side="right")
        ttk.Button(bar, text="New\u2026", style="Accent.TButton", command=self.new).pack(side="right", padx=8)
        self.rows = {}; self.load(); self.grab_set()

    def _org(self): return self.orgs.get(self.mda.get())

    def load(self, select=None):
        for x in self.t.get_children(): self.t.delete(x)
        rows = S.list_templates(self._org(), search=self.q.get())
        rows.sort(key=lambda r: (r["org_name"].lower(), r["name"].lower()))
        self.rows = {str(r["id"]): r for r in rows}
        for r in rows:
            self.t.insert("", "end", iid=str(r["id"]), values=(r["org_name"], r["name"], r["economic_code"], r["description"], r["type"] or "\u2013", r["use_count"]))
        self.count.set(f"{len(rows)} saved item(s)")
        kids = self.t.get_children()
        pick = str(select) if select and str(select) in self.rows else (kids[0] if kids else None)
        if pick: self.t.selection_set(pick); self.t.focus(pick); self.t.see(pick)

    def _sel(self):
        s = self.t.selection()
        return self.rows.get(s[0]) if s else None

    def new(self):
        w = TemplateForm(self, org_code=self._org()); self.wait_window(w)
        if w.result:
            self.changed = True
            if w.result["org_code"] not in self.orgs.values():
                self.orgs[f"{w.result['org_name']}  [{w.result['org_code']}]"] = w.result["org_code"]; self.cb["values"] = list(self.orgs)
            self.load(select=w.result["id"])

    def edit(self):
        r = self._sel()
        if not r:
            messagebox.showinfo("Saved items", "Select a saved item first.", parent=self); return
        w = TemplateForm(self, template=r); self.wait_window(w)
        if w.result: self.changed = True; self.load(select=w.result["id"])

    def delete(self):
        r = self._sel()
        if not r:
            messagebox.showinfo("Saved items", "Select a saved item first.", parent=self); return
        if not messagebox.askyesno("Delete saved item", f"Delete \u201c{r['name']}\u201d for {r['org_name']}?\n\nWarrants that already used it are not affected.", parent=self): return
        S.delete_template(r["id"]); self.changed = True; self.load()


class AddLinesDialog(tk.Toplevel):
    """Add Line: type / search the economic code -> the description fills in -> add as many beneficiaries and amounts as needed under it."""
    def __init__(self, frame):
        super().__init__(frame); self.frame = frame; self.app = frame.app; self.org = frame.mda["code"]
        self.title(f"Add Line \u2013 {frame.mda['name']}"); self.transient(frame); self.geometry("900x700")
        self.code = tk.StringVar(); self.desc = tk.StringVar(); self.payee = tk.StringVar(); self.amount = tk.StringVar(); self.remember = tk.BooleanVar(value=False)
        self.pairs = []; self._auto = ""; self.saved = []
        f = ttk.Frame(self, padding=16); f.pack(fill="both", expand=True)
        one = ttk.LabelFrame(f, text="1.  Economic code and description", padding=12); one.pack(fill="x")
        r = 0
        self.saved = S.list_templates(self.org, self.wtype())
        if self.saved:
            ttk.Label(one, text="Saved items", foreground=MUTED).grid(row=r, column=0, sticky="w", pady=4)
            self.saved_cb = ttk.Combobox(one, values=[t["name"] for t in self.saved], state="readonly", width=58); self.saved_cb.grid(row=r, column=1, sticky="w", pady=4, padx=(8, 0))
            self.saved_cb.bind("<<ComboboxSelected>>", self.use_saved); r += 1
        ttk.Label(one, text="Economic Code", font=("Segoe UI", 10, "bold")).grid(row=r, column=0, sticky="w", pady=4)
        self.code_e = ttk.Entry(one, textvariable=self.code, width=62); self.code_e.grid(row=r, column=1, sticky="w", pady=4, padx=(8, 0))
        ttk.Button(one, text="Search Code", command=self.pick).grid(row=r, column=2, padx=8); r += 1
        ttk.Label(one, text="Description", font=("Segoe UI", 10, "bold")).grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(one, textvariable=self.desc, width=62).grid(row=r, column=1, sticky="w", pady=4, padx=(8, 0)); r += 1
        self.status = tk.StringVar(); self.status_l = ttk.Label(one, textvariable=self.status, wraplength=700, justify="left"); self.status_l.grid(row=r, column=1, columnspan=2, sticky="w")
        two = ttk.LabelFrame(f, text="2.  Add beneficiary and amount", padding=12); two.pack(fill="both", expand=True, pady=(12, 0))
        ttk.Label(two, text="Beneficiary / Payee", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(two, text="Amount (\u20a6)", font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.payee_box = PayeeEntry(two, self.payee, width=52); self.payee_box.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.amt_e = ttk.Entry(two, textvariable=self.amount, width=22, justify="right"); self.amt_e.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(2, 0))
        ttk.Button(two, text="Add", style="Accent.TButton", command=self.add).grid(row=1, column=2, padx=(12, 0), pady=(2, 0))
        ttk.Button(two, text="Forget This Payee", command=self.forget_current_payee).grid(row=1, column=3, padx=(8, 0), pady=(2, 0))
        self.payee_box.entry.bind("<Return>", self._payee_enter, add="+"); self.amt_e.bind("<Return>", self.add)
        ttk.Label(two, text="Type the payee, press Tab, type the amount and press Enter to add it; then type the next payee. All of them use the code and description above. "
                       "Typed a name wrongly and it keeps appearing? Select it in the suggestions and press Delete, or type it and press Forget This Payee.",
                  foreground=MUTED, wraplength=800, justify="left").grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 4))
        self.tree = ttk.Treeview(two, columns=("n", "payee", "amount"), show="headings", height=7)
        for c_, w_, h_ in (("n", 40, "#"), ("payee", 520, "Beneficiary / Payee"), ("amount", 150, "Amount")):
            self.tree.heading(c_, text=h_); self.tree.column(c_, width=w_, anchor="e" if c_ == "amount" else "w")
        self.tree.grid(row=3, column=0, columnspan=4, sticky="nsew"); two.rowconfigure(3, weight=1); two.columnconfigure(0, weight=1)
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        foot = ttk.Frame(two); foot.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self.total_l = ttk.Label(foot, text="", font=("Segoe UI", 10, "bold")); self.total_l.pack(side="left")
        ttk.Button(foot, text="Remove selected", command=self.remove_selected).pack(side="right")
        bar = ttk.Frame(f); bar.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(bar, text="Remember this code and description for this MDA (next time pick it from Saved items)", variable=self.remember).pack(side="left")
        ttk.Button(bar, text="Save Lines", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        self.code.trace_add("write", lambda *a: self._code_changed()); self._code_changed()
        self.grab_set(); self.code_e.focus_set()

    def wtype(self): return self.app.warrant_type.get()

    def use_saved(self, e=None):
        i = self.saved_cb.current()
        if 0 <= i < len(self.saved):
            t = self.saved[i]; self.code.set(t["economic_code"]); self.desc.set(t["description"]); self._auto = t["description"]; self.payee_box.entry.focus_set()

    def pick(self):
        w = CodePicker(self, self.org, self.code.get()); self.wait_window(w)
        if w.result: self.code.set(w.result); self.payee_box.entry.focus_set()

    def say(self, text, err=False):
        self.status.set(text); self.status_l.configure(foreground=ERR if err else MUTED)

    def _code_changed(self):
        code = self.code.get().strip()
        d = desc(code) if code else ""
        if not d:
            self.say("" if len(code) < 20 else "This economic code is not in the database.", len(code) >= 20); return
        if not code_belongs_to(code, self.org):
            self.say("This economic code belongs to a different MDA.", True); return
        if not self.desc.get().strip() or self.desc.get() == self._auto:      # fill in the description unless the user has typed their own
            self.desc.set(d); self._auto = d
        msg = S.check_code_type(code, self.wtype())
        self.say(msg, True) if msg else self.say(f"\u2713 Code found \u2013 {S.template_type(code) or self.wtype()} code, description filled in.")

    def forget_current_payee(self):
        if forget_payee_dialog(self, self.payee.get()):
            self.payee.set(""); self.payee_box._hide(); self.say("The payee was removed from the remembered list.")
            self.payee_box.entry.focus_set()

    def _payee_enter(self, e):
        if self.payee.get().strip(): self.amt_e.focus_set()

    def add(self, event=None):
        try:
            S.lines_for_code(self.org, self.wtype(), self.code.get(), self.desc.get(), [(self.payee.get(), self.amount.get())])    # checks code + this payee/amount
        except S.LineError as exc:
            self.say(str(exc), True)
            (self.code_e if "code" in str(exc).split("\n")[0].lower() else (self.amt_e if self.payee.get().strip() else self.payee_box.entry)).focus_set(); return "break"
        who = S.canonical_payee(self.payee.get()); amt = S.parse_amount(self.amount.get())
        self.pairs.append((who, amt)); self._show()
        self.say(f"Added {who} \u2013 \u20a6{amt:,.2f}. Next beneficiary:")
        self.payee.set(""); self.amount.set(""); self.payee_box.entry.focus_set(); return "break"

    def remove_selected(self):
        sel = self.tree.selection()
        if sel: del self.pairs[int(sel[0])]; self._show()

    def _show(self):
        for x in self.tree.get_children(): self.tree.delete(x)
        for i, (who, amt) in enumerate(self.pairs): self.tree.insert("", "end", iid=str(i), values=(i + 1, who, f"{amt:,.2f}"))
        self.total_l.config(text=f"{len(self.pairs)} beneficiar{'y' if len(self.pairs) == 1 else 'ies'}   \u20a6{sum(a for _, a in self.pairs):,.2f}" if self.pairs else "")

    def save(self):
        if self.payee.get().strip() or self.amount.get().strip():          # something typed but not yet added: add it, or say why not
            if self.add() == "break" and (self.payee.get().strip() or self.amount.get().strip()): return
        try:
            lines = S.lines_for_code(self.org, self.wtype(), self.code.get(), self.desc.get(), self.pairs)
        except S.LineError as exc:
            self.say(str(exc), True); messagebox.showerror("Lines not added", str(exc), parent=self); return
        self.frame.items.extend(lines); self.frame.refresh()
        try: self.frame.tree.see(str(len(self.frame.items) - 1))
        except tk.TclError: pass
        if self.remember.get():
            try: S.remember_code_description(self.org, lines[0]["code"], lines[0]["description"])
            except Exception: pass
        self.destroy()


class MDAFrame(ttk.LabelFrame):
    def __init__(self,parent,app,mda):
        super().__init__(parent,text=f"MDA: {mda['name']}",padding=8); self.app=app; self.mda=mda; self.items=mda.setdefault("items",[])
        top=ttk.Frame(self); top.pack(fill="x",pady=(0,6)); ttk.Label(top,text=f"Organisation Code: {mda['code']}",font=("Segoe UI",9,"bold")).pack(side="left"); ttk.Button(top,text="Remove MDA",command=self.remove).pack(side="right")
        self.tree=ttk.Treeview(self,columns=("n","code","payee","desc","amount"),show="headings",height=5)
        for c,h,w in [("n","#",42),("code","Economic Code",190),("payee","Beneficiary / Payee",205),("desc","Economic Line Item Description",430),("amount","Amount",125)]:self.tree.heading(c,text=h); self.tree.column(c,width=w,anchor="e" if c=="amount" else "w")
        self.tree.pack(fill="x"); self.tree.bind("<Double-1>",lambda e:self.edit_line()); buttons=ttk.Frame(self); buttons.pack(fill="x",pady=(6,0)); ttk.Button(buttons,text="Add Line",style="Accent.TButton",command=self.add_line).pack(side="left"); ttk.Button(buttons,text="Edit",command=self.edit_line).pack(side="left",padx=5); ttk.Button(buttons,text="Delete",command=self.delete_line).pack(side="left"); self.sub=ttk.Label(buttons,font=("Segoe UI",10,"bold")); self.sub.pack(side="right"); self.refresh()
    def refresh(self):
        for x in self.tree.get_children():self.tree.delete(x)
        for i,r in enumerate(self.items,1):self.tree.insert("","end",iid=str(i-1),values=(i,r["code"],S.payee_case(r["payee"]),r["description"],f"{r['amount']:,.2f}"))
        self.sub.configure(text=f"Subtotal: ₦{sum(x['amount'] for x in self.items):,.2f}"); self.app.render()
    def add_line(self):
        w=AddLinesDialog(self); self.wait_window(w)
    def edit_line(self):
        s=self.tree.selection()
        if not s:return
        i=int(s[0]); w=ItemDialog(self,self.mda["code"],self.items[i],warrant_type=self.app.warrant_type.get()); self.wait_window(w)
        if w.result:self.items[i]=w.result; self.refresh()
    def delete_line(self):
        s=self.tree.selection()
        if s:del self.items[int(s[0])]; self.refresh()
    def remove(self):self.app.remove_mda(self.mda)



class App(tk.Tk):
    PREVIEW_DELAY_MS = 250

    def __init__(self, open_file=None):
        if B: B.set_taskbar_identity()                       # taskbar shows OUR logo, not python's
        super().__init__()
        init_db(); self._backup_err = None
        try: S.init(db)
        except Exception as exc: self._backup_err = str(exc); raise
        if S.startup_warning: self._backup_err = S.startup_warning
        try: S.backup_now("start")
        except Exception as exc: self._backup_err = str(exc)
        self.after(1200, self._warn_backup)
        S.seed_payees(BUNDLED_BASE / "payees_seed.txt")
        self.title(f"Warrant Management System Demo  \u2013  v{VERSION}")
        ui = load_ui_settings()
        ch = ui.get("theme")
        self.theme_choice = ch if (ch in THEMES or ch == SYSTEM) else DEFAULT_THEME
        self.theme_name = resolve_theme(self.theme_choice); self.T = THEMES[self.theme_name]; set_palette(self.T)
        if B: B.apply_window_icon(self, APP_DIR, self.T["logo"])
        self.logo_img = B.header_logo(self.T["logo"]) if B else None
        self.geometry("1500x940"); self.minsize(1200, 760)
        try:
            if os.name == "nt": self.state("zoomed")         # opens full-screen like a normal desktop program
        except Exception: pass
        self.mdas = []; self.mda_widgets = []
        self._year_ok = S.default_year(); self._year_busy = False; self.meta = {}
        self.doc_style = load_style(); save_style(self.doc_style)
        self.layout_defaults = load_layout_settings(); save_layout_settings(self.layout_defaults)
        self.table_fmt = self.layout_defaults.get("Recurrent", E.default_layout("Recurrent")); self.table_fmt_custom = False
        self.loaded_warrant_id = None; self.loaded_no = ""; self.loaded_updated_at = None; self.loaded_uid = None; self._clean = ""
        self._render_job = None; self._loading = False; self.summary = None; self.fmt_panel = None; self.fmt_visible = False
        self.setup_style(); self.ui(); self.new(force=True)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        W.install(self)                                       # mouse wheel scrolls whatever is under the pointer, in every window
        self.bind_all("<Map>", self._on_map)                  # dark title bar on every window that opens (Windows 10/11)
        self.after(150, lambda: B and B.set_dark_titlebar(self, self.T["is_dark"]))
        self.after(3000, self._poll_theme)                    # follow Windows light/dark changes while "System (auto)" is chosen
        self._wk_running = False; self._wk_warned = set()
        self.after(8000, self._weekly_tick)                   # weekly PDF export: check shortly after start-up, then every hour
        if open_file:                                         # started by double-clicking a shared warrant file (.wmsw)
            self.after(700, lambda: self.open_shared(open_file))

    def setup_style(self):
        s = ttk.Style(self)
        try: s.theme_use("clam")
        except Exception: pass
        T = self.T
        P, PR, HI, DK, TN, BD = T["bg"], T["primary"], T["hi"], T["dark"], T["tint"], T["border"]
        SF, IK, MU = T["surface"], T["ink"], T["muted"]
        self.configure(background=P)
        self.option_add("*Toplevel.background", P)
        self.option_add("*TCombobox*Listbox.background", SF); self.option_add("*TCombobox*Listbox.foreground", IK)
        self.option_add("*TCombobox*Listbox.selectBackground", T["sel"]); self.option_add("*TCombobox*Listbox.selectForeground", IK)
        s.configure(".", background=P, foreground=IK, font=("Segoe UI", 10), bordercolor=BD, focuscolor=P, lightcolor=P, darkcolor=P)
        s.configure("TFrame", background=P); s.configure("TLabel", background=P, foreground=IK)
        s.configure("TCheckbutton", background=P, focuscolor=P); s.configure("TRadiobutton", background=P, focuscolor=P)
        s.configure("TLabelframe", background=P, bordercolor=BD, relief="solid", borderwidth=1)
        s.configure("TLabelframe.Label", background=P, foreground=DK, font=("Segoe UI", 10, "bold"))
        s.configure("MDA.TLabelframe", padding=8)
        s.configure("TSeparator", background=BD)
        for w in ("TEntry", "TCombobox", "TSpinbox"):
            s.configure(w, fieldbackground=SF, background=SF, foreground=IK, bordercolor=BD, lightcolor=BD, darkcolor=BD, padding=4)
            s.map(w, bordercolor=[("focus", PR)], lightcolor=[("focus", PR)], darkcolor=[("focus", PR)])
        s.map("TCombobox", fieldbackground=[("readonly", SF)], selectbackground=[("readonly", SF)], selectforeground=[("readonly", IK)])
        # buttons: soft white "secondary", solid theme-colour "primary"
        s.configure("TButton", padding=(14, 8), background=SF, foreground=IK, bordercolor=BD, lightcolor=SF, darkcolor=SF, relief="solid", borderwidth=1)
        s.map("TButton", background=[("active", TN), ("disabled", T["dis_bg"])], foreground=[("disabled", T["dis_fg"])], bordercolor=[("active", PR)])
        s.configure("Accent.TButton", background=PR, foreground=SF, bordercolor=PR, lightcolor=PR, darkcolor=PR, font=("Segoe UI", 10, "bold"))
        s.map("Accent.TButton", background=[("active", HI), ("disabled", T["abg"])], foreground=[("disabled", T["afg"])], bordercolor=[("active", HI)])
        s.configure("TNotebook", background=P, borderwidth=0, tabmargins=(0, 6, 0, 0))
        s.configure("TNotebook.Tab", padding=(22, 10), background=T["tab"], foreground=MU, font=("Segoe UI", 10, "bold"), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", SF), ("active", TN)], foreground=[("selected", PR)])
        s.configure("Treeview", rowheight=30, background=SF, fieldbackground=SF, foreground=IK, bordercolor=BD, borderwidth=1)
        s.configure("Treeview.Heading", background=TN, foreground=DK, font=("Segoe UI", 10, "bold"), padding=(8, 8), relief="flat", bordercolor=BD)
        s.map("Treeview.Heading", background=[("active", T["sel"])])
        s.map("Treeview", background=[("selected", T["sel"])], foreground=[("selected", IK)])
        s.configure("Status.TFrame", background=TN); s.configure("Status.TLabel", background=TN, foreground=DK, font=("Segoe UI", 9))
        s.configure("TScrollbar", background=TN, troughcolor=P, bordercolor=P, arrowcolor=DK)
        s.configure("TPanedwindow", background=P); s.configure("Sash", sashthickness=6, background=P)
        for w in ("TEntry", "TCombobox", "TSpinbox"):
            s.configure(w, insertcolor=IK, arrowcolor=IK)
        s.map("TEntry", fieldbackground=[("disabled", T["dis_bg"])], foreground=[("disabled", T["dis_fg"])])
        s.configure("TCheckbutton", foreground=IK); s.configure("TRadiobutton", foreground=IK)

    def apply_theme(self, choice):
        """Switch colour theme live (header, logo, buttons, tabs, tables, canvases, title bar).
        `choice` is a palette name or "System (auto)"."""
        if choice != SYSTEM and choice not in THEMES:
            return
        self.theme_choice = choice
        old = dict(self.T)
        self.theme_name = resolve_theme(choice); self.T = T = THEMES[self.theme_name]; set_palette(T)
        if getattr(self, "theme_var", None) is not None: self.theme_var.set(choice)
        self.setup_style()
        self.logo_img = B.header_logo(T["logo"]) if B else None
        if getattr(self, "logo_lbl", None) is not None and self.logo_img is not None:
            self.logo_lbl.configure(image=self.logo_img)
        if B:
            B.apply_window_icon(self, APP_DIR, T["logo"]); B.set_dark_titlebar(self, T["is_dark"])
        self.paint_header(); self.stripe.configure(bg=T["stripe"])
        self.preview_canvas.configure(bg=T["canvas"]); self.canvas.configure(bg=T["bg"])
        if self.fmt_panel is not None:
            self.fmt_panel.canvas.configure(background=T["bg"])
        self._retint(self, {str(old[k]).lower(): T[k] for k in ("ink", "muted", "err") if old[k] != T[k]})
        save_ui_settings({"theme": choice})
        self.schedule_preview()

    def paint_header(self):
        T = self.T
        for w, key in self.head_items:
            try:
                w.configure(bg=T["head"])
                if key: w.configure(fg=T[key])
            except tk.TclError:
                pass

    def _retint(self, widget, mapping):
        """Labels created with an explicit grey / red / ink colour get the new theme's equivalent."""
        if not mapping:
            return
        for w in widget.winfo_children():
            for opt in ("foreground", "fg"):
                try:
                    v = str(w.cget(opt)).lower()
                except tk.TclError:
                    continue
                if v in mapping:
                    try: w.configure(**{opt: mapping[v]})
                    except tk.TclError: pass
            self._retint(w, mapping)

    def _on_map(self, ev):
        w = ev.widget
        if B and (w is self or isinstance(w, tk.Toplevel)):
            B.set_dark_titlebar(w, self.T["is_dark"])

    def _poll_theme(self):
        try:
            if self.theme_choice == SYSTEM and resolve_theme(SYSTEM) != self.theme_name:
                self.apply_theme(SYSTEM)
        finally:
            self.after(3000, self._poll_theme)

    # ------------------------------------------------------------------ screen
    def ui(self):
        T = self.T
        top = tk.Frame(self, bg=T["head"]); top.pack(fill="x")
        self.head_items = [(top, None)]
        self.logo_lbl = tk.Label(top, image=self.logo_img, bg=T["head"], borderwidth=0) if self.logo_img is not None else None
        if self.logo_lbl is not None: self.logo_lbl.pack(side="left", padx=(18, 12), pady=8); self.head_items.append((self.logo_lbl, None))
        self.title_lbl = tk.Label(top, text="WARRANT MANAGEMENT SYSTEM", font=("Segoe UI", 17, "bold"), bg=T["head"], fg=T["dark"]); self.title_lbl.pack(side="left", pady=12)
        self.subtitle = tk.StringVar(value=f"{S.warrant_types()[0]} Expenditure Release Warrant")
        self.sub_lbl = tk.Label(top, textvariable=self.subtitle, font=("Segoe UI", 10), bg=T["head"], fg=T["primary"]); self.sub_lbl.pack(side="left", padx=18, pady=(8, 0))
        tp = tk.Frame(top, bg=T["head"]); tp.pack(side="right", padx=18)
        theme_lbl = tk.Label(tp, text="Theme", font=("Segoe UI", 9), bg=T["head"], fg=T["muted"]); theme_lbl.pack(side="left", padx=(0, 6))
        self.theme_var = tk.StringVar(value=self.theme_choice)
        tcb = ttk.Combobox(tp, textvariable=self.theme_var, values=[SYSTEM] + list(THEMES), state="readonly", width=14); tcb.pack(side="left")
        tcb.bind("<<ComboboxSelected>>", lambda e: (self.apply_theme(self.theme_var.get()), tcb.selection_clear()))
        self.fy_var = tk.StringVar()
        fy_lbl = tk.Label(top, textvariable=self.fy_var, font=("Segoe UI", 10, "bold"), bg=T["head"], fg=T["primary"]); fy_lbl.pack(side="right", padx=14)
        min_lbl = tk.Label(top, text=f"{S.get('ministry', '')} \u2022 {S.get('state_short', '')}".strip(" \u2022"), font=("Segoe UI", 9), bg=T["head"], fg=T["muted"]); min_lbl.pack(side="right", padx=8)
        self.head_items += [(self.title_lbl, "dark"), (self.sub_lbl, "primary"), (tp, None), (theme_lbl, "muted"), (fy_lbl, "primary"), (min_lbl, "muted")]
        self.stripe = tk.Frame(self, bg=self.T["stripe"], height=3); self.stripe.pack(fill="x")

        bar = ttk.Frame(self); bar.pack(fill="x", padx=12, pady=(10, 4))
        for t, c, accent in [("New", self.new, False), ("Save", self.save, True), ("Validate", self.validate_clicked, False),
                             ("Print", self.print_warrant, True), ("Export PDF", self.pdf, False), ("Edit MDA", self.edit_mda, False),
                             ("Format Settings", self.edit_format_settings, False), ("Share Warrant", self.share_warrant, False), ("Open Shared", self.open_shared, False),
                             ("Weekly Export", self.open_weekly, False), ("Settings", self.open_settings, False)]:
            ttk.Button(bar, text=t, command=c, style="Accent.TButton" if accent else "TButton").pack(side="left", padx=3)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Label(bar, text="Open Warrant No.:", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.quick_no = tk.StringVar()
        qe = ttk.Entry(bar, textvariable=self.quick_no, width=18); qe.pack(side="left", padx=4, ipady=3); qe.bind("<Return>", lambda e: self.quick_open())
        ttk.Button(bar, text="Open", command=self.quick_open).pack(side="left")

        # status bar first (packed at the bottom) so the notebook fills what is left
        sb = ttk.Frame(self, style="Status.TFrame"); sb.pack(side="bottom", fill="x")
        self.status = tk.StringVar(value="Draft")
        ttk.Label(sb, textvariable=self.status, style="Status.TLabel", font=("Segoe UI", 9, "bold")).pack(side="left", padx=12, pady=4)
        self.pag_info = tk.StringVar()
        ttk.Label(sb, textvariable=self.pag_info, style="Status.TLabel").pack(side="right", padx=12)

        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=12, pady=(0, 8)); self.nb = nb
        form = ttk.Frame(nb); prev = ttk.Frame(nb); saved = ttk.Frame(nb)
        nb.add(form, text="Warrant Entry"); nb.add(prev, text="Print Preview"); nb.add(saved, text="Saved Warrants")
        self.tabs = {"entry": form, "preview": prev, "saved": saved}
        self.build_form(form)

        # ---- Print Preview tab: preview on the left, live Format Settings docked on the right
        self.pv_pane = ttk.PanedWindow(prev, orient="horizontal"); self.pv_pane.pack(fill="both", expand=True)
        left = ttk.Frame(self.pv_pane); self.pv_pane.add(left, weight=1)
        pvwrap = ttk.Frame(left); pvwrap.pack(fill="both", expand=True, padx=(4, 4), pady=8)
        self.preview_canvas = tk.Canvas(pvwrap, bg=self.T["canvas"], highlightthickness=0)
        pvy = ttk.Scrollbar(pvwrap, orient="vertical", command=self.preview_canvas.yview); pvx = ttk.Scrollbar(pvwrap, orient="horizontal", command=self.preview_canvas.xview)
        self.preview_canvas.configure(yscrollcommand=pvy.set, xscrollcommand=pvx.set)
        pvy.pack(side="right", fill="y"); pvx.pack(side="bottom", fill="x"); self.preview_canvas.pack(side="left", fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", lambda e: self.schedule_preview())
        self.fmt_panel = FormatPanel(self.pv_pane, self)
        self.toggle_format_panel(True)

        # ---- Saved Warrants tab (was a pop-up window)
        self.saved_panel = SavedWarrantsPanel(saved, self); self.saved_panel.pack(fill="both", expand=True)
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        for v in (self.no, self.date, self.office, self.city, self.state):
            v.trace_add("write", lambda *a: self.on_edit())
        self.warrant_type.trace_add("write", lambda *a: self.on_type_changed())
        self.bind_all("<Control-p>", self._ctrl_p); self.bind_all("<Control-P>", self._ctrl_p)
        self.bind_all("<Control-s>", self._ctrl_s); self.bind_all("<Control-S>", self._ctrl_s)
        self.bind_all("<Control-o>", lambda e: (self.saved_warrants(), "break")[1])

    # ---- tabs / panels
    def show_tab(self, name):
        self.nb.select(self.tabs[name])

    def _on_tab_changed(self, event=None):
        cur = self.nb.select()
        if cur == str(self.tabs["saved"]):
            self.saved_panel.load()
        elif cur == str(self.tabs["preview"]):
            self.schedule_preview()

    def toggle_format_panel(self, show=None):
        show = (not self.fmt_visible) if show is None else bool(show)
        if show and not self.fmt_visible:
            self.pv_pane.add(self.fmt_panel, weight=0); self.fmt_visible = True
            self.after(60, self._place_sash)
        elif not show and self.fmt_visible:
            self.pv_pane.forget(self.fmt_panel); self.fmt_visible = False
        self.schedule_preview()

    def _place_sash(self):
        try:
            w = self.pv_pane.winfo_width()
            if w > 900: self.pv_pane.sashpos(0, w - 480)
        except Exception: pass

    def build_form(self, f):
        h = ttk.LabelFrame(f, text="Warrant Header", padding=12); h.pack(fill="x", padx=8, pady=8)
        self.no = tk.StringVar()
        self.year = tk.StringVar(value=str(self._year_ok)); self.date = tk.StringVar()
        self.office = tk.StringVar(); self.city = tk.StringVar(); self.state = tk.StringVar()
        self.warrant_type = tk.StringVar(value=S.warrant_types()[0]); self._prev_type = self.warrant_type.get()
        ttk.Label(h, text="Warrant Type", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(h, textvariable=self.warrant_type, values=S.warrant_types(), state="readonly", width=29).grid(row=0, column=1, padx=(5, 20), pady=3)
        ttk.Label(h, text="Financial Year", font=("Segoe UI", 9, "bold")).grid(row=0, column=2, sticky="w", pady=3)
        self.year_box = ttk.Combobox(h, textvariable=self.year, values=[str(y) for y in S.years()], width=10)
        self.year_box.grid(row=0, column=3, sticky="w", padx=(5, 20), pady=3)
        for ev in ("<<ComboboxSelected>>", "<Return>", "<FocusOut>"):
            self.year_box.bind(ev, lambda e: self.after_idle(self.commit_year))
        ttk.Label(h, text="Warrant No.", font=("Segoe UI", 9, "bold")).grid(row=0, column=4, sticky="w", pady=3)
        nf = ttk.Frame(h); nf.grid(row=0, column=5, sticky="w", padx=(5, 20), pady=3)
        self.no_xx = tk.StringVar(); self.no_tail = tk.StringVar(); self.no_note = tk.StringVar(); self._date_ok = ""; self._date_busy = False
        digits2 = (self.register(lambda P: P == "" or (P.isdigit() and len(P) <= WD.PREFIX_LEN)), "%P")
        self.xx_box = ttk.Spinbox(nf, from_=0, to=99, wrap=True, width=4, format="%02.0f", textvariable=self.no_xx, validate="key",
                                  validatecommand=digits2, command=self._xx_edited, font=("Consolas", 11, "bold"))
        self.xx_box.pack(side="left")
        ttk.Label(nf, textvariable=self.no_tail, font=("Consolas", 11, "bold")).pack(side="left", padx=(4, 10))
        ttk.Button(nf, text="Update from date", command=self.regenerate_no).pack(side="left")
        for ev in ("<KeyRelease>", "<FocusOut>"):
            self.xx_box.bind(ev, self._xx_edited)
        for i, (lab, v) in enumerate([("Date", self.date), ("Office", self.office), ("City", self.city), ("State", self.state)]):
            r, c = (2 + i // 3 * 2), (i % 3 * 2)
            ttk.Label(h, text=lab + (" (DD/MM/YYYY)" if lab == "Date" else "")).grid(row=r, column=c, sticky="w", pady=3)
            e_ = ttk.Entry(h, textvariable=v, width=32); e_.grid(row=r, column=c + 1, sticky="w", padx=(5, 20), pady=3)
            if lab == "Date":
                self.date_entry = e_
                for ev in ("<Return>", "<FocusOut>"):
                    e_.bind(ev, lambda e: self.after_idle(self.commit_date))
        self.no_note_l = ttk.Label(h, textvariable=self.no_note, foreground=MUTED, wraplength=1000, justify="left"); self.no_note_l.grid(row=5, column=0, columnspan=6, sticky="w")
        ttk.Label(h, text="Type only the first two digits of the Warrant No. \u2013 the month, day and year come from the warrant date (DD/MM/YYYY; the number always lists month then day, e.g. 4 October 2026 gives 1004/26), and the number never changes by itself after it is saved. The number keeps its two-digit year suffix; the warrant date shows the full four-digit year. The year applies to the whole warrant and the wording below. The Warrant Type controls the title, wording, Ref format and schedule layout.", foreground=MUTED, wraplength=1000, justify="left").grid(row=6, column=0, columnspan=6, sticky="w", pady=(7, 0))
        intro = ttk.LabelFrame(f, text="Schedule by MDA", padding=8); intro.pack(fill="both", expand=True, padx=8, pady=8)
        actions = ttk.Frame(intro); actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="Add MDA / Organisation", command=self.add_mda).pack(side="left")
        self.payee_up = tk.BooleanVar(value=S.payee_upper_on())
        ttk.Checkbutton(actions, text="UPPERCASE payees", variable=self.payee_up, command=self.toggle_payee_upper).pack(side="right")
        ttk.Label(actions, text="Each MDA is its own table block with an automatic Total; Total, rows and MDA name stay together across pages.", foreground=MUTED).pack(side="left", padx=12)
        container = ttk.Frame(intro); container.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(container, highlightthickness=0, bg=self.T["bg"]); vs = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set); vs.pack(side="right", fill="y"); self.canvas.pack(side="left", fill="both", expand=True)
        self.mda_host = ttk.Frame(self.canvas); self.window_id = self.canvas.create_window((0, 0), window=self.mda_host, anchor="nw")
        self.mda_host.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window_id, width=e.width))
        self.total = tk.StringVar(); self.words = tk.StringVar()
        bot = ttk.Frame(f); bot.pack(fill="x", padx=8, pady=8)
        ttk.Label(bot, text="GRAND TOTAL", font=("Segoe UI", 12, "bold")).pack(side="left"); ttk.Label(bot, textvariable=self.total, font=("Segoe UI", 12, "bold")).pack(side="left", padx=12); ttk.Label(bot, textvariable=self.words, foreground=MUTED).pack(side="left")

    # ------------------------------------------------------------ state / dirty
    def payload(self):
        return {"warrant_no": self.no.get().strip(), "date": WD.normalise(self.date.get()) or self.date.get(), "office": self.office.get(), "city": self.city.get(), "state": self.state.get(),
                "warrant_type": self.warrant_type.get(), "year": str(self._year_ok), "ref_prefix": self.meta.get("ref_prefix", ""),
                "addressee": self.meta.get("addressee", ""), "state_short": self.meta.get("state_short", ""), "table_fmt": self.table_fmt, "mdas": self.mdas,
                "payee_upper": S.payee_upper_on()}

    def snapshot(self):
        d = self.payload(); d.pop("payee_upper", None)          # the UPPERCASE option is not a change to the warrant
        d.pop("table_fmt", None)                                 # page / table layout is one global setting, not part of a warrant
        return json.dumps(d, sort_keys=True, default=str)

    def mark_clean(self):
        self._clean = self.snapshot()

    def is_dirty(self):
        return self.snapshot() != self._clean

    def has_content(self):
        return bool(self.no.get().strip() or any(m.get("items") for m in self.mdas))

    def confirm_discard(self):
        """True when it is safe to replace what is on screen."""
        if not self.is_dirty() or not self.has_content():
            return True
        r = messagebox.askyesnocancel("Unsaved changes", "This warrant has changes that are not saved.\n\nYes = save them now\nNo = discard them\nCancel = stay on this warrant")
        if r is None:
            return False
        return self.save() if r else True

    def update_status(self):
        dirty = self.is_dirty()
        if self.loaded_warrant_id:
            self.status.set(f"Editing saved Warrant No. {self.loaded_no} \u2013 UNSAVED changes (Save updates this same record)" if dirty else f"Saved \u2013 Warrant No. {self.loaded_no}")
        else:
            self.status.set("New warrant \u2013 not saved yet" if dirty else "New warrant")

    def _sync_format_dialog(self):
        if self.fmt_panel is not None:
            self.fmt_panel.refresh()

    # ------------------------------------------------------------ change events
    def on_edit(self):
        if not self._loading:
            self.render()

    def on_type_changed(self):
        if self._loading:
            return
        new_t = self.warrant_type.get()
        rules, strict = S.code_rules(), S.strict_unlisted()
        bad = []
        for m in self.mdas:
            for li, r in enumerate(m.get("items", []), 1):
                msg = S.check_code_type(r["code"], new_t, rules, strict)
                if msg: bad.append(f"{m['name']}, line {li}: {r['code']}")
        if bad:
            old_t = self._prev_type
            messagebox.showerror("Warrant type not changed",
                                 f"These schedule lines use economic codes that are not allowed on a {new_t} warrant ({S.code_rules_text()}):\n\n" + "\n".join(bad[:15]) +
                                 "\n\nThe type was kept as " + old_t + ". Change or remove those lines first.")
            self._loading = True; self.warrant_type.set(old_t); self._loading = False
            return
        self._prev_type = new_t
        self.table_fmt = self.layout_defaults.get(new_t, E.default_layout(new_t))
        self.table_fmt_custom = True
        self.subtitle.set(f"{self.warrant_type.get()} Expenditure Release Warrant")
        self.meta["ref_prefix"] = S.get(f"ref_prefix:{self.warrant_type.get()}", "")
        self.render(); self._sync_format_dialog()

    def on_format_changed(self):
        self.render(now=True)

    def render(self, now=False):
        total = sum(x["amount"] for m in self.mdas for x in m["items"])
        self.total.set(f"\u20a6{total:,.2f}")
        self.words.set(E.amount_words(total, self.warrant_type.get()) if ENGINE_OK else "")
        self.update_status()
        if now:
            self.render_preview_canvas()
        else:
            self.schedule_preview()

    def schedule_preview(self):
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(self.PREVIEW_DELAY_MS, self.render_preview_canvas)

    def render_preview_canvas(self):
        """Print Preview = the ACTUAL generated PDF (same build_pdf as Export/Print)."""
        if self._render_job:
            self.after_cancel(self._render_job); self._render_job = None
        c = self.preview_canvas; c.delete("all")
        cw = max(c.winfo_width(), 900)
        if not ENGINE_OK:
            c.create_text(20, 20, anchor="nw", text="Install ReportLab to enable the print preview:\npython -m pip install -r requirements.txt"); return
        try:
            tmp_path = str(Path(tempfile.gettempdir()) / f"_warrant_live_preview_{os.getpid()}.pdf")
            with WK.BUILD_LOCK:
                self.summary = E.build_pdf(tmp_path, self.payload(), self.doc_style)
            self.pag_info.set(E.describe_summary(self.summary))
            if self.fmt_panel is not None:
                self.fmt_panel._show_info(self.table_fmt)
            if fitz is None:
                c.create_text(20, 20, anchor="nw", text="Install PyMuPDF to enable the print preview:\npython -m pip install -r requirements.txt"); return
            doc = fitz.open(tmp_path); imgs = []
            try:
                for page in doc:
                    pix = page.get_pixmap(matrix=fitz.Matrix(E.PREVIEW_ZOOM, E.PREVIEW_ZOOM))
                    imgs.append(tk.PhotoImage(data=pix.tobytes("ppm")))
            finally:
                doc.close()
        except Exception as exc:
            c.create_text(20, 20, anchor="nw", text=f"Preview unavailable:\n{exc}"); return
        self._preview_imgs = imgs
        gap = 16
        pw = max(im.width() for im in imgs); total_h = sum(im.height() for im in imgs) + gap * (len(imgs) + 1)
        x0 = max(20, (cw - pw) / 2)
        c.configure(scrollregion=(0, 0, max(cw, pw + 40), total_h + 20))
        y = gap
        for im in imgs:
            c.create_rectangle(x0 + 5, y + 5, x0 + im.width() + 5, y + im.height() + 5, fill=self.T["shadow"], outline="")
            c.create_image(x0, y, anchor="nw", image=im); y += im.height() + gap

    # ------------------------------------------------------------ actions
    def new(self, force=False):
        if not force and not self.confirm_discard():
            return
        self._loading = True
        self.loaded_warrant_id = None; self.loaded_no = ""; self.table_fmt_custom = True
        self.loaded_updated_at = None
        self._year_ok = S.default_year(); self.year.set(str(self._year_ok))
        t0 = S.warrant_types()[0]
        self.table_fmt = self.layout_defaults.get(t0, E.default_layout(t0))
        d = S.defaults_for_new_warrant(t0); self.meta = {k: d[k] for k in ("ref_prefix", "addressee", "state_short")}
        self.date.set(S.date_for_year(self._year_ok)); self.office.set(d["office"])
        self.city.set(d["city"]); self.state.set(d["state"]); self.warrant_type.set(t0); self._prev_type = t0
        self.subtitle.set(f"{t0} Expenditure Release Warrant"); self.mdas = []
        self.loaded_uid = None
        self._date_ok = WD.normalise(self.date.get()) or self.date.get()
        self.no_xx.set(S.suggest_prefix(WD.parse_input(self.date.get()), S.last_prefix()))   # the two digits used last time (next free if taken)
        self.no.set(""); self._compose_no()
        self._loading = False
        self.refresh_year_label(); self.rebuild_mdas(); self.mark_clean(); self.update_status(); self._sync_format_dialog()

    # ---- year / warrant number
    def refresh_year_label(self):
        self.fy_var.set(f"Financial Year {self._year_ok}")
        self.year_box["values"] = [str(y) for y in S.years()]

    def commit_year(self):
        """Apply the year typed or picked in the Year box to the whole warrant (text, date, number, preview)."""
        if self._year_busy:
            return
        self._year_busy = True
        try:
            y = S.parse_year(self.year.get())
            if y is None:
                messagebox.showerror("Financial year", f"Enter a 4-digit year between {S.YEAR_MIN} and {S.YEAR_MAX}.")
                self.year.set(str(self._year_ok)); return
            self.year.set(str(y))
            if y == self._year_ok:
                return
            self._year_ok = y
            S.add_year(y)
            if not self.loaded_warrant_id:
                S.set_default_year(y)                       # a new warrant's year becomes the default for the next one
            self.date.set(S.date_with_year(self.date.get(), y))
            self.commit_date()                               # new warrant: number follows; saved warrant: asks first
            self.refresh_year_label(); self.render()
        finally:
            self._year_busy = False

    # ---- warrant date and the date-based Warrant No.  (XXMMDD/YY)
    def commit_date(self, event=None):
        """The date box was left / Enter pressed: tidy it to DD/MM/YYYY. A NEW warrant's number follows the date automatically; a SAVED
        warrant's number is never changed unless the person says so (and never because of today's date)."""
        if self._loading or self._date_busy:
            return
        self._date_busy = True
        try:
            text = self.date.get().strip(); d = WD.parse_input(text)
            if d is None:
                self.no_note.set(f"The date must be DD/MM/YYYY, for example {WD.EXAMPLE}."); self._refresh_no_tail(); return
            canon = WD.fmt(d)
            if canon != self.date.get():
                self.date.set(canon)
            if canon == self._date_ok:
                self._refresh_no_tail(); return
            self._date_ok = canon
            xx = self.no_xx.get().strip() or WD.prefix_of(self.loaded_no)
            if self.loaded_warrant_id is None:                    # new warrant: the number follows its date
                pick = S.suggest_prefix(d, xx if WD.valid_prefix(xx) else S.last_prefix())
                self.no_xx.set(pick); self._compose_no()
            else:                                                 # saved warrant: only if the person agrees
                new_no = WD.build_no_from_text(xx, canon)
                if new_no and new_no != self.no.get() and messagebox.askyesno(
                        "Warrant date changed",
                        f"The warrant date is now {canon}.\n\nAlso change the Warrant No. to match the new date?\n\n"
                        f"   Now:  {self.no.get()}\n   New:  {new_no}\n\nChoose No to keep the Warrant No. exactly as it is."):
                    self.no_xx.set(xx); self._compose_no()
            self._refresh_no_tail()
        finally:
            self._date_busy = False

    def _xx_edited(self, event=None):
        """The first two digits were typed / picked: that is a deliberate change, so the number is rebuilt from THE WARRANT'S date."""
        if self._loading:
            return
        xx = self.no_xx.get().strip()
        if xx == WD.prefix_of(self.no.get()):                 # nothing was changed (e.g. the cursor just passed through): leave the number alone
            self._refresh_no_tail(); return
        if WD.valid_prefix(xx) and WD.parse_input(self.date.get()) is not None:
            self._compose_no()
        else:
            self._refresh_no_tail()

    def _compose_no(self):
        """Warrant No. = the two digits + the warrant date's month, day, year. Reads only what is on the form, never the clock."""
        no = WD.build_no_from_text(self.no_xx.get(), self.date.get())
        self.no.set(no or "")
        self._refresh_no_tail()

    def regenerate_no(self):
        """The 'Update from date' button: rebuild the number from the two digits and the date now on the form."""
        xx = self.no_xx.get().strip()
        if not WD.valid_prefix(xx):
            messagebox.showinfo("Warrant No.", "Enter the first two digits of the Warrant No. first (for example 23)."); return
        d = WD.parse_input(self.date.get())
        if d is None:
            messagebox.showinfo("Warrant No.", f"Enter the warrant date as DD/MM/YYYY first, for example {WD.EXAMPLE}."); return
        self.date.set(WD.fmt(d)); self._date_ok = WD.fmt(d)
        self._compose_no()

    def _show_number(self, no):
        """Puts a number that is already saved on the screen exactly as it is stored -- nothing is generated or changed."""
        self.no.set(no or "")
        self.no_xx.set(WD.prefix_of(no))
        self._refresh_no_tail()

    def _refresh_no_tail(self):
        no, d = self.no.get().strip(), WD.parse_input(self.date.get())
        warn = False
        if no and not WD.split_no(no):
            self.no_tail.set(f"{no}"); note = "This Warrant No. is kept exactly as it was saved. Type two digits and press Update from date to use the date-based number."
        elif no:
            self.no_tail.set(no[WD.PREFIX_LEN:])
            if d is not None and not WD.no_matches_date(no, d):
                note = "The Warrant No. no longer matches the warrant date. It is kept as it is; press Update from date to rebuild it."; warn = True
            elif S.number_taken(no, self.loaded_warrant_id):
                note = f"Warrant No. {no} is already used by another saved warrant - change the first two digits."; warn = True
            else:
                note = "Warrant No. = first two digits + month, day and year of the warrant date."
        elif d is not None:
            self.no_tail.set(WD.build_no("00", d)[WD.PREFIX_LEN:]); note = "Enter the first two digits of the Warrant No."
        else:
            self.no_tail.set(""); note = ""
        self.no_note.set(note)
        try: self.no_note_l.configure(foreground=ERR if warn else MUTED)
        except Exception: pass

    def edit_mda(self):
        """Edit MDA is password protected: no password, no access to any MDA change."""
        if not mda_gate(self):
            return
        w = MDAAdminDialog(self); self.wait_window(w)
        if getattr(w, "changed", False):
            for m in self.mdas:
                r = db(); row = r.execute("SELECT organisation_name FROM budget WHERE organisation_code=? LIMIT 1", (m["code"],)).fetchone(); r.close()
                if row: m["name"] = row["organisation_name"]
            self.rebuild_mdas()

    # ---- shareable, editable warrant file (.wmsw)
    def share_warrant(self):
        """Saves the warrant on screen as ONE file (.wmsw) that anyone with the WMS can open, edit, save and print."""
        if self.loaded_warrant_id is None or self.is_dirty():
            if not self.has_content():
                messagebox.showinfo("Share warrant", "Open or create a warrant first."); return
            if not messagebox.askyesno("Share warrant", "A shared warrant is a SAVED warrant, so that it can be identified and never duplicated.\n\nSave this warrant now, then share it?"):
                return
            if not self.save(): return
        row = S.load_warrant(self.loaded_warrant_id)
        if row is None:
            messagebox.showerror("Share warrant", "That warrant is no longer in the records."); return
        errs = S.validate_payload(row["data"], historical=True)
        if errs:
            messagebox.showerror("Share warrant", "Only a completed warrant can be shared. This one does not pass the checks:\n\n" + "\n".join(errs[:12])); return
        path = filedialog.asksaveasfilename(parent=self, title="Share warrant as a file", defaultextension=SH.EXT, filetypes=[("WMS warrant file", "*" + SH.EXT)],
                                            initialfile=SH.suggest_name(row["data"]))
        if not path: return
        wt = row["data"].get("warrant_type")
        share_row = copy.deepcopy(row)
        share_row["data"]["table_fmt"] = self.layout_defaults.get(wt, E.default_layout(wt))
        env = SH.build(share_row, VERSION, style={wt: copy.deepcopy(self.doc_style.get(wt, {}))}, payee_upper=S.payee_upper_on())
        try: path = SH.write(path, env)
        except SH.ShareFileError as exc:
            messagebox.showerror("Share warrant", str(exc)); return
        L.get().info("SHARED warrant file written: no=%s uid=%s revision=%s", row["warrant_no"], row.get("uid"), row.get("revision"))
        messagebox.showinfo("Warrant shared", f"Saved:\n{path}\n\nSend this ONE file by WhatsApp (as a document), e-mail, USB stick ... The other person opens it with "
                            "Open Shared in their WMS, can edit it, save it and print it.\n\nNote: the file is not encrypted - share it only with people who should see this warrant.")

    def open_shared(self, path=None):
        """Opens a shared warrant file in this WMS. It is checked first; if you already have that warrant it is found by its identity and
        NEVER duplicated; otherwise it opens as an unsaved warrant (it joins your records once, when you Save it)."""
        if not self.confirm_discard(): return False
        if not path:
            path = filedialog.askopenfilename(parent=self, title="Open shared warrant", filetypes=[("WMS warrant file", "*" + SH.EXT), ("All files", "*.*")])
        if not path: return False
        try: env = SH.read(path)
        except SH.ShareFileError as exc:
            messagebox.showerror("Open shared warrant", str(exc)); return False
        d = env["warrant"]; wt = d.get("warrant_type")
        if wt not in S.warrant_types():
            messagebox.showerror("Open shared warrant", f"This warrant is of type '{wt}', which this WMS does not have (it has: {', '.join(S.warrant_types())})."); return False
        local = S.find_by_uid(env["uid"])
        if local is not None:
            if SH.same_content(local["data"], d):
                self.show_row(local); self.show_tab("entry")
                messagebox.showinfo("Already in your records", f"{SH.describe(env)}\n\nThis warrant is already saved in this WMS and the file has the same content, so your saved copy was "
                                    "opened. No duplicate was created."); return True
            ans = messagebox.askyesnocancel("This warrant is already in your records",
                f"{SH.describe(env)}\n\nYou already have this warrant (saved as {local['warrant_no']}, version {local.get('revision', 1)}), and the file's content is different "
                f"(file version {env.get('revision', 1)}).\n\nYes  = open the FILE's version. When you Save it, it updates your existing record - no second warrant is made.\n"
                "No   = open your saved copy and ignore the file.\nCancel = do nothing.")
            if ans is None: return False
            if not ans:
                self.show_row(local); self.show_tab("entry"); return True
        if not self._share_codes_ok(env): return False
        other = S.warrant_by_number(d.get("warrant_no"))
        clash = other is not None and (local is None or other["id"] != local["id"])
        self._share_formatting(env)
        row = {"id": local["id"] if local else None, "warrant_no": d.get("warrant_no", ""), "updated_at": local.get("updated_at") if local else None,
               "uid": env["uid"], "data": copy.deepcopy(d), "warrant_type": wt, "fiscal_year": d.get("year")}
        self.show_row(row); self._clean = ""; self.update_status(); self.show_tab("entry")
        self.status.set(f"Shared warrant {d.get('warrant_no', '')} opened - NOT saved yet. Edit it if needed, then Save and Print.")
        if clash:
            messagebox.showwarning("Warrant No. already used", f"A DIFFERENT warrant in your records already has the Warrant No. {d.get('warrant_no')}.\n\n"
                                   "Change the first two digits of the Warrant No. before saving this one.")
        else:
            messagebox.showinfo("Shared warrant opened", f"{SH.describe(env)}\n\nIt is open for editing and printing, and is not saved yet. Press Save to keep it in your records "
                                "(this creates one warrant, with its original identity).")
        return True

    def _share_codes_ok(self, env):
        """The warrant's MDAs / economic codes must exist here to save or print it. Offers to add what is missing (needs the MDA password)."""
        gaps = S.share_gaps(env.get("mda_info"))
        n_m, n_c = len(gaps["mdas"]), len(gaps["codes"]) + sum(len(m["codes"]) for m in gaps["mdas"])
        if not (n_m or n_c): return True
        lines = [f"MDA {m['code']} - {m['name']}  ({len(m['codes'])} code(s))" for m in gaps["mdas"][:6]] + [f"code {c['code']}" for c in gaps["codes"][:6]]
        ans = messagebox.askyesnocancel("Missing MDAs / economic codes",
            f"This warrant uses {n_m} MDA(s) and {n_c} economic code(s) that are not in this WMS:\n\n" + "\n".join(lines) +
            "\n\nYes  = add them now (needs the MDA password)\nNo   = open anyway (you cannot save or print it until they are added)\nCancel = do not open")
        if ans is None: return False
        if ans:
            if not mda_gate(self): return False
            try:
                a, b = S.adopt_share_gaps(gaps)
            except (ValueError, PermissionError) as exc:
                messagebox.showerror("Add MDAs / codes", str(exc)); return False
            L.get().info("SHARED warrant: added %d MDA(s), %d economic code(s) from the file", a, b)
        return True

    def _share_formatting(self, env):
        """If the sender printed with different text formatting or the UPPERCASE-payees option, offer to use theirs (never silent)."""
        wt = env["warrant"].get("warrant_type"); theirs = (env.get("style") or {}).get(wt) or {}; mine = self.doc_style.get(wt, {})
        diff = [k for k, v in theirs.items() if k in mine and mine[k] != v]
        up = bool((env.get("options") or {}).get("payee_upper"))
        if not diff and up == S.payee_upper_on(): return
        what = ("text sizes / bold / fonts" if diff else "") + (" and " if diff and up != S.payee_upper_on() else "") + ("UPPERCASE payees" if up != S.payee_upper_on() else "")
        if not messagebox.askyesno("Formatting differs", f"The sender printed this warrant with different {what}.\n\nUse the sender's formatting so it prints exactly as they intended?\n\n"
                                   "Yes = apply it (it becomes your setting)\nNo  = keep your own formatting"):
            return
        for k, v in theirs.items():
            if k.startswith("font_"):
                for t in self.doc_style:
                    if k in self.doc_style[t]: self.doc_style[t][k] = v
            elif k in mine:
                mine[k] = v
        if ENGINE_OK and wt in self.doc_style:
            E.share_text_style(self.doc_style, wt)               # text styling is shared by both warrant types
        save_style(self.doc_style)
        if up != S.payee_upper_on():
            S.set_payee_upper(up); self.payee_up.set(up)

    # ---- weekly PDF export (automatic; the work is done in a background thread)
    def open_weekly(self):
        WeeklyExportDialog(self, self)

    def weekly_check(self):
        """Starts the automatic weekly export in the background when a finished week is waiting for it."""
        if not ENGINE_OK or self._wk_running:
            return
        try:
            if not WK.is_due(): return
        except Exception:
            L.get().exception("weekly export check failed"); return
        self._wk_running = True
        style = copy.deepcopy(self.doc_style)
        run_job(self, lambda: WK.run_pending(style), self._weekly_done)

    def _weekly_tick(self):
        try: self.weekly_check()
        finally: self.after(3600 * 1000, self._weekly_tick)

    def _weekly_done(self, res, err):
        self._wk_running = False
        if err is not None or (res and res["status"] == "error"):
            msg = str(err) if err is not None else res["message"]
            if msg not in self._wk_warned:                    # tell the person once per problem, not every hour
                self._wk_warned.add(msg)
                messagebox.showwarning("Weekly PDF export", f"The weekly PDF export could not finish:\n\n{msg}\n\nIt will be tried again automatically. "
                                       "Settings > Weekly PDF export shows the folder and the last result.")
            return
        if not res or res["status"] != "done":
            return
        text = " | ".join(WK.describe(r) for r in res["results"]); bad = WK.problems_text(res["results"])
        self.status.set("Weekly PDF export: " + text)
        if bad:
            messagebox.showwarning("Weekly PDF export", f"{text}\n\nThese were NOT exported:\n\n{bad[:1500]}\n\nThe list is also in Export_Summary.txt in each week's folder.")
        elif any(r["files"] for r in res["results"]):
            messagebox.showinfo("Weekly PDF export", text)

    def toggle_payee_upper(self):
        """Quick switch: every Beneficiary / Payee in capitals (screen, suggestions, preview, PDF, weekly export). Stored names are untouched."""
        S.set_payee_upper(self.payee_up.get()); self.rebuild_mdas()
        self.status.set("Beneficiary / Payee names are now written in UPPERCASE." if self.payee_up.get() else "Beneficiary / Payee names are shown as they were typed.")

    def open_settings(self):
        w = SettingsDialog(self); self.wait_window(w)
        if w.saved:
            self.payee_up.set(S.payee_upper_on()); self.rebuild_mdas()
            self.status.set("Settings saved \u2013 new warrants use them.")
            if not self.loaded_warrant_id and not self.is_dirty():
                self.new(force=True)
            else:
                self.refresh_year_label()

    def edit_format_settings(self):
        if not ENGINE_OK:
            messagebox.showerror("Missing dependency", "Run python -m pip install -r requirements.txt"); return
        self.show_tab("preview"); self.toggle_format_panel(True)

    def add_mda(self):
        w = OrganisationPicker(self); self.wait_window(w)
        if not w.result: return
        code, name = w.result
        if any(x["code"] == code for x in self.mdas): messagebox.showwarning("MDA already added", "This MDA is already part of the warrant."); return
        self.mdas.append({"code": code, "name": name, "items": []}); self.rebuild_mdas()

    def remove_mda(self, mda):
        if messagebox.askyesno("Remove MDA", f"Remove {mda['name']} and all its schedule lines?"): self.mdas.remove(mda); self.rebuild_mdas()

    def rebuild_mdas(self):
        for w in self.mda_widgets: w.destroy()
        self.mda_widgets = []
        for mda in self.mdas:
            w = MDAFrame(self.mda_host, self, mda); w.pack(fill="x", padx=8, pady=8); self.mda_widgets.append(w)
        self.render()

    def _warn_backup(self):
        if getattr(self, "_backup_err", None):
            messagebox.showwarning("Automatic backup failed", f"{self._backup_err}\n\nThe program still works, but no backup copy was made at start-up. "
                                   "Open Settings > Backups & restore to check the backup folder.")
            self._backup_err = None

    def _saved_unchanged(self):
        """True for a warrant that is already saved and has not been edited: printing / exporting it must not depend on whether the
        MDA / economic-code tables or Settings were changed after it was saved."""
        return bool(self.loaded_warrant_id) and not self.is_dirty()

    def validate(self, show=True):
        """One rule set for Save, PDF export and Print (wms_store.validate_payload): includes the Recurrent/Capital code check."""
        if S.parse_year(self.year.get()) is not None and S.parse_year(self.year.get()) != self._year_ok: self.commit_year()
        self.commit_date()
        e = S.validate_payload(self.payload(), historical=self._saved_unchanged() and not getattr(self, "_for_save", False))
        if e and show:
            more = f"\n... and {len(e) - 20} more" if len(e) > 20 else ""
            messagebox.showerror("Validation failed \u2013 not saved / printed", "\n".join(e[:20]) + more)
        elif show and getattr(self, "_explicit_validate", False):
            messagebox.showinfo("Validation passed", "Warrant No. (format), date, year, MDAs, economic codes (including the Recurrent / Capital check), payees and amounts are all valid.")
        return not e

    def validate_clicked(self):
        self._explicit_validate = True
        try: self.validate()
        finally: self._explicit_validate = False

    # ------------------------------------------------------------ permanent records
    def save(self):
        """Saves permanently in one database transaction. A warrant opened from Saved Warrants is UPDATED in place (same record,
        even if its Warrant No. was corrected) -- never duplicated. After a successful save the record is re-read from the
        database and shown, so the screen always displays what is really stored."""
        self._for_save = True
        try: ok = self.validate()
        finally: self._for_save = False
        if not ok: return False
        try:
            record = self.payload(); record.pop("payee_upper", None)  # the option is applied when printing; the record keeps the names as typed
            res = S.save_warrant(record, self.loaded_warrant_id, self.loaded_updated_at if self.loaded_warrant_id else None, uid=self.loaded_uid)
        except S.ValidationFailed as exc:
            messagebox.showerror("Not saved \u2013 validation failed", "\n".join(exc.errors[:20])); return False
        except S.WarrantError as exc:
            messagebox.showerror("Not saved", str(exc)); return False
        except Exception as exc:
            messagebox.showerror("Save failed", f"The warrant was NOT saved (nothing was changed in the records):\n\n{exc}"); return False
        self.show_row(res["row"])
        try: self.saved_panel.load()
        except Exception: pass
        messagebox.showinfo("Saved", f"Warrant No. {res['row']['warrant_no']} {res['verb']} permanently and reloaded from the database." +
                            ("\nThe existing record was updated \u2013 no new warrant was created." if res["verb"] == "updated" else "\nFind it any time under Saved Warrants."))
        return True

    def show_row(self, r):
        """Puts a record that was just read from the database on the screen (used after Save and by Open)."""
        d = r["data"]
        self._loading = True
        self.loaded_warrant_id = r["id"]; self.loaded_no = r["warrant_no"]; self.loaded_updated_at = r.get("updated_at"); self.loaded_uid = r.get("uid")
        self.date.set(d.get("date", "")); self._date_ok = WD.normalise(self.date.get()) or self.date.get()
        self._show_number(d.get("warrant_no", r["warrant_no"] or "")); self.office.set(d.get("office", "")); self.city.set(d.get("city", "")); self.state.set(d.get("state", ""))
        wt = d.get("warrant_type") or r.get("warrant_type") or S.warrant_types()[0]; self.warrant_type.set(wt); self._prev_type = wt
        self._year_ok = S.parse_year(d.get("year")) or S.parse_year(r.get("fiscal_year")) or S.year_from_date(d.get("date")) or S.default_year()
        self.year.set(str(self._year_ok))
        dflt = S.defaults_for_new_warrant(wt); self.meta = {k: (d.get(k) if d.get(k) else dflt[k]) for k in ("ref_prefix", "addressee", "state_short")}
        self.table_fmt = self.layout_defaults.get(wt, E.default_layout(wt)); self.table_fmt_custom = True
        self.subtitle.set(f"{wt} Expenditure Release Warrant")
        self.mdas = d.get("mdas", [])
        self._loading = False
        self.refresh_year_label(); self.rebuild_mdas(); self.mark_clean(); self.update_status(); self._sync_format_dialog()

    def open_warrant(self, warrant_id):
        """Loads a saved warrant (fresh from the database) into the form for viewing / editing / reprinting. Returns True if loaded."""
        if not self.confirm_discard(): return False
        try: r = S.load_warrant(warrant_id)
        except Exception as exc:
            messagebox.showerror("Open failed", f"The warrant could not be read:\n\n{exc}"); return False
        if not r:
            messagebox.showerror("Not found", "That warrant is no longer in the records."); return False
        self.show_row(r)
        self.show_tab("entry")
        return True

    def saved_warrants(self, initial_no=""):
        self.show_tab("saved")
        self.saved_panel.focus_search(initial_no if isinstance(initial_no, str) and initial_no else None)

    def quick_open(self):
        q = self.quick_no.get().strip()
        if not q:
            self.saved_warrants(); return
        ids = S.find_warrant_ids(q)
        if ids:
            if self.open_warrant(ids[0]): self.quick_no.set("")
        else:
            self.saved_warrants(q)

    def on_close(self):
        if not self.confirm_discard(): return
        save_style(self.doc_style)
        for f in list(_TEMP_FILES) + [str(Path(tempfile.gettempdir()) / f"_warrant_live_preview_{os.getpid()}.pdf")]:
            try: os.remove(f)
            except OSError: pass
        self.destroy()

    # ------------------------------------------------------------ output
    def make_pdf(self, p):
        if not ENGINE_OK: raise RuntimeError("ReportLab is not installed.")
        errs = S.validate_payload(self.payload(), historical=self._saved_unchanged())
        if errs: raise RuntimeError("Not allowed to produce this warrant:\n" + "\n".join(errs[:10]))
        with WK.BUILD_LOCK:
            return E.build_pdf(p, self.payload(), self.doc_style)

    def _ctrl_p(self, event=None):
        self.print_warrant(); return "break"

    def _ctrl_s(self, event=None):
        self.save(); return "break"

    def pdf(self):
        if not ENGINE_OK: messagebox.showerror("Missing dependency", "Run python -m pip install -r requirements.txt"); return
        if not self.validate(): return
        p = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")], initialfile="Warrant_" + self.no.get().strip().replace("/", "-") + ".pdf")
        if not p: return
        try: self.make_pdf(p); messagebox.showinfo("Exported", "PDF created successfully.")
        except Exception as exc: messagebox.showerror("PDF export failed", str(exc))

    def print_warrant(self):
        if not ENGINE_OK: messagebox.showerror("Missing dependency", "Run python -m pip install -r requirements.txt"); return
        if not self.validate(): return
        fd, p = tempfile.mkstemp(prefix="Warrant_", suffix=".pdf"); os.close(fd)
        _TEMP_FILES.append(p)
        try:
            summary = self.make_pdf(p)
        except Exception as exc:
            try: os.remove(p)
            except OSError: pass
            messagebox.showerror("Print failed", f"Could not prepare the warrant for printing.\n\n{exc}"); return
        if PRT is not None and PRT.available():
            PrintDialog(self, p, summary["pages"], self.no.get().strip())      # direct printing: printer, copies, pages -> Print
        elif os.name == "nt":
            messagebox.showwarning("Direct printing not set up", "Direct printing needs the 'pywin32' and 'Pillow' packages.\n"
                                   "Run once:  py -3 -m pip install -r requirements.txt\n\nFor now the PDF will be sent to your PDF program's print command.")
            try: os.startfile(p, "print"); self.status.set("Sent to printer")
            except Exception as exc: messagebox.showerror("Print failed", str(exc))
        else:
            messagebox.showinfo("Print", "Direct printing works on Windows. The PDF was created at:\n" + p)


_TEMP_FILES = []


def _clean_old_temp(days=2):
    """Print copies are temporary; remove the ones a crash or a forced close left behind."""
    cutoff = time.time() - days * 86400
    for f in Path(tempfile.gettempdir()).glob("Warrant_*.pdf"):
        try:
            if f.stat().st_mtime < cutoff: f.unlink()
        except OSError:
            pass
    for f in Path(tempfile.gettempdir()).glob("_warrant_live_preview_*.pdf"):
        try:
            if f.stat().st_mtime < cutoff: f.unlink()
        except OSError:
            pass


def _report_crash(exc_type, exc, tb):
    """pythonw has no console: every unexpected error goes to the log file (history kept), and the user is told where it is."""
    path = L.log_exception(exc_type, exc, tb)
    try:
        messagebox.showerror("Unexpected error", f"Something went wrong:\n\n{exc}\n\nNothing was saved by this step unless a message said so. "
                             f"Details were written to the log:\n{path or LOG_PATH}\n\nSettings > About & log shows the log folder.")
    except Exception:
        pass


def cli_weekly_export():
    """--weekly-export: the automatic weekly export WITHOUT opening a window (this is what the Windows schedule starts).
    Does nothing unless it is switched on in Settings > Weekly PDF export. Exit code 0 = fine / nothing to do, 1 = a problem (see the log)."""
    L.install_hooks()
    try:
        init_db(); S.init(db)
        if not ENGINE_OK:
            L.get().error("Scheduled weekly export: ReportLab is not installed"); return 2
        res = WK.run_pending(load_style())
        L.get().info("Scheduled weekly export: %s - %s", res["status"], res["message"])
        return 1 if res["status"] == "error" else 0
    except Exception:
        L.log_exception(*sys.exc_info(), where="scheduled weekly export"); return 1


if __name__ == "__main__":
    if "--weekly-export" in sys.argv:
        sys.exit(cli_weekly_export())
    L.install_hooks()                                      # errors in background threads / outside the window go to the log too
    if not ENGINE_OK:                                      # say what is wrong instead of dying with a traceback
        _r = tk.Tk(); _r.withdraw()
        messagebox.showerror("Missing component", "ReportLab is not installed, so warrants cannot be laid out.\n\n"
                             "Run once:  py -3 -m pip install -r requirements.txt\nthen start the program again.")
        sys.exit(1)
    _clean_old_temp()
    shared = next((a for a in sys.argv[1:] if a.lower().endswith(SH.EXT) and os.path.isfile(a)), None)
    try:
        app = App(open_file=shared)
    except Exception:
        _report_crash(*sys.exc_info())                     # a start-up failure is logged and shown, not silent
        sys.exit(1)
    app.report_callback_exception = _report_crash          # errors inside buttons/events
    try:
        app.mainloop()
    except Exception:
        _report_crash(*sys.exc_info())
    L.get().info("Warrant System closed")

