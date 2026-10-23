"""The Python API, the pointer, and parking it: a GTK button, hovered,
clicked, then not hovered, on X11 and, with sway and grim, on Wayland.
Then the accessible tree of a libadwaita window, with GTK 4, libadwaita
and at-spi2-core. Run by test/run.sh; prints ok/FAIL lines like it."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import shotbox  # noqa: E402
from shotbox import x  # noqa: E402

BUTTON = """
import sys, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
w = Gtk.Window(title="shotbox-button")
w.set_default_size(200, 120)
w.move(0, 0)
b = Gtk.Button(label="hover me")
b.connect("clicked", lambda b: open(sys.argv[1], "a").write("clicked\\n"))
w.add(b)
w.connect("destroy", Gtk.main_quit)
w.show_all()
Gtk.main()
"""

fails = 0


def check(cond, what):
    global fails
    print(("ok    " if cond else "FAIL  ") + what)
    fails += not cond


tmp = Path(tempfile.mkdtemp())


def pointer(on, **session):
    """Hover, click and park, in a session made with `session`."""
    d = tmp / on
    d.mkdir()
    with shotbox.Session(size=(640, 480), **session) as s:
        s.spawn([sys.executable, "-c", BUTTON, str(d / "clicks")])
        w = s.wait_window("shotbox-button")
        check(w[2] > 1 and w[3] > 1, f"{on}: a Session waits for a window, and says where it is")
        s.move(630, 470)
        s.wait_stable(window="shotbox-button")
        s.capture(d / "rest.png", window="shotbox-button")
        s.move(w[2] // 2, w[3] // 2, window="shotbox-button")
        s.wait_stable(window="shotbox-button")
        s.capture(d / "hover.png", window="shotbox-button")
        check(x.compare(d / "rest.png", d / "hover.png") > 0, f"{on}: hovering the button shows")
        s.click(w[2] // 2, w[3] // 2, window="shotbox-button")
        s.until("the click", lambda: (d / "clicks").exists(), timeout=5)
        check((d / "clicks").read_text() == "clicked\n", f"{on}: a click reaches the button")
        s.capture(d / "parked.png", window="shotbox-button", park=True)
        check(x.compare(d / "rest.png", d / "parked.png") == 0,
              f"{on}: capture(park=True) takes it as if never hovered")
        subprocess.run(["convert", d / "parked.png", "-crop", "60x30+70+45", "+repage",
                        d / "part.png"], check=True)
        # Where it matched: 0,0 without a crop, the crop's corner, or where a margin found it.
        for ref, how, at in (("parked", {}, (0, 0)), ("part", {"crop": "60x30+70+45"}, (70, 45)),
                             ("part", {"crop": "60x30+73+43", "margin": 3}, (70, 45))):
            m = s.wait_match(d / f"{ref}.png", window="shotbox-button", timeout=5, **how)
            check(m.seconds >= 0 and (m.x, m.y) == at,
                  f"{on}: wait_match {how}: a Match, at {at} ({m})")
        (d / "script").write_text("click ${X} ${Y} --window shotbox-button\n+0.5 park --settle 0\n")
        s.play(d / "script", vars={"X": w[2] // 2, "Y": w[3] // 2})
        check((d / "clicks").read_text() == "clicked\n" * 2,
              f"{on}: play() a script's file, with vars")
        try:
            s.play("""
