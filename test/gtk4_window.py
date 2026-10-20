#!/usr/bin/env python3
"""A plain GTK 4 window, with no libadwaita: test/gtk4_window.py. In
frames, faint labels: one GTK can't pick (can-target off), one under an
overlay's clear child, and one in a menu button's popover, open below
the rest; and a stack shown at its first page, "Gone", then its second,
whose faint "Here" is at the other end. `glyphs
TEXT|FONT|PX|WEIGHT|COLOR|BACKGROUND[|SHADOW]...` is a window of those
labels instead."""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402


def faint(text, **props):
    label = Gtk.Label(**props)
    label.set_markup(f'<span foreground="#b0b0b0">{text}</span>')
    return label


def activate(app):
    w = Gtk.ApplicationWindow(application=app, title="gtk4-old")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    menu = Gtk.MenuButton(label="Save", halign=Gtk.Align.START,
                          popover=Gtk.Popover(child=faint("Popped")))
    box.append(faint("Faint words"))
    box.append(faint("Untouchable", can_target=False))
    over = Gtk.Overlay(child=faint("Underneath"))
    over.add_overlay(Gtk.Box())
    box.append(over)
    stack = Gtk.Stack()
    stack.add_named(Gtk.Label(label="Gone", halign=Gtk.Align.START), "gone")
    here = faint("Here", halign=Gtk.Align.END)
    stack.add_named(here, "here")
    box.append(stack)
    box.append(menu)
    w.set_child(Gtk.Frame(child=Gtk.Frame(child=box, margin_top=9, margin_start=9)))
    w.present()

    def later():
        stack.set_visible_child(here)
        menu.popup()
    GLib.timeout_add(300, later)


def glyphs(app):
    w = Gtk.ApplicationWindow(application=app, title="gtk4-glyphs")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    css = ""
    for i, case in enumerate(sys.argv[2:]):
        text, family, px, weight, color, back, *shadow = case.split("|")
        box.append(Gtk.Label(label=text, halign=Gtk.Align.START, css_classes=[f"g{i}"]))
        css += (f"label.g{i} {{ font-family: {family}; font-size: {px}px; font-weight: {weight};"
                f" color: {color}; background: {back}; padding: 6px 12px;"
                f" text-shadow: {shadow[0] if shadow else 'none'}; }}\n")
    provider = Gtk.CssProvider()
    provider.load_from_string(css)
    Gtk.StyleContext.add_provider_for_display(w.get_display(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    w.set_child(box)
    w.present()


app = Gtk.Application(application_id="org.shotbox.Gtk4Old")
app.connect("activate", glyphs if sys.argv[1:2] == ["glyphs"] else activate)
app.run([])
