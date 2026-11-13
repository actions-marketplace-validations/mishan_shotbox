#!/usr/bin/env python3
"""a11y lint --from, on trees made by hand from what each rule says: no
display, accessibility bus or PyGObject (its import is blocked):
test/lint_test.py SHOTBOX."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sb = sys.argv[1]
fails = 0
tmp = Path(tempfile.mkdtemp())
(tmp / "gi").mkdir()
(tmp / "gi" / "__init__.py").write_text("raise ImportError('PyGObject blocked by lint_test.py')\n")


def check(ok, what):
    global fails
    print(("ok    " if ok else "FAIL  ") + what)
    fails += not ok


def node(path, role, name="", states=(), **more):
    return {"role": role, "name": name, "description": "", "states": list(states), "path": path,
            "depth": path.count("/"), **more}


def lint(nodes, *args, text=None):
    f = tmp / "tree.json"
    f.write_text(text if text is not None else json.dumps(nodes))
    r = subprocess.run([sb, "a11y", "lint", "--from", str(f), *args], capture_output=True,
                       text=True, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tmp)})
    return r.returncode, r.stdout.splitlines(), r.stderr


W = node("frame", "frame", "w", pid=7, toolkit="GTK 3.24.49")
WEB = [W, node("frame/document web[0]", "document web", "page")]
ICON = node("frame/document web[0]/button[0]", "button", states=["focusable"],
            dom={"tag": "button", "id": "icon"})

for case, nodes, args, want in [
    ("an unnamed button", [W, node("frame/button[0]", "button")], [],
     (1, ["frame/button[0]: button with no name"])),
    ("an unnamed push button, as libatspi before 2.54 says", [W, node("frame/push button[0]", "push button")],
     [], (1, ["frame/push button[0]: push button with no name"])),
    ("a named button", [W, node("frame/button[0]", "button", "Save")], [], (0, [])),
    ("an unnamed image", [W, node("frame/image[0]", "image")], [],
     (1, ["frame/image[0]: image with no name (a decorative one should be role presentation, "
          "which leaves it out)"])),
    ("an image in a named button", [W, node("frame/button[0]", "button", "Save"),
                                    node("frame/button[0]/image[0]", "image")], [], (0, [])),
    ("an unnamed field", [W, node("frame/text[0]", "text", states=["editable"])], [],
     (1, ["frame/text[0]: text with no name"])),
    ("unnamed text not to edit", [W, node("frame/text[0]", "text")], [], (0, [])),
    ("an unnamed focusable panel", [W, node("frame/panel[0]", "panel", states=["focusable"])], [],
     (1, ["frame/panel[0]: focusable panel with no name"])),
    ("a focusable panel said by what's in it", [W, node("frame/panel[0]", "panel", states=["focusable"]),
                                                node("frame/panel[0]/label[0]", "label", "Hi")],
     [], (0, [])),
    ("a list passing the focus on: only what it's passed to is said",
     [W, node("frame/list[0]", "list", states=["focusable"]),
      node("frame/list[0]/list item[0]", "list item", states=["focusable"])], [],
     (1, ["frame/list[0]/list item[0]: focusable list item with no name"])),
    ("a tab not drawn", [W, node("frame/page tab[0]", "page tab", drawn=False)], [], (0, [])),
    ("a web node, said by where it is in the page", WEB + [ICON], [],
     (1, ["frame/document web[0]/button[0]: button with no name  <- button#icon"])),
    ("ignored by its path", [W, node("frame/button[0]", "button")], ["--ignore", r"^frame/button\["],
     (0, [])),
    ("ignored by its tree line", [W, node("frame/button[0]", "button")],
     ["--ignore", r'^button "": button'], (0, [])),
    ("ignored by where it is in the page", WEB + [ICON], ["--ignore", "button#icon"], (0, [])),
    ("from a tree saved without where it is in the page, not ignored by it",
     WEB + [{k: v for k, v in ICON.items() if k != "dom"}], ["--ignore", "button#icon"],
     (1, ["frame/document web[0]/button[0]: button with no name"])),
]:
    got = lint(nodes, *args)
    check(got[:2] == want, f"lint --from: {case}" + ("" if got[:2] == want else f": {got}"))

st, out, err = lint([W, node("frame/button[0]", "button"),
                     node("application", "application", depth=0, pid=42, quiet=True, bounds=None)])
check(st == 2 and out == ["frame/button[0]: button with no name"]
      and err.endswith("shotbox: not all checked: program 42 didn't answer: busy, or hung\n"),
      f"lint --from: a program that didn't answer, not all checked ({st}, {out}, {err!r})")

st, out, err = lint([node("frame", "frame", states=["focusable"], pid=7, toolkit="GTK 3.24.49",
                          bounds=[0, 0, 10, 10])], "--json")
check((st, json.loads(out[0]) if out else None) == (0, [{
    "role": "frame", "name": "", "description": "", "states": ["focusable"], "path": "frame",
    "depth": 0, "bounds": [0, 0, 10, 10], "problem": "focusable frame with no name", "fails": True}]),
      f"lint --from --json: as the live one has it, a window without its pid or toolkit ({st}, {out})")

for case, text, said in [
    ("not JSON", "{", "isn't a tree saved by a11y tree --json"),
    ("not a tree", json.dumps([{"role": "frame"}]), "isn't a tree saved by a11y tree --json"),
    ("drawn, but not false", json.dumps([dict(W, drawn=True)]), "isn't a tree saved by a11y tree --json"),
    ("a dom with no tag", json.dumps([dict(W, dom={"id": "x"})]), "isn't a tree saved by a11y tree --json"),
    ("text, but not a string", json.dumps([dict(W, text=3)]), "isn't a tree saved by a11y tree --json"),
]:
    st, out, err = lint(None, text=text)
    check(st == 2 and not out and said in err and "Traceback" not in err,
          f"lint --from: {case}, said ({st}, {err!r})")

r = subprocess.run([sb, "a11y", "lint", "--from", str(tmp / "none.json")], capture_output=True, text=True)
check(r.returncode == 2 and "can't read" in r.stderr, f"lint --from: a file that isn't there ({r.stderr!r})")
r = subprocess.run([sb, "a11y", "lint", "--from", str(tmp / "tree.json"), "--window", "w"],
                   capture_output=True, text=True)
check(r.returncode == 2 and "takes no --window" in r.stderr, f"lint --from: no --window ({r.stderr!r})")

sys.exit(bool(fails))
