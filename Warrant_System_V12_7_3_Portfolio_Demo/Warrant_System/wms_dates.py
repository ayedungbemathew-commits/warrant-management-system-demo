"""Warrant dates (DD/MM/YYYY) and date-based Warrant Nos. (XXMMDD/YY).

  * Every warrant date is written DD/MM/YYYY (04/10/2026 = 4 October 2026) on screen, in Print Preview, in PDFs, in shared files.
  * A Warrant No. is built from the WARRANT'S OWN DATE plus two digits the user chooses. The number keeps the month-then-day order
    it has always had (issued numbers are identifiers and are never rewritten):
        XX  +  MM  +  DD  /  YY      e.g. XX=99, date 04/10/2026 (4 October)  ->  991004/26
  * Nothing in this module reads the computer's clock. A number is a pure function of (XX, warrant date), so reopening a warrant
    tomorrow can never change it; it only changes when the person deliberately changes the date or the two digits.

Two parsers on purpose:
  parse_input   what a person TYPES:   DD/MM/YY or DD/MM/YYYY (also YYYY-MM-DD)
  parse_stored  current DD/MM/YYYY or DD/MM/YY.
  from_mdy      reads the MM/DD/YY(YY) text that V12.5 - V12.6 wrote (used only by the one-time database upgrade and for old shared files).
"""
import re
from datetime import date, datetime

DISPLAY = "%d/%m/%Y"
EXAMPLE = "25/10/2026"                                       # unambiguous: the 25th can only be a day
PREFIX_LEN = 2
NO_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})/(\d{2})$")           # XX MM DD / YY


def fmt(d):
    """date -> '04/10/2026' (4 October 2026)."""
    return f"{d.day:02d}/{d.month:02d}/{d.year:04d}"


def _mk(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_input(text):
    """What a person types -> date, or None.  DD/MM/YY (20YY), DD/MM/YYYY, or YYYY-MM-DD."""
    t = str(text or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", t)
    if m:
        dy, mo, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        return _mk(int(yr) if len(yr) == 4 else 2000 + int(yr), mo, dy)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_stored(text):
    """A date already in the records -> date, or None. DD/MM/YYYY and the shorter DD/MM/YY are accepted."""
    t = str(text or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2})", t)
    if m:
        return _mk(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def from_mdy(text):
    """'MM/DD/YYYY' or 'MM/DD/YY' text (what V12.5 - V12.6 saved) -> 'DD/MM/YYYY'; None when it is not a valid date of that kind."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s*", str(text or ""))
    if not m:
        return None
    yr = m.group(3)
    d = _mk(int(yr) if len(yr) == 4 else 2000 + int(yr), int(m.group(1)), int(m.group(2)))
    return fmt(d) if d else None


def normalise(text):
    """Typed date -> canonical 'DD/MM/YYYY'; None if it is not a real date."""
    d = parse_input(text)
    return fmt(d) if d else None


def year_of(text):
    d = parse_stored(text)
    return d.year if d else None


def with_year(text, year):
    """The same month and day in another year (29 Feb -> 28 Feb when needed); text unchanged if it is not a date."""
    d = parse_input(text)
    if d is None:
        return text
    return fmt(_mk(year, d.month, d.day) or _mk(year, d.month, 28))


# ---- Warrant No.  XXMMDD/YY ---------------------------------------------------------------------------------------------
def valid_prefix(xx):
    return bool(re.fullmatch(r"\d{2}", str(xx or "").strip()))


def build_no(xx, d):
    """('99', date 2026-10-04) -> '991004/26'.  Uses only its arguments; never the current date."""
    xx = str(xx or "").strip()
    if not valid_prefix(xx):
        raise ValueError("Enter the first two digits of the Warrant No. (for example 23).")
    return f"{xx}{d.month:02d}{d.day:02d}/{d.year % 100:02d}"


def build_no_from_text(xx, date_text):
    """Same, from the date as typed / saved; None when the digits or the date are not usable yet."""
    d = parse_input(date_text)
    if d is None or not valid_prefix(xx):
        return None
    return build_no(xx, d)


def split_no(no):
    """'991004/26' -> ('99', 10, 4, 26); None if the number does not follow XXMMDD/YY."""
    m = NO_RE.match(str(no or "").strip())
    return (m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))) if m else None


def prefix_of(no):
    s = split_no(no)
    return s[0] if s else ""


def no_matches_date(no, d):
    """True when the number's MMDD/YY part is exactly this date's month, day and year."""
    s = split_no(no)
    return bool(s) and d is not None and (s[1], s[2], s[3]) == (d.month, d.day, d.year % 100)