park
click 1 1 --window ${NOPE}""", name="$EXPLORE")
            check(False, f"{on}: play() text, an unset name raising")
        except shotbox.SessionError as e:
            check(str(e) == "$EXPLORE:2: ${NOPE} isn't set",
                  f"{on}: play() text, an unset name raising, by its name and line ({e})")
        s.failed = str(d / "failed.png")
        try:
            s.wait_window("never", timeout=0.5)
            check(False, f"{on}: a failed wait raises")
        except shotbox.SessionError as e:
            check("failed.png" in str(e) and (d / "failed.png").exists(),
                  f"{on}: a failed wait raises, with a picture of the screen")


pointer("x11")
if shutil.which("sway") and shutil.which("grim"):
    pointer("wayland", wayland=True)
elif os.environ.get("SHOTBOX_WAYLAND") == "required":
    check(False, "wayland: the pointer, skipped: no sway or grim")
else:
    print("skip  wayland: the pointer: no sway or grim")

APP = Path(__file__).resolve().parent / "a11y_app.py"


def a11y():
    with shotbox.Session(size=(640, 480), a11y=True) as s:
        s.spawn([str(APP)])
        # "push button" until at-spi2-core 2.54, "button" since.
        roles = sorted(n.role for n in s.find(name="Save", window="a11y-test"))
        button = [r for r in roles if r != "label"]
        check(len(roles) == 2 and "label" in roles and button[0] in ("button", "push button"),
              f"a11y: find() by name ({roles})")
        check(len(s.find(role=button[0], name="Sa.e")) == 1, "a11y: find() by role and name")
        check(s.focused().name == "Save", "a11y: focused()")
        walk = [n.name for n in s.tab_walk(window="a11y-test")]
        check(walk == ["", "Announce", "Wrap lines", "File name", "", "Save"],
              f"a11y: tab_walk() goes round ({walk})")
        save = s.find(role=button[0], name="Save")[0]
        check(save.bounds[2] > 0 and save.bounds[3] > 0, f"a11y: a node's bounds ({save.bounds})")
        problems = s.a11y_lint(window="a11y-test", ignore=[r"image\[", "panel with"])
        check([n.problem for n in problems] == [f"{button[0]} with no name"],
              f"a11y: a11y_lint(), ignoring some ({[n.problem for n in problems]})")
        got = s.contrast(window="a11y-test", passes=True)
        check(not got["problems"] and got["notes"] and got["passes"]
              and all("reports where" in n.get("why", "") for n in got["notes"]),
              f"a11y: contrast() on GTK 4, its text found and passing, the rest said why "
              f"({[n.problem for n in got['problems']]}, {[n.note for n in got['notes']]})")
        try:
            s.tab_walk(window="a11y-test", visible=True)
            check(False, "a11y: tab_walk(visible=True) raises where the focus doesn't show")
        except shotbox.SessionError as e:
            check('panel ""' in str(e),
                  "a11y: tab_walk(visible=True) raises where the focus doesn't show")
        s.focus_window("a11y-test")
        s.key("Tab", "Tab")
        check(s.wait_focus("Announce", timeout=5).role.endswith("button"),
              "a11y: wait_focus()")
        check(s.wait_node("^Wrap", window="a11y-test", timeout=5).name == "Wrap lines",
              "a11y: wait_node()")
        try:
            s.wait_node("")
            check(False, "a11y: wait_node() with an empty regex raises")
        except shotbox.SessionError as e:
            check("needs a regex" in str(e), "a11y: wait_node() with an empty regex raises")
        s.key("space")
        s.wait_announce("notes", timeout=5)
        check(s.click_node("Announce", window="a11y-test").name == "Announce",
              "a11y: click_node()")
        s.wait_announce("notes", timeout=5)
        try:
            s.wait_announce("notes", timeout=1)
            check(False, "a11y: wait_announce() takes each announcement once")
        except shotbox.SessionError:
            check(True, "a11y: wait_announce() takes each announcement once")
        # Off the screen's corner, where the window's x,y and the screen's differ.
        s.run(["xdotool", "search", "--name", "^a11y-test$", "windowmove", "--sync", "230", "170"])
        x, y, w, h = next(n for n in s.find(name="File name", window="a11y-test")
                          if "focusable" in n.states).bounds
        s.click(x + w // 2, y + h // 2, window="a11y-test")
        check(s.wait_focus("File name", timeout=5)
              and s.a11y_tree("a11y-test")[0].bounds[:2] == [0, 0],
              "a11y: with a window, bounds are in its x,y, as click() takes them")
    with shotbox.Session(size=(640, 480), a11y=True) as s:
        s.spawn([str(APP), "trap"])
        try:
            s.tab_walk(window="a11y-test")
            check(False, "a11y: tab_walk() on a focus trap raises")
        except shotbox.SessionError as e:
            check('"Notes"' in str(e), "a11y: tab_walk() on a focus trap raises, saying where")
    with shotbox.Session() as s:
        try:
            s.a11y_tree()
            check(False, "a11y: without a11y=True, the tree raises")
        except shotbox.SessionError as e:
            check("--a11y" in str(e), "a11y: without a11y=True, the tree raises, saying why")


def a11y_missing():
    """What a11y() needs and this machine lacks, or None."""
    import gi
    try:
        gi.require_version("Adw", "1")
        gi.require_version("Atspi", "2.0")
    except ValueError as e:
        return str(e)
    from shotbox.session import A11Y_LAUNCHERS
    if not any(os.access(p, os.X_OK) for p in A11Y_LAUNCHERS):
        return "no at-spi-bus-launcher"
    return None


missing = a11y_missing()
if not missing:
    try:
        a11y()
    except shotbox.SessionError as e:
        check(False, f"a11y: {e}")
elif os.environ.get("SHOTBOX_A11Y") == "required":
    check(False, f"a11y skipped: {missing}")
else:
    print(f"skip  a11y: {missing}")

here = subprocess.run(
    [sys.argv[1], "run", "--env", f"PYTHONPATH={Path(__file__).resolve().parent.parent}", "--",
     sys.executable, "-c", "import shotbox; s = shotbox.here(); print(s.xt.width, s.xt.height)"],
    capture_output=True, text=True).stdout.strip()
check(here == "1280 800", f"here() is the display it's inside ({here})")
sys.exit(1 if fails else 0)
