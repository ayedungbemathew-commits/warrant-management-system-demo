"""
wms_engine.py -- Warrant layout + PDF engine (no GUI code in here).

Everything about how a warrant is paginated lives in this file so the rules
can be tested on their own:

  * every MDA is ONE table block: MDA name row, its lines, and its Total;
  * a block is only split between rows; every row that physically fits in the
    space left on the page STAYS on that page (no blank space left behind);
  * the Total is ALWAYS on the last page of that MDA, together with at least
    `min_tail_rows` (default 1) row -- only as many rows are pushed over as the
    Total needs;
  * a block is never started at the very bottom of a page unless at least
    `min_head_rows` (default 1) row fits under its MDA name;
  * the table may use the lines of the bottom margin set by `bottom_flex_lines`;
  * the MDA name row (and optional column headings) is repeated at the top
    of every page an MDA continues onto, under a running "WARRANT NO" line;
  * PAGE 1 reserves `letterhead_lines` blank lines (default 10) at the top for the pre-printed letterhead; pages 2+ start at
    `cont_top_mm` with "WARRANT NO" on the first row, MDA table right below it. (`letterhead_all_pages` on = pages 2+ also
    reserve the letterhead space; off by default.)

Row heights are measured up-front and handed to ReportLab as exact numbers,
so the pagination decisions and the drawn table can never disagree.
"""
import math
import os
import sys
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, NextPageTemplate,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.doctemplate import LayoutError
import wms_dates as WD

# Tables are allowed to be a little wider than the text frame (the standard
# warrant table is ~184 mm on a 172 mm frame, centred) -- do not raise.
rl_config.allowTableBoundsErrors = 1

# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------
_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
_BUNDLED_FONTS = _BASE / "fonts"

