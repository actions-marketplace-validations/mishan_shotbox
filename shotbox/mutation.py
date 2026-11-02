"""shotbox mutate: run a test against small changes to the page it
tests, to find a test that fails on a change that shouldn't matter
(brittle) or passes on one that should (blind).
"""

import dataclasses
import inspect
import itertools
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .screen import SessionError
from .session import Session, kill_group

# Whether each kind of change should fail a test that checks what it's for.
MATTERS = {"size": False, "move": False, "hide": True, "dead": True, "number": True}

FORMS = 'ROLE "NAME", ROLE /RE/, text:"TEXT", text:/RE/ or css:SELECTOR'
NAMED = re.compile(r'("(?:[^"\\]|\\.)*")|/(.*)/')


def target(spec):
    """What a --target names, for the page to find: an ARIA role and its
    exact name, or a JavaScript regex its name matches in full; text the
    same ways; or a CSS selector. The page checks the regex, being the
    one to read it."""
    spec = spec.strip()
    if spec.startswith("css:"):
        if not spec[4:].strip():
            raise SessionError(f"target {spec!r}: its selector is empty")
        return {"by": "css", "css": spec[4:]}
    by, role, rest = ("text", None, spec[5:]) if spec.startswith("text:") else (
        "role", *(spec.split(" ", 1) if " " in spec else (spec, "")))
    m = NAMED.fullmatch(rest)
    if not m or (role is not None and not re.fullmatch(r"[a-z]+", role)):
        raise SessionError(f"target {spec!r} isn't one of {FORMS}")
    try:
        name = json.loads(m[1]) if m[1] else None
    except ValueError as e:
        raise SessionError(f"target {spec!r}: its name isn't a JSON string: {e}") from None
    if not (name.strip() if m[1] else m[2]):
        raise SessionError(f"target {spec!r}: its name is empty")
    return {"by": by, **({"role": role} if role else {}),
            **({"name": name} if m[1] else {"re": m[2]})}


def verdict(kind, ended, proven, confirmed=None):
    """What a run says about the test: `ended` is pass, fail or timeout;
    `proven`, whether the page showed the change was made; `confirmed`,
    for a failure on a change that shouldn't matter, whether it failed
    again on a rerun while a clean rerun passed."""
    if not proven:
        return "UNAPPLIED"
    if MATTERS[kind]:
        return {"pass": "BLIND", "fail": "caught", "timeout": "caught (timeout)"}[ended]
    if ended == "pass":
        return "robust"
    return "BRITTLE" if ended == "fail" and confirmed else "inconclusive"


# The sizes `size` runs the page at, one run each.
SIZES = ((1024, 768), (1920, 1080))
ON_TARGETS = ("move", "hide", "dead", "number")


class Unusable(SessionError):
    """The clean runs say nothing can be learned from changed ones."""


@dataclasses.dataclass
class Run:
    label: str
    spec: dict
    ended: str = ""
    seconds: float = 0.0
    proven: bool = False
    why: str = ""
    verdict: str = ""


@dataclasses.dataclass
class MutationReport:
    runs: list
    clean: list
    control: str
    suspect: bool
    strict: bool = False

    def _with(self, *verdicts):
        return [r for r in self.runs if r.verdict in verdicts]

    @property
    def brittle_runs(self):
        return self._with("BRITTLE")

    @property
    def blind_runs(self):
        return self._with("BLIND")

    @property
    def unapplied_runs(self):
        return self._with("UNAPPLIED")

    @property
    def status(self):
        """0 with nothing brittle or blind, else 1; with `strict`,
        UNAPPLIED counts too."""
        return int(bool(self.brittle_runs or self.blind_runs or self.strict and self.unapplied_runs))


def looking(r, kind):
    """Whether report `r` had the change made and still being looked for,
    not fought and no copy found showing (and a dead target used): what a
    test that fails then failed on."""
    return bool(r and r.get("made") is True and r.get("looking") is True and not r.get("fought")
                and not r.get("shown") and (kind != "dead" or r.get("used") is True))


