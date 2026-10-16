#!/usr/bin/env python3
"""A tiny GTK 4 app for the README's demo, with two accessibility bugs for
shotbox to find: an icon button with no name, and faint status text."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

CSS = ".status { color: #a8a8a8; }"

TOOLS = (
    ("document-edit-symbolic", "Draw"),
    ("insert-text-symbolic", "Text"),
    ("edit-undo-symbolic", "Undo"),
    ("edit-redo-symbolic", "Redo"),
    ("edit-delete-symbolic", None),
)


def tool(icon, name):
    button = Gtk.Button(icon_name=icon)
    button.add_css_class("flat")
    if name:
        button.update_property([Gtk.AccessibleProperty.LABEL], [name])
    return button


def draw(area, cr, width, height):
    for x, y, r, g, b in ((30, 50, 0.21, 0.52, 0.89), (100, 100, 0.20, 0.82, 0.48),
                          (55, 160, 0.96, 0.76, 0.07)):
        cr.set_source_rgb(r, g, b)
        cr.rectangle(x, y, 90, 70)
        cr.fill()


def activate(app):
    css = Gtk.CssProvider()
    css.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 800)

    bar = Gtk.Box(spacing=2, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
    for icon, name in TOOLS:
        bar.append(tool(icon, name))
    canvas = Gtk.DrawingArea(vexpand=True)
    canvas.set_draw_func(draw)
    status = Gtk.Label(label="3 shapes, saved", xalign=0, margin_start=12,
                       margin_top=6, margin_bottom=8)
    status.add_css_class("status")

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    for child in (bar, Gtk.Separator(), canvas, Gtk.Separator(), status):
        body.append(child)
    Gtk.ApplicationWindow(application=app, title="Sketchpad", child=body,
                          titlebar=Adw.HeaderBar(decoration_layout=":close"),
                          default_width=224, default_height=480).present()


app = Adw.Application(application_id="org.example.Sketchpad")
app.connect("activate", activate)
app.run()
