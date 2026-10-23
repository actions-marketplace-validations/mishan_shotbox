#!/usr/bin/env python3
"""A libadwaita window to read the accessible tree of: test/a11y_app.py
[fixed] [notes] [trap] [dialog] [lists] [one] [unfocusable] [untitled] [second] [faint]
[thin] [long [down]] [keys] [form] [decoys] [lines] [letter] [carousel] [nested [recheck]] [split] [twin [hidden] [under]] [over [hidden] [drawn | named]] [lone] [slow] [alert]
[freeze] [ready [late]] [modal] [warned] [inline] [popped] [toasted] [overlaid] [folded]
[paged] [laid] [drawing] [fading]. Its icon-only button,
its status icon and its drawing area have no accessible name unless
`fixed`, nor has the window with `untitled`, and `second` opens an
untitled second window; `notes` puts a text view first, which keeps Tab
(and prints its text as it changes), `trap` one that keeps ctrl+Tab,
shift+Tab, Escape and F6 too, and `dialog` that one in a dialog; `lists`
is lists and a notebook instead, `one` a single button, and
`unfocusable` that, and a button Tab can't reach;
`faint` adds a label too light to read and a disabled button, `thin` one in small monospace
spaced by a fraction of a pixel, whose stems are thinner than one, `long` is a scrolled
list running far past the window (scrolled 60 pixels down, `down`), and `keys`
buttons named as a calculator's are, which aren't regexes or match more than
themselves as one; `form` adds a field
named by a label with a mnemonic, `decoys` faint labels drawn otherwise
than their font says (letter-spaced, upper case), each over an entry
holding its text or showing it as a placeholder, or a drawing area
drawing it (not all ASCII), `lines` a button labeled over two lines,
`letter` a label of one letter, `carousel` a carousel at its second
page, the first's label outside the window, `nested` "check" over
"unchecked" (under "Recheck" with `recheck`), padded below as the
decoys are, `split` a hidden
"Open File" over "Open" and "File" side by side, padded so too;
`twin` a faint "Twin" over an entry holding it, in a box padded below
alone, with `under` under it, with `hidden` a hidden "Twin" over the
label; `over` a box padded above holding an entry holding "Twin", a
faint "Twin" laid over the box's top, all padded far below. With
`hidden`, `drawn` or `named`, the box holds a tall label "Twin"
instead, and what's laid over its top draws "Twin": a label, a drawing
area with `drawn`, or a label named "Brother" with `named`. With
`hidden`, a hidden "Twin" is laid over the box's label too; without,
the box's label is faint and an entry holding "Twin" is beside it all.
`lone`
an icon button and a faint letter-spaced label alone, `slow`
moves the focus a second after Tab, and `alert` says an error
and asks in an alert dialog as it starts, again once answered, then
holds a button that makes a toast; `freeze`, a button that stops it
answering for 30 seconds; `ready`, buttons "Behind" under a dialog,
"Soon" disabled, "Moving" moving and "Covered" under a label, until
"Ready" is clicked, each printing its name when clicked; with `late`, under
a window of the same title and size, which puts nothing in either
anywhere, for two seconds; `modal`, a modal window over it; `warned`, an
error label, an alert, revealed below the rest; `inline` a box with a
dialog's role below it, and `popped` a popover with one, open;
`toasted`, a faint label in a toast over a status page that's an alert,
alone, and `overlaid` one laid over a label that's an alert, alone. With
no header bar, `folded` puts it all under a banner folded away, `paged`
on a view stack's second page, and `laid` under a layer as big as it
holding an alert at its foot. `drawing` shows a label "Drawn: 4 tiles"
three seconds after it starts, and `fading` has the faint label darken
from its color to #242424 over 1.6 s, again and again.
The Announce button says something to a screen reader (GTK 4.14+)."""

import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, PangoCairo  # noqa: E402


