"""
wms_log.py -- error logging for the Warrant System (no GUI code, so it is testable on its own).

  * one rotating log file  <data folder>/logs/wms.log  (1 MB x 5 files), timestamped, with full tracebacks
  * every unexpected error -- in a button, at start-up, or in a background thread -- is written there, never lost
  * the log never contains warrant contents or passwords: only events (saved / updated / failed / backup / restore ...), warrant numbers and error text
"""
import logging
import logging.handlers
import os
import platform
import sys
import threading
import traceback

LOGGER_NAME = "wms"
_log_dir = None


def setup(data_dir, version=""):
    """Start logging into <data_dir>/logs. Safe to call more than once. Returns the log file path."""
    global _log_dir
    _log_dir = os.path.join(str(data_dir), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    path = os.path.join(_log_dir, "wms.log")
    lg = logging.getLogger(LOGGER_NAME)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    for h in list(lg.handlers):
        lg.removeHandler(h)
        try: h.close()
        except Exception: pass
    h = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    lg.addHandler(h)
    lg.info("=== Warrant System %s starting | Python %s | %s ===", version or "?", platform.python_version(), platform.platform())
    return path


def log_dir():
    return _log_dir


def get():
    return logging.getLogger(LOGGER_NAME)


def log_exception(exc_type, exc, tb, where="unexpected error"):
    """Write one error with its full traceback. Returns the log path (or None)."""
    try:
        get().error("%s: %s\n%s", where, exc, "".join(traceback.format_exception(exc_type, exc, tb)))
    except Exception:
        pass
    return os.path.join(_log_dir, "wms.log") if _log_dir else None


def install_hooks(on_error=None):
    """Route uncaught exceptions (main thread and background threads) to the log. `on_error(exc_type, exc, tb)` may also tell the user."""
    def hook(t, e, tb):
        if issubclass(t, KeyboardInterrupt):
            return
        log_exception(t, e, tb)
        if on_error:
            try: on_error(t, e, tb)
            except Exception: pass

    def thread_hook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = hook
    threading.excepthook = thread_hook
    return hook