DEFAULT_FONT = "Times New Roman"
# display name -> ordered candidates; each candidate = (regular, bold, italic, bold-italic) file names. The first complete one that
# contains the Naira sign is used: the real Windows font when installed, else the bundled look-alike. Families without a bundled
# look-alike are offered only when Windows has them.
FONT_CATALOG = {
    "Times New Roman": [("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"), ("Tinos-Regular.ttf", "Tinos-Bold.ttf", None, None)],
    "Calibri": [("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"), ("Carlito-Regular.ttf", "Carlito-Bold.ttf", "Carlito-Italic.ttf", "Carlito-BoldItalic.ttf")],
    "Arial": [("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"), ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf")],
    "Cambria": [("cambria.ttc", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf"), ("Caladea-Regular.ttf", "Caladea-Bold.ttf", "Caladea-Italic.ttf", "Caladea-BoldItalic.ttf")],
    "Courier New": [("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"), ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf", "LiberationMono-Italic.ttf", "LiberationMono-BoldItalic.ttf")],
    "Georgia": [("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf")],
    "Verdana": [("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf")],
    "Tahoma": [("tahoma.ttf", "tahomabd.ttf", None, None)],
    "Segoe UI": [("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf")],
    "Trebuchet MS": [("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf")],
    "Palatino Linotype": [("pala.ttf", "palab.ttf", "palai.ttf", "palabi.ttf")],
    "Book Antiqua": [("bkant.ttf", "antquab.ttf", "antquai.ttf", "antquabi.ttf")],
    "Bookman Old Style": [("BOOKOS.TTF", "BOOKOSB.TTF", "BOOKOSI.TTF", "BOOKOSBI.TTF")],
    "Garamond": [("GARA.TTF", "GARABD.TTF", "GARAIT.TTF", None)],
    "Century Gothic": [("GOTHIC.TTF", "GOTHICB.TTF", "GOTHICI.TTF", "GOTHICBI.TTF")],
    "Candara": [("candara.ttf", "candarab.ttf", "candarai.ttf", "candaraz.ttf")],
    "Constantia": [("constan.ttf", "constanb.ttf", "constani.ttf", "constanz.ttf")],
    "Corbel": [("corbel.ttf", "corbelb.ttf", "corbeli.ttf", "corbelz.ttf")],
    "Consolas": [("consola.ttf", "consolab.ttf", "consolai.ttf", "consolaz.ttf")],
}
_font_index = None
_families = {}                       # display name -> {"n","b","i","bi"} registered PDF font names, or None when unusable
_warned = set()


def _font_dirs():
    dirs = [_BUNDLED_FONTS]
    if os.name == "nt":
        dirs += [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    return [d for d in dirs if str(d) and d.exists()]


def _find_font(filename):
    """Path of a font file by (case-insensitive) file name in the bundled, Windows or system font folders, or None."""
    global _font_index
    if _font_index is None:
        _font_index = {}
        for d in _font_dirs():
            try:
                for root, _dirs, files in os.walk(d):
                    for f in files:
                        _font_index.setdefault(f.lower(), Path(root) / f)
            except OSError:
                pass
    return _font_index.get(str(filename).lower())


def _slug(name):
    return "".join(ch for ch in name if ch.isalnum())


def _load_family(display):
    """Register one family with reportlab (regular/bold/italic/bold-italic, so <b> works inside paragraphs). None if unusable."""
    for cand in FONT_CATALOG.get(display, []):
        reg, bold = _find_font(cand[0]), _find_font(cand[1])
        if reg is None or bold is None:
            continue
        slug, names, fonts = _slug(display), {}, {}
        try:
            for key, fn in zip(("n", "b", "i", "bi"), cand):
                path = _find_font(fn) if fn else None
                if path is None:                                   # a missing italic uses the upright face
                    path = reg if key in ("n", "i") else bold
                    if key in ("i", "bi"):
                        path = reg if key == "i" else bold
                nm = f"WF-{slug}-{key}"
                fonts[key] = TTFont(nm, str(path), subfontIndex=0) if str(path).lower().endswith(".ttc") else TTFont(nm, str(path))
                names[key] = nm
            if 0x20A6 not in fonts["n"].face.charToGlyph or 0x20A6 not in fonts["b"].face.charToGlyph:
                continue                                           # no Naira sign: would print an empty box; try the next candidate
            for nm, ft in zip(names.values(), fonts.values()):
                pdfmetrics.registerFont(ft)
            pdfmetrics.registerFontFamily(names["n"], normal=names["n"], bold=names["b"], italic=names["i"], boldItalic=names["bi"])
            return names
        except Exception:
            continue
    return None


def family_names(display):
    if display not in _families:
        _families[display] = _load_family(display)
    return _families[display]


def available_fonts():
    """Display names that can really be used on this PC, in a sensible order (the standard five first)."""
    return [n for n in FONT_CATALOG if family_names(n)]


def font_for(style, role, bold):
    """Registered PDF font name for a part of the warrant (`role`) in the given style; falls back to the default family."""
    want = (style.get(f"font_{role}") or DEFAULT_FONT) if style else DEFAULT_FONT
    fam = family_names(want)
    if fam is None:
        if want not in _warned:
            _warned.add(want)
            try:
                import logging
                logging.getLogger("wms").warning("Font '%s' is not available on this PC; using %s instead", want, DEFAULT_FONT)
            except Exception:
                pass
        fam = family_names(DEFAULT_FONT)
    return fam["b" if bold else "n"]


def share_fonts(style, source_type):
    """Fonts are ONE setting for every warrant: copy the font of each part from `source_type` to all warrant types."""
    src = style[source_type]
    for t, d in style.items():
        if t != source_type:
            for role, _lab in FONT_ROLES:
                d[f"font_{role}"] = src.get(f"font_{role}") or DEFAULT_FONT
    return style


def share_text_style(style, source_type):
    """Copy all non-font typography choices from one warrant type to the others."""
    src = style[source_type]
    for t, d in style.items():
        if t != source_type:
            d.update({k: v for k, v in src.items() if not k.startswith("font_")})
    return style


def unify_fonts(style):
    """After loading saved styles: make the fonts the same for every warrant type. Per part, a font that was changed from the default wins
    (Recurrent first), so settings saved when each type had its own fonts are not lost."""
    types = list(style)
    for role, _lab in FONT_ROLES:
        key = f"font_{role}"
        chosen = next((style[t].get(key) for t in types if style[t].get(key) not in (None, "", DEFAULT_FONT)), DEFAULT_FONT)
        for t in types:
            style[t][key] = chosen
    return style


def size_for(style, role, auto):
    """Point size of a part: the size chosen for it when one is set (5 pt or more), else the automatic size."""
    try:
        v = float((style or {}).get(f"size_{role}", 0) or 0)
    except (TypeError, ValueError):
        v = 0.0
    return v if v >= 5.0 else float(auto)


# keep the old names working (older code / tests refer to them)
_dflt = family_names(DEFAULT_FONT)
if _dflt:
    pdfmetrics.registerFont(TTFont("WarrantTinos", str(_find_font("Tinos-Regular.ttf") or _find_font("times.ttf"))))
    pdfmetrics.registerFont(TTFont("WarrantTinosBold", str(_find_font("Tinos-Bold.ttf") or _find_font("timesbd.ttf"))))
    pdfmetrics.registerFontFamily("WarrantTinos", normal="WarrantTinos", bold="WarrantTinosBold", italic="WarrantTinos", boldItalic="WarrantTinosBold")

# --------------------------------------------------------------------------
# Constants / defaults
# --------------------------------------------------------------------------
PAGE_W_MM = 210.0
PAGE_H_MM = 297.0
PREVIEW_ZOOM = 1.4                      # Print Preview renders the PDF at this zoom
PX_MM = 25.4 / (72.0 * PREVIEW_ZOOM)    # millimetres per Print-Preview pixel
LINE_MM = 15.0 * 25.4 / 72.0            # one "line" = Excel's default 15 pt row = 5.29 mm
CONT_HEADER_MM = 6.0                    # running "WARRANT NO" row on pages 2+
CONT_HEADER_STAMP_CLEAR_MM = 35.0       # keep room at the right for a received/time stamp
FOOTER_MM = 10.0                        # baseline of "Page X of Y"
MIN_FRAME_BOTTOM_MM = 16.0              # the frame never reaches the footer
EPS = 0.01

DEFAULT_STYLE = {
    "Recurrent": {
        "title_size": 11.0, "title_bold": True,
        "ref_size": 10.5, "ref_bold": False,
        "recipient_size": 10.5, "recipient_bold": False,
        "body_size": 10.5,
        "authorization_bold": "full", "provided_bold": "lead", "and_bold": "lead",
        "mda_bold": True,
    },
    "Capital": {
        "title_size": 11.0, "title_bold": True,
        "ref_size": 10.5, "ref_bold": False,
        "recipient_size": 10.5, "recipient_bold": True,
        "body_size": 10.5,
        "authorization_bold": "full", "provided_bold": "off", "and_bold": "off",
        "mda_bold": True,
    },
}


# Every part of the warrant has its own font and an optional size (0 = automatic: the title / body / table size set elsewhere).
FONT_ROLES = [
    ("date", "Date"), ("title", "Title"), ("ref", "Ref line"), ("recipient", "Recipient block"),
    ("authorization", "Authorization paragraph (You are hereby...)"), ("provided", "PROVIDED paragraph"), ("and", "AND paragraph"),
    ("mda", "MDA names"), ("headings", "Column headings"), ("table", "Schedule table (lines)"), ("totals", "Total row (each MDA)"), ("grand", "Grand Total row"),
    ("running", "WARRANT NO on pages 2+"), ("footer", "Page x of y"),
]
for _t in DEFAULT_STYLE.values():
    for _role, _label in FONT_ROLES:
        _t[f"font_{_role}"] = DEFAULT_FONT
        _t[f"size_{_role}"] = 0.0

# Table/page layout. Stored PER WARRANT (inside each saved warrant).
DEFAULT_COL_PX = {
    "Recurrent": [27.5, 163.0, 129.5, 250.0, 160.0],
    "Capital":   [27.5, 255.5, 102.0, 194.0, 151.0],
}
LAYOUT_COMMON = {
    "row_padding": 3.0,        # pt, top/bottom (and left) padding inside every table row
    "table_size": 10.0,        # pt
    "row_height_mm": 0.0,      # minimum row height, 0 = automatic (fit the text)
    "table_bold": False,       # bold/normal for the schedule lines
    "total_bold": True,        # bold/normal for Total / Grand Total rows
    "final_total_full": False, # split MDA: False = every page (incl. the last) totals ITS OWN rows; True = last page shows the MDA's full total
    "col_headings": False,     # print a No./Code/Payee/Description/Amount row under each MDA name
    "mda_gap_mm": 5.0,         # spacing between one MDA table and the next
    "body_line_extra": 4.0,    # pt added to the body font size for line spacing
    "margin_left_mm": 18.0,
    "margin_right_mm": 20.0,
    "margin_top_mm": 0.0,      # extra space ABOVE the letterhead space (PAGE 1 only)
    "cont_top_mm": 14.0,       # top margin of pages 2+ ONLY when "letterhead on every page" is switched off
    "margin_bottom_mm": 35.0,  # stamping/signing band at the bottom of every page
    "letterhead_lines": 10,    # blank lines reserved at the top of PAGE 1 (pre-printed letterhead) -- default 10
    "letterhead_all_pages": False, # False (default): pages 2, 3, ... start at cont_top_mm with WARRANT NO on the first row; True: same letterhead space as page 1
    "bottom_flex_lines": 3,    # lines of the bottom margin that the table (rows AND Totals) may use
    "min_head_rows": 1,        # rows that must fit under an MDA name at the bottom of a page, otherwise the MDA moves on
    "min_tail_rows": 1,        # rows that travel with an MDA Total to its last page (Total is never alone)
    "pag_rules_v": 5,          # bumped when the default pagination rules changed (see norm_layout)
}
MAX_COL_PX = 1200.0                     # no single column can be wider than this (a typo like 99999 must not wreck every warrant)
LAYOUT_LIMITS = {
    "row_padding": (0.0, 20.0), "table_size": (6.0, 18.0), "row_height_mm": (0.0, 40.0),
    "mda_gap_mm": (0.0, 30.0), "body_line_extra": (0.0, 12.0),
    "margin_left_mm": (0.0, 60.0), "margin_right_mm": (0.0, 60.0),
    "margin_top_mm": (0.0, 40.0), "cont_top_mm": (5.0, 40.0), "margin_bottom_mm": (16.0, 70.0),
    "letterhead_lines": (0, 14), "bottom_flex_lines": (0, 12), "pag_rules_v": (5, 5),
    "min_head_rows": (1, 6), "min_tail_rows": (1, 6),
}


def default_layout(warrant_type):
    d = dict(LAYOUT_COMMON)
    d["col_px"] = list(DEFAULT_COL_PX.get(warrant_type, DEFAULT_COL_PX["Recurrent"]))
    return d


def norm_layout(warrant_type, saved=None):
    """Defaults + whatever the warrant saved, validated and clamped.
    Old warrants (saved before a setting existed) simply get its default."""
    d = default_layout(warrant_type)
    if isinstance(saved, dict):
        ver = saved.get("pag_rules_v", 1)
        drop = set()
        if ver < 2:      # first V12 rules (2 rows always forced onto the Total's page) -> new fill-the-page defaults
            drop |= {"min_head_rows", "min_tail_rows"}
        if ver < 3:      # the last page of a split MDA now shows ITS OWN rows' Total by default
            drop |= {"final_total_full"}
        if ver < 4:      # letterhead is now 10 lines on every page: older warrants take the new default too
            drop |= {"letterhead_lines", "letterhead_all_pages"}
        if ver < 5:      # pages 2+ keep their own layout (WARRANT NO on the first row); 10 lines apply to page 1
            drop |= {"letterhead_all_pages"}
        if drop:
            saved = {k: v for k, v in saved.items() if k not in drop}
        for k, v in saved.items():
            if k not in d:
                continue
            try:
                if k == "col_px":
                    cols = [min(max(5.0, float(x)), MAX_COL_PX) if math.isfinite(float(x)) else d[k][i] for i, x in enumerate(v)]
                    if len(cols) == 5:
                        d[k] = cols
                elif isinstance(d[k], bool):
                    d[k] = bool(v)
                elif isinstance(d[k], int):
                    d[k] = int(round(float(v)))
                else:
                    d[k] = float(v)
            except (TypeError, ValueError, OverflowError):
                pass
    for k, (lo, hi) in LAYOUT_LIMITS.items():
        d[k] = type(d[k])(min(max(d[k], lo), hi))
    # The writing area on a page must stay usable whatever is typed in.
    while (PAGE_H_MM - d["margin_top_mm"] - d["letterhead_lines"] * LINE_MM - CONT_HEADER_MM
           - d["margin_bottom_mm"]) < 90.0 and d["letterhead_lines"] > 0:
        d["letterhead_lines"] -= 1
    return d


def share_layout_settings(layouts, source_type, source_layout):
    """Apply page/table settings to all warrant types while keeping each type's column widths."""
    source = norm_layout(source_type, source_layout)
    out = {t: norm_layout(t, d) for t, d in layouts.items()}
    if source_type not in out:
        out[source_type] = default_layout(source_type)
    for t in out:
        if t == source_type:
            out[t] = dict(source)
            out[t]["col_px"] = list(source["col_px"])
        else:
            merged = dict(out[t])
            merged.update({k: v for k, v in source.items() if k != "col_px"})
            out[t] = norm_layout(t, merged)
    return out


# --------------------------------------------------------------------------
# Amount in words
# --------------------------------------------------------------------------
ONES = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]
TEENS = ["Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def three(n):
    a = []
    if n >= 100:
        a += [ONES[n // 100], "Hundred"]
        n %= 100
    if 10 <= n < 20:
        a.append(TEENS[n - 10])
    elif n >= 20:
        a.append(TENS[n // 10])
        if n % 10:
            a.append(ONES[n % 10])
    elif n:
        a.append(ONES[n])
    return " ".join(a)


def words(n):
    if n == 0:
        return "Zero"
    out = []
    for v, name in _SCALES_CAP:
        if n >= v:
            q, n = divmod(n, v)
            out.append(three(q))
            if name:
                out.append(name)
    return " ".join(out)


def money_words(x):
    d = Decimal(str(x)).quantize(Decimal(".01"))
    whole = int(d)
    k = int((d - whole) * 100)
    s = f"{words(whole)} Naira"
    if k:
        s += f" and {words(k)} Kobo"
    return s


def title_words(text):
    """Capitalises every word (and each part of a hyphenated word) except the word 'and', which stays lower case."""
    import re as _re
    return _re.sub(r"[A-Za-z]+", lambda m: m.group(0).lower() if m.group(0).lower() == "and" else m.group(0)[0].upper() + m.group(0)[1:].lower(), text)


_SCALES_LOWER = [(10 ** 15, "quadrillion"), (10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand"), (1, "")]
_SCALES_CAP = [(10 ** 15, "Quadrillion"), (10 ** 12, "Trillion"), (10 ** 9, "Billion"), (10 ** 6, "Million"), (1000, "Thousand"), (1, "")]
MAX_AMOUNT = 10 ** 15 - 0.01          # the largest amount a schedule line may carry (also the limit of the words routines)


def sentence_case_money(x):
    d = Decimal(str(x)).quantize(Decimal(".01"))
    whole = int(d)
    k = int((d - whole) * 100)
    units = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}
    teens = {10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
             16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen"}
    tens = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}

    def below100(n):
        if n < 10:
            return units.get(n, "")
        if n < 20:
            return teens[n]
        return tens[n // 10 * 10] + (("-" + units[n % 10]) if n % 10 else "")

    def group(n):
        if n < 100:
            return below100(n)
        h = n // 100
        rem = n % 100
        s = units[h] + " hundred"
        if rem:
            s += " and " + below100(rem)
        return s

    parts = []
    for v, name in _SCALES_LOWER:
        if whole >= v:
            q, whole = divmod(whole, v)
            parts.append(group(q) + (" " + name if name else ""))
    s = (", ".join(parts) or "zero") + " Naira"
    if k:
        s += " and " + below100(k) + " Kobo"
    return title_words(s)          # every word capitalised except "and" (e.g. "One Hundred and Twenty-Five Naira and Fifty Kobo")


_CAP_ONES = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}
_CAP_TEENS = {10: "Ten", 11: "Eleven", 12: "Twelve", 13: "Thirteen", 14: "Fourteen", 15: "Fifteen",
              16: "Sixteen", 17: "Seventeen", 18: "Eighteen", 19: "Nineteen"}
_CAP_TENS = {20: "Twenty", 30: "Thirty", 40: "Forty", 50: "Fifty", 60: "Sixty", 70: "Seventy", 80: "Eighty", 90: "Ninety"}


def capital_money_words(x):
    """Title-Case amount in words matching the Capital Expenditure warrant template."""
    d = Decimal(str(x)).quantize(Decimal(".01"))
    whole = int(d)
    k = int((d - whole) * 100)

    def below100(n):
        if n < 10:
            return _CAP_ONES.get(n, "")
        if n < 20:
            return _CAP_TEENS[n]
        t = _CAP_TENS[n // 10 * 10]
        return t + (("-" + _CAP_ONES[n % 10]) if n % 10 else "")

    def group(n):
        if n < 100:
            return below100(n)
        h = n // 100
        rem = n % 100
        s = _CAP_ONES[h] + " Hundred"
        if rem:
            s += " and " + below100(rem)
        return s

    parts = []
    remainder = whole
    for v, name in _SCALES_CAP:
        if remainder >= v:
            q, remainder = divmod(remainder, v)
            parts.append(group(q) + ((" " + name) if name else ""))
    whole_words = ", ".join(parts) if parts else "Zero"
    s = f"{whole_words} Naira"
    if k:
        s += f" and {group(k)} Kobo"                         # "...Naira and Thirty-Two Kobo", as in the Recurrent warrant
    return s


def amount_words(total, warrant_type="Recurrent"):
    """Amount in words exactly as the warrant prints it (every word capitalised except 'and'), used for the on-screen total too."""
    return capital_money_words(total) if warrant_type == "Capital" else sentence_case_money(total)


def naira(total):
    return f"\u20a6{total:,.2f}"


def warrant_year(data):
    """The financial year of a warrant: the selected year, else the year of its date, else the current year."""
    import re as _re
    from datetime import datetime as _dt
    v = str(data.get("year", "") or "").strip()
    if _re.fullmatch(r"\d{4}", v):
        return str(int(v))
    m = _re.search(r"/(\d{4}|\d{2})\s*$", str(data.get("date", "")))
    if m:
        y = m.group(1)
        return str(int(y) if len(y) == 4 else 2000 + int(y))
    return str(_dt.now().year)


def authorization_text(warrant_type, total, year, state_short=""):
    """Wording of the authorization paragraph. `year` and `state_short` come from the warrant, never from code."""
    place = f" in {state_short}" if state_short else ""
    if warrant_type == "Capital":
        return (f"You are hereby authorized and required to expend out of the Capital Development Fund during the Financial Year {year} "
                f"a sum not exceeding {capital_money_words(total)} ({naira(total)}) Only, being part of the funds appropriated as Capital Expenditure "
                f"for the provision of services{place} for the Financial Year {year}.")
    return (f"You are hereby authorized and required to expend out of the Consolidated Revenue Fund during the Financial Year {year} "
            f"a sum not exceeding {sentence_case_money(total)} ({naira(total)}) Only, being part of the fund appropriated as Recurrent Expenditure "
            f"for the Provision of services{place} for the financial year, {year}.")


# --------------------------------------------------------------------------
# Canvas that stamps "Page X of Y"
# --------------------------------------------------------------------------
class NumberedCanvas(pdfcanvas.Canvas):
    footer_font, footer_size = "Times-Roman", 9.0           # replaced per document by build_pdf

    def __init__(self, *args, **kwargs):
        pdfcanvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.setFont(self.footer_font, self.footer_size)
            self.drawCentredString(PAGE_W_MM / 2.0 * mm, FOOTER_MM * mm, f"Page {self._pageNumber} of {total_pages}")
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)


# --------------------------------------------------------------------------
# Layout specification (everything derived from settings, computed once)
# --------------------------------------------------------------------------
def _esc(s):
    return _xml_escape(str(s if s is not None else ""))


def _ph(paragraph, width):
    """Height a Paragraph needs at a given width (same call ReportLab's Table makes)."""
    return paragraph.wrap(max(width, 1.0), 10 ** 6)[1]


COLUMN_HEADINGS = ["No.", "Economic Code", "Beneficiary/Payee", "Description", "Amount"]


class Spec:
    def payee_text(self, text):
        t = str(text if text is not None else "")
        return t.upper() if self.payee_upper else t

    def __init__(self, data, style):
        self.payee_upper = bool(data.get("payee_upper"))       # "UPPERCASE payees" option: capitals on the printed warrant
        self.wt = data.get("warrant_type") or "Recurrent"
        if self.wt not in style:
            self.wt = "Recurrent"
        self.s = style[self.wt]
        self.L = L = norm_layout(self.wt, data.get("table_fmt"))
        self.col_pt = [p * PX_MM * mm for p in L["col_px"]]
        self.table_w = sum(self.col_pt)
        self.pad = L["row_padding"]                     # vertical padding (top/bottom) of every row
        # Horizontal padding may never swallow a column: a large "Row padding" used to make the narrow No. column
        # negative-width and ReportLab refused to build the page at all. Normal settings (3 pt) are unaffected.
        self.padx = max(0.0, min(self.pad, min(self.col_pt) / 2.0 - 0.5))
        self.min_row = L["row_height_mm"] * mm
        self.gap = L["mda_gap_mm"] * mm
        self.min_head = L["min_head_rows"]
        self.min_tail = L["min_tail_rows"]

        # ---- page geometry (all mm measured from the page edge) ----
        self.content_top_mm = L["margin_top_mm"] + L["letterhead_lines"] * LINE_MM
        self.frame_bottom_mm = max(L["margin_bottom_mm"] - L["bottom_flex_lines"] * LINE_MM, MIN_FRAME_BOTTOM_MM)
        self.flex_pt = max(L["margin_bottom_mm"] - self.frame_bottom_mm, 0.0) * mm
        self.first_h_pt = (PAGE_H_MM - self.content_top_mm - self.frame_bottom_mm) * mm
        self.cont_top_mm = self.content_top_mm if L["letterhead_all_pages"] else L["cont_top_mm"]
        self.later_h_pt = (PAGE_H_MM - self.cont_top_mm - CONT_HEADER_MM - self.frame_bottom_mm) * mm
        self.frame_left_mm = L["margin_left_mm"]
        self.frame_w_mm = PAGE_W_MM - L["margin_left_mm"] - L["margin_right_mm"]
        self.center_mm = self.frame_left_mm + self.frame_w_mm / 2.0

        # ---- table paragraph styles ----
        ts = L["table_size"]
        st = self.s
        ts_tab = size_for(st, "table", ts); ts_head = size_for(st, "headings", ts)
        tsz = size_for(st, "totals", ts + 0.5); tsz_mda = size_for(st, "mda", ts + 0.5)
        self.cell = ParagraphStyle("WCell", fontName=font_for(st, "table", L["table_bold"]), fontSize=ts_tab, leading=ts_tab + 2.5)
        self.cell_r = ParagraphStyle("WCellR", parent=self.cell, alignment=TA_RIGHT)
        self.head_style = ParagraphStyle("WHead", fontName=font_for(st, "headings", True), fontSize=ts_head, leading=ts_head + 2.5, alignment=TA_CENTER)
        tot_font = font_for(st, "totals", L["total_bold"])                 # Total row of each MDA
        gt_font, gsz = font_for(st, "grand", L["total_bold"]), size_for(st, "grand", ts + 0.5)   # Grand Total row: its own font and size
        self.total_lbl = ParagraphStyle("WTotalLbl", fontName=tot_font, fontSize=tsz, leading=tsz + 1.5, alignment=TA_CENTER)
        self.total_amt = ParagraphStyle("WTotalAmt", fontName=tot_font, fontSize=tsz, leading=tsz + 1.5, alignment=TA_RIGHT)
        self.gt_lbl = ParagraphStyle("WGrandLbl", fontName=gt_font, fontSize=gsz, leading=gsz + 1.5, alignment=TA_RIGHT)
        self.gt_amt = ParagraphStyle("WGrandAmt", fontName=gt_font, fontSize=gsz, leading=gsz + 1.5, alignment=TA_RIGHT)
        self.name_style = ParagraphStyle("WMdaName", fontName=font_for(st, "mda", self.s["mda_bold"]), fontSize=tsz_mda, leading=tsz_mda + 2.5)
        self.gt_label_rpad = 6 * mm + 4.0
        self.log = []          # filled while drawing: what landed on which page
        self.splits = []       # every split decision (used by the tests)


class MDAData:
    """One MDA: its lines, measured row heights, Total (and Grand Total if last)."""

    def __init__(self, spec, index, name, items, grand_total=None):
        self.spec = sp = spec
        self.idx = index
        self.name = name
        self.items = items
        self.total = sum(float(x["amount"]) for x in items)
        self.grand_total = grand_total
        pad, padx = sp.pad, sp.padx
        cw = [c - 2 * padx for c in sp.col_pt]         # ReportLab wraps cell text at colwidth - left - right padding

        self.row_h = []
        for i, r in enumerate(items, 1):
            hs = [_ph(Paragraph(str(i), sp.cell), cw[0]),
                  _ph(Paragraph(_esc(r["code"]), sp.cell), cw[1]),
                  _ph(Paragraph(_esc(sp.payee_text(r["payee"])), sp.cell), cw[2]),
                  _ph(Paragraph(_esc(r["description"]), sp.cell), cw[3]),
                  _ph(Paragraph(f"{float(r['amount']):,.2f}", sp.cell_r), cw[4])]
            self.row_h.append(max(max(hs) + 2 * pad, sp.min_row))

        # header: MDA name row (+ optional column headings)
        self.name_h = _ph(Paragraph(_esc(name), sp.name_style), sp.table_w - padx) + 4.0
        self.cols_h = 0.0
        if sp.L["col_headings"]:
            self.cols_h = max(max(_ph(Paragraph(t, sp.head_style), c) for t, c in zip(COLUMN_HEADINGS, cw)) + 2 * pad, sp.min_row)
        self.head_h = self.name_h + self.cols_h

        # Total row
        span_w = sum(sp.col_pt[:4]) - 2 * padx
        self.total_h = max(max(_ph(Paragraph("Total", sp.total_lbl), span_w),
                               _ph(Paragraph(f"{self.total:,.2f}", sp.total_amt), cw[4])) + 2 * pad, sp.min_row)
        # Grand Total row (only on the last MDA)
        self.gt_h = 0.0
        if grand_total is not None:
            gspan = sum(sp.col_pt[:4]) - sp.gt_label_rpad
            self.gt_h = (sp.gap + 2.0 + 2.0 +
                         max(_ph(Paragraph("GRAND TOTAL", sp.gt_lbl), gspan),
                             _ph(Paragraph(f"{grand_total:,.2f}", sp.gt_amt), cw[4])))
        self.tail_h = self.total_h + self.gt_h

    def height(self, start, end, with_tail):
        return self.head_h + sum(self.row_h[start:end]) + (self.tail_h if with_tail else self.total_h)


class MDABlock(Flowable):
    """A run of one MDA's lines [start:end) drawn as a single table.
    with_tail=True means this run also carries the MDA's Total (and the
    Grand Total for the last MDA), i.e. it is the LAST piece of that MDA."""

    def __init__(self, md, start, end, with_tail, space_before=0.0):
        Flowable.__init__(self)
        self.md, self.start, self.end, self.with_tail = md, start, end, with_tail
        self.hAlign = "CENTER"
        self.spaceBefore = space_before
        self.spaceAfter = 0

    # ---- measurements ----
    def _total_amount(self):
        md = self.md
        part = sum(float(x["amount"]) for x in md.items[self.start:self.end])
        if self.with_tail and md.spec.L["final_total_full"]:
            return md.total                     # last page of the MDA: the MDA's full Total
        return part                             # a page part: the Total of the rows shown on that page

    def _h(self):
        return self.md.height(self.start, self.end, self.with_tail)

    def wrap(self, availWidth, availHeight):
        return self.md.spec.table_w, self._h()

    def split(self, availWidth, availHeight):
        """Called by ReportLab when this block does not fit in the space left on the page.

        1. Measure how many rows physically fit in ALL the remaining space
           (name row + rows, nothing reserved, no rows left behind on purpose).
        2. If this is the last piece of the MDA it must still carry its Total, so
           at least `min_tail_rows` (default 1) rows are handed to the next page
           WITH the Total -- but only as many as the Total actually needs:
           every row that fits stays on this page.
        3. If not even `min_head_rows` (default 1) rows fit under the MDA name,
           the whole block moves to the next page instead."""
        md, sp = self.md, self.md.spec
        n = self.end - self.start
        avail = availHeight
        fresh = avail >= sp.later_h_pt - 2.0

        if self._h() <= avail + EPS:
            return [self]                    # everything (rows + Total) fits: keep it whole

        k, acc = 0, md.head_h                # rows that fit in the remaining space, keeping room for this page's Total
        room = avail - md.total_h
        while k < n and acc + md.row_h[self.start + k] <= room + EPS:
            acc += md.row_h[self.start + k]
            k += 1
        fit_rows = k
        if self.with_tail:
            k = min(k, n - min(sp.min_tail, n))   # the Total never travels without its rows
        else:
            k = min(k, n - 1)
        if k < min(sp.min_head, n) and not fresh:
            return []                        # no decent start here -> whole block moves to next page
        if fresh:
            k = max(k, 1)
            k = min(k, n - 1) if n > 1 else k
        if k <= 0 or k >= n:
            return []
        sp.splits.append({"mda": md.idx, "rows_here": k, "rows_fit": fit_rows, "n": n, "avail": avail,
                          "used": md.head_h + sum(md.row_h[self.start:self.start + k]) + md.total_h,
                          "next_row": md.row_h[self.start + k], "with_tail": self.with_tail})
        first = MDABlock(md, self.start, self.start + k, False, self.spaceBefore)
        rest = MDABlock(md, self.start + k, self.end, self.with_tail, 0.0)
        return [first, rest]

    # ---- drawing ----
    def _table(self, explicit=True):
        md, sp = self.md, self.md.spec
        pad, padx, L = sp.pad, sp.padx, sp.L
        data = [[Paragraph(_esc(md.name), sp.name_style), "", "", "", ""]]
        heights = [md.name_h]
        if L["col_headings"]:
            data.append([Paragraph(t, sp.head_style) for t in COLUMN_HEADINGS])
            heights.append(md.cols_h)
        first_body = len(data)
        for i in range(self.start, self.end):
            r = md.items[i]
            data.append([Paragraph(str(i + 1), sp.cell), Paragraph(_esc(r["code"]), sp.cell),
                         Paragraph(_esc(sp.payee_text(r["payee"])), sp.cell), Paragraph(_esc(r["description"]), sp.cell),
                         Paragraph(f"{float(r['amount']):,.2f}", sp.cell_r)])
            heights.append(md.row_h[i])
        tr = gr = None
        tr = len(data)                          # EVERY page part of an MDA ends with its own Total row
        data.append([Paragraph("Total", sp.total_lbl), "", "", "", Paragraph(f"{self._total_amount():,.2f}", sp.total_amt)])
        heights.append(md.total_h)
        if self.with_tail:
            if md.grand_total is not None:
                gr = len(data)
                data.append([Paragraph("GRAND TOTAL", sp.gt_lbl), "", "", "", Paragraph(f"{md.grand_total:,.2f}", sp.gt_amt)])
                heights.append(md.gt_h)
        last_grid = tr if tr is not None else len(data) - 1

        t = Table(data, colWidths=sp.col_pt, rowHeights=heights if explicit else None, hAlign="CENTER")
        cmds = [
            ("SPAN", (0, 0), (4, 0)), ("ALIGN", (0, 0), (0, 0), "LEFT"), ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, 0), 2), ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ("LEFTPADDING", (0, 0), (-1, 0), 0), ("RIGHTPADDING", (0, 0), (-1, 0), padx),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
            ("GRID", (0, 1), (-1, last_grid), 0.75, colors.black),
            ("TOPPADDING", (0, 1), (-1, last_grid), pad), ("BOTTOMPADDING", (0, 1), (-1, last_grid), pad),
            ("LEFTPADDING", (0, 1), (-1, last_grid), padx), ("RIGHTPADDING", (0, 1), (-1, last_grid), padx),
        ]
        if L["col_headings"]:
            cmds += [("VALIGN", (0, 1), (-1, 1), "MIDDLE")]
        if first_body <= (tr - 1 if tr is not None else len(data) - 1):
            last_body = (tr - 1) if tr is not None else len(data) - 1
            cmds.append(("VALIGN", (0, first_body), (-1, last_body), "BOTTOM"))
        if tr is not None:
            cmds += [("SPAN", (0, tr), (3, tr)), ("ALIGN", (0, tr), (3, tr), "CENTER"),
                     ("ALIGN", (4, tr), (4, tr), "RIGHT"), ("VALIGN", (0, tr), (-1, tr), "MIDDLE")]
        if gr is not None:
            cmds += [("SPAN", (0, gr), (3, gr)), ("ALIGN", (0, gr), (3, gr), "RIGHT"), ("ALIGN", (4, gr), (4, gr), "RIGHT"),
                     ("VALIGN", (0, gr), (-1, gr), "BOTTOM"),
                     ("TOPPADDING", (0, gr), (-1, gr), sp.gap + 2.0), ("BOTTOMPADDING", (0, gr), (-1, gr), 2),
                     ("LEFTPADDING", (0, gr), (-1, gr), 0), ("RIGHTPADDING", (0, gr), (3, gr), sp.gt_label_rpad),
                     ("RIGHTPADDING", (4, gr), (4, gr), padx)]
        t.setStyle(TableStyle(cmds))
        return t

    def draw(self):
        sp = self.md.spec
        t = self._table()
        t.wrapOn(self.canv, sp.table_w, self._h())
        t.drawOn(self.canv, 0, 0)
        sp.log.append({"mda": self.md.idx, "name": self.md.name, "page": self.canv.getPageNumber(),
                       "start": self.start, "end": self.end, "with_total": self.with_tail, "total": self._total_amount()})


# --------------------------------------------------------------------------
# Story + document
# --------------------------------------------------------------------------
def _mode_apply(text, lead_word, mode, bold_style, plain_style):
    if mode == "full":
        return Paragraph(text, bold_style)
    if mode == "lead" and text.startswith(lead_word):
        return Paragraph(f"<b>{lead_word}</b>" + text[len(lead_word):], plain_style)
    return Paragraph(text, plain_style)


def _build_story(spec, data):
    s, L = spec.s, spec.L
    wt = spec.wt
    capital = wt == "Capital"
    mdas = [m for m in data.get("mdas", []) if m.get("items")]
    total = sum(float(x["amount"]) for m in mdas for x in m["items"])
    extra = L["body_line_extra"]

    bs = s["body_size"]

    def para(role, bold, size=None, leading_extra=None, **kw):
        sz = size_for(s, role, bs if size is None else size)
        lead = sz + (extra if leading_extra is None else leading_extra)
        return ParagraphStyle("W" + role, fontName=font_for(s, role, bold), fontSize=sz, leading=lead, **kw)

    title_style = para("title", s["title_bold"], size=s["title_size"], leading_extra=3)
    date_style = para("date", False, leading_extra=3, alignment=TA_RIGHT)
    ref_style = para("ref", s["ref_bold"], leading_extra=extra - 1.5)
    recip_style = para("recipient", s["recipient_bold"], leading_extra=extra - 1.0)
    # authorization / PROVIDED / AND: "bold" style = whole paragraph bold, "plain" style = normal text (a lead word may be bold via <b>)
    auth_bold, auth_plain = para("authorization", True, alignment=TA_LEFT), para("authorization", False, alignment=TA_LEFT)
    prov_bold, prov_plain = para("provided", True, alignment=TA_LEFT), para("provided", False, alignment=TA_LEFT)
    and_bold, and_plain = para("and", True, alignment=TA_LEFT), para("and", False, alignment=TA_LEFT)

    title_prefix = "CAPITAL EXPENDITURE RELEASE WARRANT NO" if capital else "RECURRENT EXPENDITURE RELEASE WARRANT NO"
    no = _esc(data.get("warrant_no", ""))
    year = warrant_year(data)
    ref_prefix = _esc(data.get("ref_prefix", "")).strip()          # e.g. "DEMO/REW" -- stored in the settings table
    ref_line = f"Ref: {ref_prefix}/ {no}" if ref_prefix else f"Ref: {no}"
    addressee = data.get("addressee", "")
    state_short = data.get("state_short", "")

    display_date = WD.normalise(data.get("date", "")) or str(data.get("date", ""))
    story = [NextPageTemplate("Later"),
             Paragraph(_esc(display_date), date_style),
             Paragraph(f"{title_prefix} {no}", title_style),
             Spacer(1, 2.0 * mm),
             Paragraph(ref_line, ref_style),
             Spacer(1, 3.0 * mm)]
    for txt in [addressee, data.get("office", ""), data.get("city", ""), data.get("state", "")]:
        story.append(Paragraph(_esc(txt), recip_style))
    story.append(Spacer(1, 4.0 * mm))
    story.append(_mode_apply(authorization_text(wt, total, year, _esc(state_short)), "You", s["authorization_bold"], auth_bold, auth_plain))
    story.append(Spacer(1, 4.0 * mm))
    provided = ("PROVIDED that the amount so expended on any one sub-organizational/code shall not exceed the amount "
                "entered against that sub-organizational code in the schedule subjoined hereto.")
    and_text = ("AND for so doing, this together with the Accounts, Certificates and Acquaintances prescribed in the "
                f"Laws and Instruments of {_esc(data.get('state', ''))} shall be your sufficient warrant and discharge.")
    story.append(_mode_apply(provided, "PROVIDED", s["provided_bold"], prov_bold, prov_plain))
    story.append(Spacer(1, 3.0 * mm))
    story.append(_mode_apply(and_text, "AND", s["and_bold"], and_bold, and_plain))

    for i, m in enumerate(mdas):
        last = i == len(mdas) - 1
        md = MDAData(spec, i, m.get("name", ""), m["items"], grand_total=total if last else None)
        story.append(MDABlock(md, 0, len(m["items"]), True, space_before=spec.gap))
    return story


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _verify_drawn_schedule(spec, data):
    """Refuse a PDF unless every source line was drawn once, in order, with a matching page-part total."""
    mdas = [m for m in data.get("mdas", []) if m.get("items")]
    if not mdas:
        raise ValueError("The schedule has no printable lines.")
    for mi, mda in enumerate(mdas):
        items = mda["items"]
        parts = [p for p in spec.log if p["mda"] == mi]
        if not parts:
            raise ValueError(f"The schedule for {mda.get('name', 'an MDA')} was not drawn. No PDF was saved.")
        cursor = 0
        for part in parts:
            start, end = part["start"], part["end"]
            if start != cursor or end <= start or end > len(items):
                raise ValueError(f"The schedule for {mda.get('name', 'an MDA')} has a missing or repeated line near S/N {cursor + 1}. No PDF was saved.")
            expected_part = sum(float(x["amount"]) for x in items[start:end])
            expected_total = sum(float(x["amount"]) for x in items) if part["with_total"] and spec.L["final_total_full"] else expected_part
            if abs(float(part["total"]) - expected_total) >= 0.005:
                raise ValueError(f"The schedule total for {mda.get('name', 'an MDA')} does not match its printed lines. No PDF was saved.")
            cursor = end
        if cursor != len(items) or not parts[-1]["with_total"] or any(p["with_total"] for p in parts[:-1]):
            raise ValueError(f"The schedule for {mda.get('name', 'an MDA')} is incomplete. No PDF was saved.")


def build_pdf(path, data, style):
    """Builds the warrant PDF at `path`. Returns a summary of the pagination.
    The file is written under a temporary name and moved into place only when it is complete, so a failed build can never leave
    a half-written PDF (or destroy an older good one) at `path`."""
    path = str(path)
    final_path, path = path, path + ".part"
    spec = Spec(data, style)
    L = spec.L
    warrant_no = data.get("warrant_no", "")
    doc = BaseDocTemplate(path, pagesize=A4, leftMargin=spec.frame_left_mm * mm, rightMargin=(PAGE_W_MM - spec.frame_left_mm - spec.frame_w_mm) * mm,
                          topMargin=spec.content_top_mm * mm, bottomMargin=spec.frame_bottom_mm * mm,
                          title=f"Warrant {warrant_no}", author="Warrant Management System Portfolio Demo")
    fx, fw = spec.frame_left_mm * mm, spec.frame_w_mm * mm
    fb = spec.frame_bottom_mm * mm
    first = Frame(fx, fb, fw, spec.first_h_pt, id="first", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later = Frame(fx, fb, fw, spec.later_h_pt, id="later", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    def on_later_page(canvas, _doc):
        # Pages 2+: keep a clear stamp area at the right; the MDA table follows below the running warrant number.
        # (With letterhead_all_pages switched on, pages 2+ start below the letterhead space instead.)
        canvas.saveState()
        canvas.setFont(font_for(spec.s, "running", True), size_for(spec.s, "running", 10.5))
        y = (PAGE_H_MM - spec.cont_top_mm - 3.5) * mm
        right_edge = spec.center_mm + spec.table_w / mm / 2.0 - CONT_HEADER_STAMP_CLEAR_MM
        canvas.drawRightString(right_edge * mm, y, f"WARRANT NO {warrant_no}")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="First", frames=[first]),
                          PageTemplate(id="Later", frames=[later], onPage=on_later_page)])
    class _Canvas(NumberedCanvas):
        footer_font, footer_size = font_for(spec.s, "footer", False), size_for(spec.s, "footer", 9.0)

    try:
        doc.build(_build_story(spec, data), canvasmaker=_Canvas)
        _verify_drawn_schedule(spec, data)
    except LayoutError as exc:
        _discard(path)
        raise ValueError("A single schedule line is taller than a whole page, so the warrant cannot be laid out. "
                         "Shorten that line's description or Beneficiary / Payee, or widen its column in Format Settings.") from exc
    except BaseException:
        _discard(path)
        raise
    os.replace(path, final_path)
    return summarize(spec, doc.page)


def summarize(spec, pages):
    by = {}
    for e in spec.log:
        m = by.setdefault(e["mda"], {"name": e["name"], "parts": [], "total_page": None, "totals": []})
        m["parts"].append((e["page"], e["start"], e["end"]))
        m["totals"].append((e["page"], e["total"], e["with_total"]))
        if e["with_total"]:
            m["total_page"] = e["page"]
    return {"pages": pages, "mdas": [by[k] for k in sorted(by)], "layout": spec.L}


def describe_summary(sm):
    if not sm:
        return ""
    bits = []
    for i, m in enumerate(sm["mdas"], 1):
        pgs = sorted({p for p, _, _ in m["parts"]})
        span = f"p{pgs[0]}" if len(pgs) == 1 else f"p{pgs[0]}\u2013{pgs[-1]}"
        bits.append(f"MDA {i}: {span}" + (" (Total on each page)" if len(pgs) > 1 else ""))
    return f"{sm['pages']} page{'s' if sm['pages'] != 1 else ''}   |   " + "   |   ".join(bits) if bits else f"{sm['pages']} page(s)"
