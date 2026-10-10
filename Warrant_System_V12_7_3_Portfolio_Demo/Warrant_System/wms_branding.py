"""
wms_branding.py -- the app's logo and window/taskbar icon.

Put your own logo in the `assets` folder as
    assets/logo.png      (square PNG, transparent background, at least 256 x 256)
and the app uses it for the header, the window icon, the taskbar and the desktop shortcut.
Delete logo.png to go back to the built-in logo.
"""
import os
import sys
from pathlib import Path

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS = BASE / "assets"
BUILTIN_PNG = ASSETS / "wms_logo.png"
BUILTIN_ICO = ASSETS / "wms_icon.ico"            # exe / installer / shortcut icon (16..128 classic + 256 PNG)
WINDOW_ICO = ASSETS / "wms_window.ico"           # classic bitmap-only icon for the window and taskbar (works with every Tk version, no Pillow needed)
USER_PNG = ASSETS / "logo.png"
APP_ID = "PortfolioDemo.WarrantManagementSystem.V12"


def write_ico(img, dest, sizes=(16, 24, 32, 48, 64, 128), png256=False):
    """Writes a Windows .ico from a square PIL RGBA image using the classic bitmap (BMP/DIB) entry format that every Windows and Tk
    version understands (Pillow writes PNG entries, which Tk's iconbitmap cannot always read -> Windows then shows python's icon).
    png256=True adds one 256x256 PNG entry (what Explorer uses for large icons)."""
    import struct
    from io import BytesIO
    from PIL import Image
    img = img.convert("RGBA")
    entries = []
    for sz in sizes:
        im = img.resize((sz, sz), Image.LANCZOS)
        px = im.load()
        rows = []
        for y in range(sz - 1, -1, -1):                              # DIBs are stored bottom-up, BGRA
            rows.append(b"".join(struct.pack("4B", px[x, y][2], px[x, y][1], px[x, y][0], px[x, y][3]) for x in range(sz)))
        mask_row = b"\x00" * (((sz + 31) // 32) * 4)                  # AND mask: all zero (alpha does the work)
        hdr = struct.pack("<IiiHHIIiiII", 40, sz, sz * 2, 1, 32, 0, sz * sz * 4, 0, 0, 0, 0)
        entries.append((sz, hdr + b"".join(rows) + mask_row * sz))
    if png256:
        buf = BytesIO(); img.resize((256, 256), Image.LANCZOS).save(buf, "PNG"); entries.append((256, buf.getvalue()))
    out = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    body = b""
    for sz, data in entries:
        out += struct.pack("<BBBBHHII", 0 if sz >= 256 else sz, 0 if sz >= 256 else sz, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    Path(dest).write_bytes(out + body)


def logo_png(theme=None):
    """Your assets/logo.png if present, else the built-in logo in the colours of the chosen theme."""
    if USER_PNG.exists():
        return USER_PNG
    themed = ASSETS / f"wms_logo_{(theme or '').lower()}.png"
    return themed if themed.exists() else BUILTIN_PNG


def ensure_ico(dest_dir, window=False):
    """Path of a .ico for the window / shortcut. A user logo.png is converted (and re-converted when it changes).
    window=True gives the bitmap-only icon used for the title bar and taskbar."""
    builtin = WINDOW_ICO if window else BUILTIN_ICO
    if not USER_PNG.exists():
        return builtin if builtin.exists() else None
    try:
        from PIL import Image
        dest = Path(dest_dir) / ("app_window_icon.ico" if window else "app_icon.ico")
        if not dest.exists() or dest.stat().st_mtime < USER_PNG.stat().st_mtime:
            im = Image.open(USER_PNG).convert("RGBA")
            side = max(im.size)
            sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
            write_ico(sq, dest, sizes=(16, 24, 32, 48, 64) if window else (16, 24, 32, 48, 64, 128), png256=not window)
        return dest
    except Exception:
        _log("warning", "could not convert assets\\logo.png to an icon", exc=True)
        return builtin if builtin.exists() else None


def _log(level, msg, exc=False):
    try:
        import logging
        getattr(logging.getLogger("wms"), "exception" if exc else level)(msg)
    except Exception:
        pass


def set_taskbar_identity():
    """Windows groups taskbar buttons by program and shows python's icon; our own app id makes it a separate program with its own icon.
    Must run before the first window is created. The result is written to the log."""
    if os.name == "nt":
        try:
            import ctypes
            hr = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(APP_ID))
            _log("info", f"taskbar identity: AppUserModelID {APP_ID} -> result {hr} ({'ok' if hr == 0 else 'FAILED'})")
        except Exception:
            _log("warning", "taskbar identity: could not set the AppUserModelID", exc=True)


def _relaunch_command():
    """Command Windows runs when the taskbar button is pinned and clicked: this program, without a console window."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    script = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    return f'"{pyw if pyw.exists() else exe}"' + (f' "{script}"' if script else "")


def _set_taskbar_properties(window, ico):
    """Give THIS window its own taskbar identity (app id, icon, name, relaunch command) through the Windows property store.
    This is what makes the taskbar button -- including a pinned one -- show our icon instead of python's. Needs pywin32 (already required)."""
    from win32com.propsys import propsys, pscon
    window.update_idletasks()
    import ctypes
    hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
    store = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
    V = propsys.PROPVARIANTType
    store.SetValue(pscon.PKEY_AppUserModel_ID, V(APP_ID))
    store.SetValue(pscon.PKEY_AppUserModel_RelaunchDisplayNameResource, V("Warrant System"))
    store.SetValue(pscon.PKEY_AppUserModel_RelaunchIconResource, V(f"{ico},0"))
    store.SetValue(pscon.PKEY_AppUserModel_RelaunchCommand, V(_relaunch_command()))
    store.Commit()


def header_logo(theme=None, size=44):
    """PhotoImage of the logo for the header bar (keep a reference), or None."""
    try:
        from PIL import Image, ImageTk
        im = Image.open(logo_png(theme)).convert("RGBA")
        return ImageTk.PhotoImage(im.resize((size, size), Image.LANCZOS))
    except Exception:
        return None


def full_logo(width=190):
    """PhotoImage of the full logo (shield + WMS + 'Warrant Management System') for the About window, on white; or None."""
    try:
        from PIL import Image, ImageTk
        im = Image.open(ASSETS / "wms_logo_full.png").convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255)); bg.alpha_composite(im)
        h = round(im.height * width / im.width)
        return ImageTk.PhotoImage(bg.convert("RGB").resize((width, h), Image.LANCZOS))
    except Exception:
        return None


