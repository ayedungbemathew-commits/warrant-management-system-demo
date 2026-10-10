"""
wms_print.py -- print a warrant PDF straight to a Windows printer.

The PDF that the Print Preview shows is rendered page by page with PyMuPDF and
drawn onto the chosen printer through the Windows GDI (pywin32 + Pillow), so no
PDF reader (Adobe/Edge) is involved and nothing has to be exported first.

Only this module touches the printer; the layout itself is wms_engine.py.
Needs (Windows only):  pip install pywin32 Pillow
"""
import os

RENDER_DPI = 300           # page image resolution sent to the printer (sharp text, small spool file)
A4_MM = (210.0, 297.0)


def _imports():
    """The Windows-only pieces. Returns a dict, or raises ImportError with a clear message."""
    if os.name != "nt":
        raise ImportError("Direct printing works on Windows only.")
    try:
        import win32con, win32gui, win32print, win32ui
        from PIL import ImageWin
    except ImportError as exc:
        raise ImportError("Direct printing needs the 'pywin32' and 'Pillow' packages.\n"
                          "Run:  py -3 -m pip install -r requirements.txt") from exc
    return dict(win32con=win32con, win32gui=win32gui, win32print=win32print, win32ui=win32ui, ImageWin=ImageWin)


def available():
    try:
        _imports()
        return True
    except ImportError:
        return False


def list_printers():
    """Names of the printers this PC can print to (local + network)."""
    m = _imports()
    p = m["win32print"]
    flags = p.PRINTER_ENUM_LOCAL | p.PRINTER_ENUM_CONNECTIONS
    names = [r[2] for r in p.EnumPrinters(flags)]
    return sorted(set(names), key=str.lower)


def default_printer():
    try:
        return _imports()["win32print"].GetDefaultPrinter()
    except Exception:
        return ""


def parse_pages(text, total):
    """'1-3, 5' -> [1,2,3,5] (1-based, within 1..total). '' or 'all' -> every page.
    Raises ValueError with a readable message."""
    text = (text or "").strip().lower()
    if not text or text == "all":
        return list(range(1, total + 1))
    out = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = [int(x) for x in part.split("-", 1)]
            else:
                a = b = int(part)
        except ValueError:
            raise ValueError(f"'{part}' is not a page number or range (example: 1-3, 5).")
        if a > b:
            a, b = b, a
        if a < 1 or b > total:
            raise ValueError(f"This warrant has {total} page(s); {part} is outside 1-{total}.")
        out.extend(range(a, b + 1))
    seen, pages = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            pages.append(x)
    if not pages:
        raise ValueError("No pages selected.")
    return pages


def page_rect(img_w, img_h, phys_w, phys_h, off_x, off_y, dpi_x, dpi_y):
    """Where to draw an A4 page image on the printer's device surface (pixels).

    The device surface starts at the printable-area corner, the paper starts at
    (-off_x, -off_y). If the paper is A4 (within 3 mm) the image is stretched to the
    exact paper size, so millimetres on screen = millimetres on paper; on any other
    paper the page is scaled down to fit and centred, never distorted."""
    paper_mm = (phys_w / dpi_x * 25.4, phys_h / dpi_y * 25.4)
    is_a4 = abs(paper_mm[0] - A4_MM[0]) <= 3.0 and abs(paper_mm[1] - A4_MM[1]) <= 3.0
    if is_a4:
        w, h = phys_w, phys_h
    else:
        k = min(phys_w / img_w, phys_h / img_h)
        w, h = int(img_w * k), int(img_h * k)
    left = -off_x + (phys_w - w) // 2
    top = -off_y + (phys_h - h) // 2
    return int(left), int(top), int(left + w), int(top + h)


def _render(doc, index, fitz, Image):
    page = doc[index]
    zoom = RENDER_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def print_pdf(pdf_path, printer_name, pages=None, copies=1, title="Warrant", progress=None):
    """Prints the given 1-based `pages` (default all) of the PDF, `copies` times
    (each copy complete, in page order). Returns the number of sheets sent."""
    m = _imports()
    import fitz
    from PIL import Image
    win32con, win32gui, win32print, win32ui, ImageWin = (m[k] for k in ("win32con", "win32gui", "win32print", "win32ui", "ImageWin"))

    doc = fitz.open(pdf_path)
    try:
        total = len(doc)
        pages = pages or list(range(1, total + 1))
        copies = max(1, int(copies))

        # Ask the driver for A4 portrait (some default to Letter); fall back to the driver defaults if it refuses.
        hdc = None
        try:
            hp = win32print.OpenPrinter(printer_name)
            try:
                devmode = win32print.GetPrinter(hp, 2)["pDevMode"]
            finally:
                win32print.ClosePrinter(hp)
            if devmode is not None:
                devmode.PaperSize = win32con.DMPAPER_A4
                devmode.Orientation = win32con.DMORIENT_PORTRAIT
                devmode.Fields |= win32con.DM_PAPERSIZE | win32con.DM_ORIENTATION
                hdc = win32ui.CreateDCFromHandle(win32gui.CreateDC("WINSPOOL", printer_name, devmode))
        except Exception:
            hdc = None
        if hdc is None:
            hdc = win32ui.CreateDC()
            hdc.CreatePrinterDC(printer_name)

        phys_w, phys_h = hdc.GetDeviceCaps(110), hdc.GetDeviceCaps(111)      # PHYSICALWIDTH / PHYSICALHEIGHT
        off_x, off_y = hdc.GetDeviceCaps(112), hdc.GetDeviceCaps(113)        # PHYSICALOFFSETX / PHYSICALOFFSETY
        dpi_x, dpi_y = hdc.GetDeviceCaps(88), hdc.GetDeviceCaps(90)          # LOGPIXELSX / LOGPIXELSY

        sheets, todo = 0, copies * len(pages)
        started = False
        try:
            hdc.StartDoc(title)
            started = True
            for _ in range(copies):
                for pno in pages:
                    img = _render(doc, pno - 1, fitz, Image)
                    rect = page_rect(img.width, img.height, phys_w, phys_h, off_x, off_y, dpi_x, dpi_y)
                    hdc.StartPage()
                    ImageWin.Dib(img).draw(hdc.GetHandleOutput(), rect)
                    hdc.EndPage()
                    sheets += 1
                    if progress:
                        progress(sheets, todo)
            hdc.EndDoc()
            started = False
        except Exception:
            if started:
                try:
                    hdc.AbortDoc()
                except Exception:
                    pass
            raise
        finally:
            try:
                hdc.DeleteDC()
            except Exception:
                pass
        return sheets
    finally:
        doc.close()
