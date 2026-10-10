"""Mouse-wheel scrolling everywhere in the program.

Why this exists: Windows sends a wheel turn to the widget that has KEYBOARD FOCUS, not the one under the mouse, and
every table / list / drop-down used to react in its own way (a drop-down even changed its value). This module replaces all of
that with ONE rule:

  * The wheel scrolls what is under the mouse pointer.
  * If that thing cannot scroll any further (a table already at its end, or a list that fits on screen) the next
    scrollable thing around it scrolls instead, so the page keeps moving.
  * Over a header, a label or any blank area, the biggest scrollable area of the same window scrolls
    (e.g. the MDA list on the Entry tab, the preview on the Print Preview tab).
  * Over a scrollbar, the list/canvas beside it scrolls.
  * Shift + wheel scrolls sideways where there is something to scroll sideways.
  * The wheel never changes a drop-down or a number box by accident; it scrolls the page instead.
  * While a pop-up window is open and modal, the window behind it is left alone.

The decision logic is plain Python (no tkinter import) so it is covered by automated tests; `install(root)` wires it into Tk.
"""

SCROLLABLE = ("Canvas", "Treeview", "Listbox", "Text")      # widget classes that can have hidden content
LIST_LIKE = ("Treeview", "Listbox", "Text")                 # these move LIST_UNITS rows per notch; canvases move 1 "unit"
SCROLLBARS = ("TScrollbar", "Scrollbar")
# classes whose built-in wheel behaviour is replaced (left alone it would scroll twice, or change a drop-down / number box)
OVERRIDE = ("Text", "Listbox", "Treeview", "TCombobox", "TSpinbox", "Spinbox", "TScrollbar", "Scrollbar")
LIST_UNITS = 3
EPS = 1e-6


def wheel_steps(delta=0, num=None):
    """Notches in one wheel event. Positive = wheel rolled up (toward the start), negative = down."""
    if num == 4:
        return 1
    if num == 5:
        return -1
    try:
        d = int(delta)
    except (TypeError, ValueError):
        return 0
    if d == 0:
        return 0
    n = max(1, abs(d) // 120)           # Windows: 120 per notch; touch-pads send smaller values (still one step)
    return n if d > 0 else -n


def can_scroll(widget, axis, direction):
    """True when `widget` has hidden content in that direction (axis 'y' or 'x'; direction +1 = toward the end, -1 = toward the start)."""
    try:
        if widget.winfo_class() not in SCROLLABLE:
            return False
        view = getattr(widget, axis + "view", None)
        if not callable(view):
            return False
        first, last = view()
        first, last = float(first), float(last)
    except Exception:
        return False
    return first > EPS if direction < 0 else last < 1.0 - EPS


def chain(widget):
    """The widget, its parent, and so on up to its own window -- never into the window it belongs to."""
    w = widget
    while w is not None:
        yield w
        try:
            if w.winfo_toplevel() is w:
                return
        except Exception:
            return
        w = getattr(w, "master", None)


def descendants(widget):
    stack = [widget]
    while stack:
        w = stack.pop()
        yield w
        try:
            stack.extend(w.winfo_children())
        except Exception:
            pass


def _is_scrollbar(widget):
    try:
        return widget.winfo_class() in SCROLLBARS
    except Exception:
        return False


def _beside(scrollbar, axis):
    """The list / canvas a scrollbar belongs to: the scrollable sibling packed in the same frame."""
    try:
        for sib in scrollbar.master.winfo_children():
            if sib is not scrollbar and sib.winfo_class() in SCROLLABLE and callable(getattr(sib, axis + "view", None)):
                return sib
    except Exception:
        pass
    return None


def find_target(widget, axis, direction):
    """The widget that should scroll for a wheel turn with the pointer over `widget`; None if nothing can move."""
    start = widget
    if _is_scrollbar(widget):
        try:
            orient_axis = "x" if str(widget.cget("orient")).startswith("h") else "y"
        except Exception:
            orient_axis = axis
        start = _beside(widget, orient_axis) or widget
    for w in chain(start):                                    # 1. nearest scrollable thing under the pointer
        if can_scroll(w, axis, direction):
            return w
    try:                                                      # 2. otherwise the biggest scrollable area on screen in this window
        top = widget.winfo_toplevel()
        best, area = None, -1
        for w in descendants(top):
            if can_scroll(w, axis, direction) and w.winfo_viewable():
                a = w.winfo_width() * w.winfo_height()
                if a > area:
                    best, area = w, a
        return best
    except Exception:
        return None


def allowed_by_grab(widget, grab_widget):
    """While a modal window holds the grab, only that window (and pop-ups opened from it) may scroll."""
    if grab_widget is None:
        return True
    try:
        top, gtop = widget.winfo_toplevel(), grab_widget.winfo_toplevel()
    except Exception:
        return True
    t = top
    while t is not None:
        if t is gtop:
            return True
        t = getattr(t, "master", None)
    return False


def scroll(target, axis, direction, notches):
    """Move `target` by `notches` notches; returns True."""
    mult = LIST_UNITS if target.winfo_class() in LIST_LIKE else 1
    getattr(target, axis + "view_scroll")(direction * notches * mult, "units")
    return True


def install(root):
    """Hook the wheel into every window of the program (call once, after the main window exists). Returns the handler."""
    if getattr(root, "_wms_wheel_handler", None):
        return root._wms_wheel_handler

    def handle(event):
        steps = wheel_steps(getattr(event, "delta", 0), getattr(event, "num", None))
        if not steps:
            return "break"
        axis = "x" if (isinstance(getattr(event, "state", 0), int) and event.state & 0x1) else "y"
        direction = -1 if steps > 0 else 1
        try:
            under = root.winfo_containing(event.x_root, event.y_root)
        except KeyError:
            under = False                                     # a widget Python does not know: the open drop-down list of a combobox
        except Exception:
            return "break"
        if under is False:
            try:
                root.tk.call(str(event.widget), axis + "view", "scroll", direction * abs(steps) * LIST_UNITS, "units")
            except Exception:
                pass
            return "break"
        if under is None:
            return "break"
        try:
            grab = root.grab_current()
        except Exception:
            grab = None
        if not allowed_by_grab(under, grab):
            return "break"
        target = find_target(under, axis, direction)
        if target is not None:
            try:
                scroll(target, axis, direction, abs(steps))
            except Exception:
                pass
        return "break"

    seqs = ["<MouseWheel>", "<Shift-MouseWheel>"]
    try:
        if root.tk.call("tk", "windowingsystem") == "x11":
            seqs += ["<Button-4>", "<Button-5>", "<Shift-Button-4>", "<Shift-Button-5>"]
    except Exception:
        pass
    for cls in OVERRIDE:
        for seq in seqs:
            root.bind_class(cls, seq, handle)
    for seq in seqs:
        root.bind_all(seq, handle)
    root._wms_wheel_handler = handle
    return handle