def _set_native_icon(window, ico):
    """Tell Windows itself which icon this window has (title bar + taskbar button), bypassing Tk. Returns True when both icons were set."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p]
    window.update_idletasks()
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
    big = user32.LoadImageW(None, str(ico), IMAGE_ICON, user32.GetSystemMetrics(11), user32.GetSystemMetrics(12), LR_LOADFROMFILE)      # SM_CXICON / SM_CYICON
    small = user32.LoadImageW(None, str(ico), IMAGE_ICON, user32.GetSystemMetrics(49), user32.GetSystemMetrics(50), LR_LOADFROMFILE)   # SM_CXSMICON / SM_CYSMICON
    if not big or not small:
        return False
    window._native_icons = (big, small)                              # keep the handles alive for the life of the window
    user32.SendMessageW(hwnd, WM_SETICON, 1, big)                    # ICON_BIG   (taskbar, Alt+Tab)
    user32.SendMessageW(hwnd, WM_SETICON, 0, small)                  # ICON_SMALL (title bar)
    return True


def apply_window_icon(window, dest_dir, theme=None):
    """Logo in the title bar and taskbar. Three layers, each independent: classic .ico via Tk, a Pillow photo icon, and the native Windows call.
    Anything that fails is written to the log instead of silently leaving python's icon."""
    ico = None
    if os.name == "nt":
        try:
            ico = ensure_ico(dest_dir, window=True)
            if ico:
                window.iconbitmap(default=str(ico))
        except Exception:
            _log("warning", "window icon: iconbitmap failed", exc=True)
    try:
        from PIL import Image, ImageTk
        im = Image.open(logo_png(theme)).convert("RGBA")
        window._icon_refs = [ImageTk.PhotoImage(im.resize((64, 64), Image.LANCZOS)), ImageTk.PhotoImage(im.resize((32, 32), Image.LANCZOS))]
        window.iconphoto(True, *window._icon_refs)
    except Exception:
        _log("warning", "window icon: Pillow photo icon failed", exc=True)
    if os.name == "nt" and ico:
        def native():
            try:
                ok = _set_native_icon(window, ico)
                _log("info", f"window icon: native icon {'set' if ok else 'FAILED'} from {ico}")
            except Exception:
                _log("warning", "window icon: native icon call failed", exc=True)
            try:
                _set_taskbar_properties(window, ico)
                _log("info", "taskbar properties: app id, icon and relaunch command set")
            except Exception:
                _log("warning", "taskbar properties could not be set (the window icon above is still applied)", exc=True)
        native()
        try:
            window.after(300, native)                                # again once the window is really on screen
        except Exception:
            pass


def set_dark_titlebar(window, dark):
    """Dark (or light) Windows title bar for a window -- Windows 10 (1809+) and 11; silently ignored elsewhere."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):                      # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old id)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass
