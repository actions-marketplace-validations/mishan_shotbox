#!/usr/bin/env python3
"""A plain GTK 4 window, with no libadwaita, for GTK 4 before 4.14:
test/gtk4_window.py. In frames, faint labels: one GTK can't pick
(can-target off), one under an overlay's clear child, and one in a menu
button's popover, open below the rest; and a stack shown at its first
page, "Gone", then its second, whose "Here" is at the other end."""

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
    here = Gtk.Label(label="Here", halign=Gtk.Align.END)
    stack.add_named(here, "here")
    box.append(stack)
    box.append(menu)
    w.set_child(Gtk.Frame(child=Gtk.Frame(child=box, margin_top=9, margin_start=9)))
    w.present()

    def later():
        stack.set_visible_child(here)
        menu.popup()
    GLib.timeout_add(300, later)


app = Gtk.Application(application_id="org.shotbox.Gtk4Old")
app.connect("activate", activate)
app.run([])
