#!/usr/bin/env python3
"""shotbox mutate's targets and verdicts, which need no display:
test/mutate_test.py."""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shotbox.mutation import merged, mutate, target, verdict  # noqa: E402
from shotbox.screen import SessionError  # noqa: E402

fails = 0


def check(ok, what):
    global fails
    print(("ok    " if ok else "FAIL  ") + what)
    fails += not ok


for spec, want in [
    ('button "Pay"', {"by": "role", "role": "button", "name": "Pay"}),
    ('link "Say \\"hi\\""', {"by": "role", "role": "link", "name": 'Say "hi"'}),
    ("button /Pay( now)?/", {"by": "role", "role": "button", "re": "Pay( now)?"}),
    ('button "Pay" ', {"by": "role", "role": "button", "name": "Pay"}),
    ("button / /", {"by": "role", "role": "button", "re": " "}),
    ('text:"Total: 42.50"', {"by": "text", "name": "Total: 42.50"}),
    ("text:/Total: .*/", {"by": "text", "re": "Total: .*"}),
    ("css:#pay > span", {"by": "css", "css": "#pay > span"}),
]:
    got = target(spec)
    check(got == want, f"target {spec!r}: {got}")

for spec, why in [
    ("Pay", "ROLE"),
    ('"Pay"', "ROLE"),
    ("button Pay", "ROLE"),
    ('button "Pay', "ROLE"),
    ("button //", "empty"),
    ('button ""', "empty"),
    ('button "   "', "empty"),
    ('button "\\x41"', "isn't"),
    ('button "tab\there"', "isn't"),
    ("css:", "empty"),
    ("text:", "ROLE"),
    ('Button "Pay"', "ROLE"),
]:
    try:
        got = target(spec)
    except SessionError as e:
        check(why in str(e), f"target {spec!r} refused: {e}")
    else:
        check(False, f"target {spec!r} refused, not {got}")

# Every cell: (kind, how the test ended, whether the change was proven,
# whether a failure held up on a rerun while a clean rerun passed).
for kind, ended, proven, confirmed, want in [
    ("move", "pass", True, None, "robust"),
    ("move", "fail", True, True, "BRITTLE"),
    ("move", "fail", True, False, "inconclusive"),
    ("move", "timeout", True, None, "inconclusive"),
    ("move", "timeout", True, True, "inconclusive"),
    ("size", "fail", True, True, "BRITTLE"),
    ("hide", "pass", True, None, "BLIND"),
    ("hide", "fail", True, None, "caught"),
    ("hide", "timeout", True, None, "caught (timeout)"),
    ("number", "pass", True, None, "BLIND"),
    ("dead", "fail", True, None, "caught"),
    *((k, e, False, None, "UNAPPLIED") for k in ("move", "hide") for e in ("pass", "fail", "timeout")),
]:
    got = verdict(kind, ended, proven, confirmed)
    check(got == want, f"{kind}, {ended}, {'proven' if proven else 'unproven'}"
          f"{'' if confirmed is None else ', confirmed' if confirmed else ', unconfirmed'}: {got}")

# What each of a test's processes said, as one: seen or proven by any is
# so, unless one found the page fighting the change; a why from one that
# made the change before one that never saw the target.
GONE = {"proven": False, "made": True, "why": "gone"}
NEVER = {"proven": False, "why": "never seen"}
for what, reports, want in [
    ("none", [], None),
    ("not reports", [[True], "x", None], None),
    ("one", [{"seen": [True, False]}], {"seen": [True, False]}),
    ("seen by either", [{"seen": [True, False]}, {"seen": [False, True]}], {"seen": [True, True]}),
    ("seen, but not as true", [{"seen": ["yes", 1]}], {"seen": [False, False]}),
    ("proven by one", [NEVER, {"proven": True, "why": ""}, GONE], {"proven": True, "why": ""}),
    ("fought by one", [{"proven": True, "why": ""}, {"proven": False, "fought": True, "why": "f"}],
     {"proven": False, "fought": True, "why": "f"}),
    ("still showing for one", [{"proven": True, "why": ""}, {"proven": False, "shown": True, "why": "s"}],
     {"proven": False, "shown": True, "why": "s"}),
    ("made by one", [NEVER, GONE], GONE),
    ("made by none", [NEVER, NEVER], NEVER),
]:
    got = merged(reports)
    check(got == want, f"merged, {what}: {got}")

# Targets over random input: a role and any name with something in it, as
# JSON, comes back as given; anything at all is a target or refused as one.
rng = random.Random(1)
CHARS = 'ab Z09 "\\/:\t\u00e9\u4e2d'
for i in range(20000):
    role = "".join(rng.choice("abcxyz") for _ in range(rng.randint(1, 8)))
    name = "".join(rng.choice(CHARS) for _ in range(rng.randint(0, 10)))
    spec = f"{role} {json.dumps(name)}"
    try:
        got = target(spec)
        ok = name.strip() and got == {"by": "role", "role": role, "name": name}
    except SessionError:
        ok = not name.strip()
    if not ok:
        check(False, f"target {spec!r}: {got if name.strip() else 'taken'}")
        break
    junk = "".join(rng.choice(CHARS + "css:text/") for _ in range(rng.randint(0, 16)))
    try:
        target(junk)
    except SessionError:
        pass
    except Exception as e:
        check(False, f"target {junk!r} raised {e!r}")
        break
else:
    check(True, "targets over random input")

# What can't be run is refused before any session starts: a BRITTLE needs
# a rerun to confirm it, a run some time, a session options it has, and
# the test a list of its arguments.
PAY = ['button "Pay"']
for said, call in [
    ("confirm=0", lambda: mutate(["true"], PAY, confirm=0)),
    ("timeout 0", lambda: mutate(["true"], PAY, timeout=0)),
    ("timeout -1", lambda: mutate(["true"], PAY, timeout=-1)),
    ("screen", lambda: mutate(["true"], PAY, screen="1024x768")),
    ("a list", lambda: mutate("node t.mjs", PAY)),
    ("a list", lambda: mutate([], PAY)),
]:
    try:
        call()
        check(False, f"{said}: taken")
    except SessionError as e:
        check(said in str(e), f"{said}: refused: {e}")
    except Exception as e:
        check(False, f"{said}: {e!r}")

sys.exit(fails)