def lists():
    """Containers that take the focus with no name of their own, which
    Tab passes through to their rows: a list box and a list view, each
    scrolled, in a notebook's page."""
    rows = Gtk.ListBox()
    for text in ("One", "Two"):
        rows.append(Gtk.Label(label=text))
    factory = Gtk.SignalListItemFactory()
    factory.connect("setup", lambda f, item: item.set_child(Gtk.Label()))
    factory.connect("bind", lambda f, item: item.get_child().set_label(item.get_item().get_string()))
    colors = Gtk.ListView(model=Gtk.SingleSelection(model=Gtk.StringList.new(["Red", "Green"])),
                          factory=factory)
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    for child in (rows, colors):
        page.append(Gtk.ScrolledWindow(child=child, vexpand=True))
    tabs = Gtk.Notebook()
    tabs.append_page(page, Gtk.Label(label="Lists"))
    tabs.append_page(Gtk.Label(label="Nothing"), Gtk.Label(label="Other"))
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(Gtk.Button(label="Save"))
    box.append(tabs)
    return box


def drawn(text):
    """A drawing area drawing `text`, which it has no text of its own for."""
    area = Gtk.DrawingArea(content_width=80, content_height=24)

    def draw(widget, cr, width, height):
        cr.set_source_rgb(0.1, 0.1, 0.1)
        PangoCairo.show_layout(cr, widget.create_pango_layout(text))
    area.set_draw_func(draw)
    return area