def merged(reports, kind=None):
    """The report of a test whose processes each wrote one: a target any
    saw was seen, and a change any proved is proven, unless one found the
    page fighting it or the target still showing; else the why of one
    that made the change, and before that, one still looking for it.
    What any page couldn't read of a target, by its index, is kept."""
    reports = [r for r in reports if isinstance(r, dict)]
    if not reports:
        return None
    if all(isinstance(r.get("seen"), list) for r in reports):
        bad = {k: v for r in reports if isinstance(r.get("bad"), dict) for k, v in r["bad"].items()}
        return {"seen": [any(i < len(r["seen"]) and r["seen"][i] is True for r in reports)
                         for i in range(max(len(r["seen"]) for r in reports))],
                **({"bad": bad} if bad else {})}
    for mark in ("fought", "shown", "proven"):
        for r in reports:
            if r.get(mark) is True:
                return r
    for r in reports:
        if looking(r, kind):
            return r
    for r in reports:
        if r.get("made") is True:
            return r
    return reports[0]


def plan(targets, kinds, only=None):
    """The changed runs, in order: the sizes, then each target's kinds;
    with `only`, the one it names."""
    bad = [k for k in kinds if k not in MATTERS]
    if bad:
        raise SessionError(f"no kind {bad[0]!r}: they are {', '.join(MATTERS)}")
    runs = [Run(f"size {w}x{h}", {"kind": "size", "screen": [w, h]})
            for w, h in SIZES if "size" in kinds]
    for spec in targets:
        runs += [Run(f"{k} {spec}", {"kind": k, "target": target(spec)})
                 for k in ON_TARGETS if k in kinds]
    if only is not None:
        runs = [r for r in runs if r.label == only]
        if not runs:
            raise SessionError(f"{only!r} isn't one of the runs these targets and kinds make")
    if not runs:
        raise SessionError("nothing to change: give a --target, or size in --kinds")
    return runs