def activate(app):
    w = Adw.ApplicationWindow(application=app, title=None if "untitled" in sys.argv else "a11y-test")
    w.set_default_size(320, 240)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    save = Gtk.Button(label="Save")
    delete = Gtk.Button(icon_name="edit-delete-symbolic")
    status = Gtk.Image(icon_name="network-offline-symbolic")
    # A widget of its own, which Tab lands on: role generic (AT-SPI's
    # panel), which GTK gives no name, unless it's given a role that has one.
    fixed = "fixed" in sys.argv
    sketch = Gtk.DrawingArea(focusable=True, content_height=20, accessible_role=(
        Gtk.AccessibleRole.IMG if fixed else Gtk.AccessibleRole.GENERIC))
    if fixed:
        delete.set_tooltip_text("Delete")
        status.update_property([Gtk.AccessibleProperty.LABEL], ["Offline"])
        sketch.update_property([Gtk.AccessibleProperty.LABEL], ["Sketch"])
    announce = Gtk.Button(label="Announce")
    announce.connect("clicked", lambda b: b.announce(
        "Saved to notes.md", Gtk.AccessibleAnnouncementPriority.MEDIUM))
    wrap = Gtk.CheckButton(label="Wrap lines", active=True)
    # On its own, not in a control: every GTK 4 places that right.
    faint = Gtk.Label(label="Faint words", visible="faint" in sys.argv)
    # Dimmed, as a disabled button is: WCAG leaves it, and its label, out.
    later = Gtk.Button(label="Later", sensitive=False, visible="faint" in sys.argv)
    thin = Gtk.Label(label="CONTACT", visible="thin" in sys.argv)
    css = Gtk.CssProvider()
    css.load_from_string("label.faint { color: #a0a0a0; } label.thin { color: #5f5670;"
                         " font-family: monospace; font-size: 9.4px; letter-spacing: 0.37px; }"
                         " label.spaced { letter-spacing: 2px; }"
                         " label.upper { text-transform: uppercase; }"
                         " box.decoy { padding: 4px 4px 40px; } box.twin { padding: 0 0 40px; }"
                         " box.over { padding-top: 40px; } label.tall { min-height: 40px; }"
                         " box.far { padding-bottom: 100px; }")
    faint.add_css_class("faint")
    thin.add_css_class("thin")
    Gtk.StyleContext.add_provider_for_display(w.get_display(), css,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    entry = Gtk.Entry()
    entry.update_property([Gtk.AccessibleProperty.LABEL], ["File name"])
    # Decorative: no name, and left out of the tree.
    mark = Gtk.Image(icon_name="emblem-ok-symbolic",
                     accessible_role=Gtk.AccessibleRole.PRESENTATION)
    for widget in (save, delete, announce, wrap, entry, status, mark, sketch, faint, thin, later):
        box.append(widget)
    if "form" in sys.argv:
        name = Gtk.Entry()
        box.append(Gtk.Label.new_with_mnemonic("_Name:"))
        box.get_last_child().set_mnemonic_widget(name)
        box.append(name)
    if "decoys" in sys.argv:
        # Padded below, which GTK 4.14.6 and later lose, so where each
        # label can be takes in what's under it.
        for text, look, field in (("Search", "spaced", Gtk.Entry(text="Search")),
                                  ("Save", "upper", Gtk.Entry(text="Save")),
                                  ("Find", "upper", Gtk.SearchEntry(placeholder_text="Find")),
                                  ("Über", "upper", drawn("Über"))):
            pair = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["decoy"])
            pair.append(Gtk.Label(label=text, css_classes=["faint", look], halign=Gtk.Align.START))
            field.set_halign(Gtk.Align.START)
            pair.append(field)
            box.append(pair)
    if "lines" in sys.argv:
        box.append(Gtk.Button(label="Line one\nLine two"))
    if "letter" in sys.argv:
        box.append(Gtk.Label(label="x"))
    notes = Gtk.TextView()
    notes.get_buffer().set_text("notes")
    notes.get_buffer().connect("changed", lambda b: print(repr(b.props.text), flush=True))
    notes.update_property([Gtk.AccessibleProperty.LABEL], ["Notes"])
    if "trap" in sys.argv or "dialog" in sys.argv:
        # As an editor's own shortcuts can.
        keep = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keep.connect("key-pressed", lambda c, key, code, state: key in (
            Gdk.KEY_Escape, Gdk.KEY_F6, Gdk.KEY_ISO_Left_Tab) or (key == Gdk.KEY_Tab and bool(
                state & Gdk.ModifierType.CONTROL_MASK)))
        notes.add_controller(keep)
    if "notes" in sys.argv or "trap" in sys.argv:
        # A text view takes Tab for itself; GTK's ctrl+Tab leaves it.
        box.prepend(notes)
        notes.grab_focus()
    if "lists" in sys.argv:
        box = lists()
    if "long" in sys.argv:
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i in range(200):
            rows.append(Gtk.Label(label=f"Row {i}"))
        box = Gtk.ScrolledWindow(child=rows, vexpand=True)
        if "down" in sys.argv:
            GLib.timeout_add(300, lambda: box.get_vadjustment().set_value(60) and False)
    if "nested" in sys.argv:
        pair = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["decoy"])
        for text in ("Recheck", "check", "unchecked")[("recheck" not in sys.argv):]:
            pair.append(Gtk.Label(label=text, halign=Gtk.Align.START))
        box.append(pair)
    if "split" in sys.argv:
        pair = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["decoy"])
        pair.append(Gtk.Label(label="Open File", opacity=0, halign=Gtk.Align.START))
        side = Gtk.Box(spacing=3)
        for text in ("Open", "File"):
            side.append(Gtk.Label(label=text))
        pair.append(side)
        box.append(pair)
    if "twin" in sys.argv:
        pair = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["twin"])
        twin = Gtk.Overlay(child=Gtk.Label(label="Twin", css_classes=["faint"]),
                           halign=Gtk.Align.START)
        if "hidden" in sys.argv:
            twin.add_overlay(Gtk.Label(label="Twin", opacity=0))
        pair.append(twin)
        pair.insert_child_after(Gtk.Entry(text="Twin", halign=Gtk.Align.START),
                                None if "under" in sys.argv else twin)
        box.append(pair)
    if "over" in sys.argv:
        # GTK 4.14.6 and later say what's in the box is 40 px up, over
        # what's laid over its top.
        hidden = "hidden" in sys.argv
        drawing = "drawn" in sys.argv or "named" in sys.argv
        inside = Gtk.Box(css_classes=["over"], halign=Gtk.Align.START, valign=Gtk.Align.START)
        inside.append(Gtk.Label(label="Twin", css_classes=["tall"] + ([] if hidden else ["faint"]))
                      if hidden or drawing else Gtk.Entry(text="Twin"))
        over = Gtk.Overlay(child=inside, halign=Gtk.Align.START)
        top = drawn("Twin") if "drawn" in sys.argv else Gtk.Label(
            label="Twin", css_classes=[] if hidden or drawing else ["faint"])
        if "named" in sys.argv:
            top.update_property([Gtk.AccessibleProperty.LABEL], ["Brother"])
        top.set_halign(Gtk.Align.START)
        top.set_valign(Gtk.Align.START)
        over.add_overlay(top)
        if hidden:
            over.add_overlay(Gtk.Label(label="Twin", opacity=0, margin_top=40, css_classes=["tall"],
                                       halign=Gtk.Align.START, valign=Gtk.Align.START))
        pair = Gtk.Box(css_classes=["far"])
        if drawing and not hidden:
            pair.append(Gtk.Entry(text="Twin", valign=Gtk.Align.END, width_chars=4))
        pair.append(over)
        box.append(pair)
    if "carousel" in sys.argv:
        box = Adw.Carousel()
        for text in ("Before", "Shown"):
            box.append(Gtk.Label(label=text, hexpand=True, vexpand=True))
        GLib.timeout_add(300, lambda: box.scroll_to(box.get_nth_page(1), False) and False)
    if "keys" in sys.argv:
        box = Gtk.Box()
        for label in ("1", "2", ".", "+", "(Draft)"):
            box.append(Gtk.Button(label=label))
    view = Adw.ToolbarView(content=box)
    view.add_top_bar(Adw.HeaderBar())
    # One thing to take the focus: no header bar and its buttons either.
    if "one" in sys.argv or "unfocusable" in sys.argv:
        view = Gtk.Box()
        view.append(Gtk.Button(label="Save"))
        if "unfocusable" in sys.argv:
            view.append(Gtk.Button(label="Play", focusable=False))
    if "lone" in sys.argv:
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        view.append(Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text="Delete"))
        view.append(Gtk.Label(label="Spaced out", css_classes=["faint", "spaced"]))
    if "freeze" in sys.argv:
        view = Gtk.Button(label="Freeze")
        view.connect("clicked", lambda b: GLib.timeout_add(100, lambda: time.sleep(30)))
    if "ready" in sys.argv:
        w.set_default_size(320, 300)
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        clicked = {}
        for text in ("Behind", "Soon", "Moving", "Covered", "Ready"):
            clicked[text] = Gtk.Button(label=text)
            clicked[text].connect("clicked", lambda b: print(b.get_label(), flush=True))
        cover = Gtk.Overlay(child=clicked["Covered"])
        cover.add_overlay(Gtk.Label(label="Cover"))
        for widget in (clicked["Behind"], clicked["Soon"], clicked["Moving"], cover,
                       clicked["Ready"]):
            view.append(widget)
        clicked["Soon"].set_sensitive(False)
        moving = clicked["Moving"]
        ticking = GLib.timeout_add(20, lambda: moving.set_margin_start(
            (moving.get_margin_start() + 1) % 40) or True)
        dialog = Gtk.Window(title="Dialog", transient_for=w, default_width=320,
                            default_height=60)

        def ready(b):
            dialog.close()
            clicked["Soon"].set_sensitive(True)
            GLib.source_remove(ticking)
            cover.remove_overlay(cover.get_last_child())
        clicked["Ready"].connect("clicked", ready)
    if "toasted" in sys.argv:
        view = Adw.ToastOverlay(child=Adw.StatusPage(title="Could not connect",
                                                     accessible_role=Gtk.AccessibleRole.ALERT))
        view.add_toast(Adw.Toast(custom_title=Gtk.Label(label="Faint words",
                                                        css_classes=["faint"]), timeout=0))
    if "overlaid" in sys.argv:
        view = Gtk.Overlay(child=Gtk.Label(label="Offline", accessible_role=Gtk.AccessibleRole.ALERT))
        view.add_overlay(Gtk.Label(label="Faint words", css_classes=["faint"],
                                   halign=Gtk.Align.START, valign=Gtk.Align.START))
    if {"folded", "paged", "laid"} & set(sys.argv):
        view.set_content(None)
    if "folded" in sys.argv:
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        view.append(Adw.Banner(title="Offline", revealed=False))
        view.append(box)
        box.set_vexpand(True)
    if "paged" in sys.argv:
        view = Adw.ViewStack()
        view.add_named(Gtk.Label(label="First page"), "first")
        view.add_named(box, "second")
        view.set_visible_child_name("second")
    if "laid" in sys.argv:
        view = Gtk.Overlay(child=box)
        view.add_overlay(Gtk.Box())
        view.get_last_child().append(Gtk.Label(
            label="Lost connection", accessible_role=Gtk.AccessibleRole.ALERT,
            valign=Gtk.Align.END, hexpand=True))
    w.set_content(view)
    if "slow" in sys.argv:
        def later(c, key, code, state):
            if key != Gdk.KEY_Tab:
                return False
            GLib.timeout_add(1000, lambda: w.child_focus(Gtk.DirectionType.TAB_FORWARD) and False)
            return True
        slow = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        slow.connect("key-pressed", later)
        w.add_controller(slow)
    w.present()
    if "ready" in sys.argv:
        dialog.present()
    if "late" in sys.argv:
        twin = Adw.ApplicationWindow(application=app, title="a11y-test", default_width=320,
                                     default_height=300, content=Gtk.Label(label="twin"))
        twin.present()
        GLib.timeout_add(2000, lambda: twin.close() and False)
    if "dialog" in sys.argv:
        # Escape, a way out of a text view elsewhere, closes a dialog.
        inside = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inside.append(notes)
        inside.append(Gtk.Button(label="Send"))
        Adw.Dialog(child=inside, title="Reply").present(w)
        notes.grab_focus()
    if "alert" in sys.argv:
        # Said, then asked in an alert dialog, twice, then a toast.
        toasts = Adw.ToastOverlay(child=Gtk.Button(label="Toast"))
        toasts.get_child().connect("clicked", lambda b: toasts.add_toast(
            Adw.Toast(title="Saved to notes.md")))
        w.set_content(toasts)
        w.announce("Error: Name is taken, pick another",
                   Gtk.AccessibleAnnouncementPriority.MEDIUM)
        ask = Adw.AlertDialog(heading="Name is taken", default_response="ok")
        ask.add_response("ok", "OK")
        asked = [1]

        def again(d, response):
            if asked[0] < 2:
                asked[0] += 1
                GLib.idle_add(lambda: d.present(w))
        ask.connect("response", again)
        ask.present(w)
    if "inline" in sys.argv:
        box.append(Gtk.Box(accessible_role=Gtk.AccessibleRole.DIALOG))
        box.get_last_child().append(Gtk.Label(label="Find"))
    if "popped" in sys.argv:
        menu = Gtk.MenuButton(label="Menu", popover=Gtk.Popover(
            accessible_role=Gtk.AccessibleRole.DIALOG, child=Gtk.Label(label="In popover")))
        box.append(menu)
        GLib.timeout_add(300, lambda: menu.popup() and False)
    if "warned" in sys.argv:
        box.append(Gtk.Revealer(reveal_child=True, child=Gtk.Label(
            label="Wrong password", accessible_role=Gtk.AccessibleRole.ALERT)))
    if "fading" in sys.argv:
        fade, step = Gtk.CssProvider(), iter(range(10 ** 9))
        Gtk.StyleContext.add_provider_for_display(w.get_display(), fade,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
        GLib.timeout_add(50, lambda: fade.load_from_string(
            "label.faint { color: #%02x%02x%02x; }" % ((0xa0 - next(step) % 32 * 4,) * 3)) or True)
    if "drawing" in sys.argv:
        tiles = Gtk.Label(label="Drawn: 4 tiles", visible=False)
        box.append(tiles)
        GLib.timeout_add(3000, lambda: tiles.set_visible(True) and False)
    if "modal" in sys.argv:
        Gtk.Window(title="Over", modal=True, transient_for=w,
                   child=Gtk.Label(label="Over")).present()
    if "second" in sys.argv:
        other = Adw.ApplicationWindow(application=app)
        other.set_content(Gtk.Label(label="second"))
        other.present()


app = Adw.Application(application_id="org.shotbox.A11yTest")
app.connect("activate", activate)
app.run([])