def mutate(cmd, targets, kinds=None, confirm=1, timeout=None, only=None, save=None,
           strict=False, out=None, _new_session=None, **session_options):
    """Run `cmd`, a test that calls the page helper `mutated()`, twice
    clean, then once for each change to each of `targets`, each in a
    session of its own (`session_options`, as Session takes them), and
    once more clean at the end; a MutationReport. Raises Unusable if the
    clean runs fail, write no report, or never find a target."""
    kinds = kinds.split(",") if isinstance(kinds, str) else kinds
    if confirm < 1:
        raise SessionError(f"confirm={confirm}: a BRITTLE needs at least one rerun to confirm it")
    if timeout is not None and not timeout > 0:
        raise SessionError(f"timeout {timeout:g}: a run needs more than 0 seconds")
    if not isinstance(cmd, (list, tuple)) or not cmd:
        raise SessionError(f"cmd {cmd!r}: give a list, the program and its arguments")
    try:
        inspect.signature(Session).bind(**session_options)
    except TypeError as e:
        raise SessionError(f"session options: {e}") from None
    runs = plan(targets, list(MATTERS) if kinds is None else kinds, only)
    if save and Path(save).exists() and any(Path(save).iterdir()):
        raise SessionError(f"--save {save}: it has files in it already, which a run's "
                           "own could be mistaken for")
    say = (lambda s: print(s, file=out, flush=True)) if out else (lambda s: None)
    new_session = _new_session or (lambda: Session(**session_options))
    # Absolute, for a test that changes directory before it reports.
    work = Path(save).resolve() if save else Path(tempfile.mkdtemp(prefix="shotbox-mutate-"))
    count = itertools.count(1)
    clean = {"kind": None, "targets": [target(t) for t in targets]}

    def go(label, spec, limit):
        n = next(count)
        where = work / f"{n:02d}-{re.sub(r'[^A-Za-z0-9.]+', '-', label).strip('-')}"
        where.mkdir(parents=True)
        reports = where / "reports"
        reports.mkdir()
        with new_session() as s:
            s.env.update(SHOTBOX_MUTATION=json.dumps(spec), SHOTBOX_MUTATION_REPORT=str(reports))
            start = time.monotonic()
            proc = s.spawn(cmd, log=where / "log")
            try:
                ended = "pass" if proc.wait(timeout=limit) == 0 else "fail"
            except subprocess.TimeoutExpired:
                ended = "timeout"
            finally:
                took = time.monotonic() - start
                kill_group(proc)
        got = []
        for p in sorted(reports.iterdir()):
            try:
                got.append(json.loads(p.read_text()))
            except (OSError, ValueError):
                pass
        return ended, took, merged(got, spec.get("kind")), where / "log"

    try:
        say(f"shotbox mutate: {len(runs)} run{'s' * (len(runs) != 1)}, plus 3 clean")
        firsts = [go("clean", clean, timeout) for _ in range(2)]
        say("clean      " + "  ".join(e for e, *_ in firsts)
            + f"  ({max(t for _, t, *_ in firsts):.1f} s)")
        for ended, _, got, log in firsts:
            if ended != "pass":
                raise Unusable("the test fails with no change, so changes can't be judged"
                               + tail(log))
            if got is None:
                raise Unusable("the test never called mutated(), or it wrote no report: "
                               "with no proof of what changed, nothing can be judged")
        unread = {k: v for _, _, g, _ in firsts for k, v in g.get("bad", {}).items()}
        for i, spec in enumerate(targets):
            if str(i) in unread:
                raise Unusable(f"{spec} isn't one the page can read: {unread[str(i)]}")
            if not all(isinstance(g.get("seen"), list) and g["seen"][i:i + 1] == [True]
                       for _, _, g, _ in firsts):
                raise Unusable(f"the clean runs didn't both find {spec}"
                               + (" (a regex matches all of the name or text, not a part)"
                                  if "re" in target(spec) else ""))
        limit = timeout or max(30.0, 3 * max(t for _, t, *_ in firsts))
        for r in runs:
            r.ended, r.seconds, got, _ = go(r.label, r.spec, limit)
            r.proven, r.why = bool(got and got.get("proven")), str((got or {}).get("why", ""))
            # A test that fails on a change that should matter, while it's
            # still looked for, failed on it, though it died first.
            died = (not r.proven and looking(got, r.spec["kind"]) and r.ended != "pass"
                    and MATTERS[r.spec["kind"]])
            if died:
                r.why = "the change was made; the test failed before it was proven"
            confirmed, notes = None, [r.why] if r.why else []
            if r.ended == "fail" and r.proven and not MATTERS[r.spec["kind"]]:
                again = [go(r.label, r.spec, limit) for _ in range(confirm)]
                notes += [f"rerun {said(e)}" for e, *_ in again]
                confirmed = all(e == "fail" and g and g.get("proven") for e, _, g, _ in again)
                if confirmed:
                    e = go("clean", clean, limit)[0]
                    notes.append(f"clean rerun {said(e)}")
                    confirmed = e == "pass"
            r.verdict = verdict(r.spec["kind"], r.ended, r.proven or died, confirmed)
            say(f"{r.label:<30}  {said(r.ended):<7}  {r.verdict}"
                + (f"  ({', '.join(notes)})" if notes else ""))
        control = go("control", clean, limit)[0]
        rep = MutationReport(runs, [e for e, *_ in firsts], control, control != "pass", strict)
        say(f"control    {said(control)}"
            + ("  (findings are suspect: something outside the session, files or "
               "the network, changed between runs)" if rep.suspect else ""))
        say(f"{len(rep.brittle_runs) + len(rep.blind_runs)} brittle or blind of {len(runs)}; "
            f"{len(rep.unapplied_runs)} unapplied")
        return rep
    finally:
        if not save:
            shutil.rmtree(work, ignore_errors=True)


def said(ended):
    """How a run ended, as the report says it: what went wrong in capitals."""
    return ended if ended == "pass" else ended.upper()


def tail(path, n=10):
    """What a log ends with, said after a colon, or that it's empty."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()[-n:]
    except OSError:
        lines = []
    return ":\n" + "\n".join(lines) if lines else ", and says nothing"
