"""Sessions that last: started by one command, used by the next, stopped
by a third, rather than living as long as one `shotbox run`.

    shotbox start --name ed [session options]   start one, and say its name
    shotbox spawn --session ed -- gnome-text-editor
    shotbox --session ed capture editor.png --window ".*Text Editor"
    shotbox stop ed

A kept session is a process of its own (`serve`), holding a Session open
until it's stopped or killed, and taking the programs it runs from a
control socket: a program that commands started by themselves would
outlive the session, so `spawn` asks the session to start it, in its own
process group, which goes with the session's. Each session has a record
in the runtime dir naming its environment, which `--session` puts a
command inside, as `shotbox run` puts one.
"""

import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .screen import SessionError, wait_for

NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def home():
    """Where kept sessions are recorded: the runtime dir, or a dir in /tmp,
    either way the user's own and no one else's. A record's environment
    goes into every command run with --session, so a dir someone else made
    first, or can write to, is refused."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    d = Path(base) / "shotbox" if base else Path(f"/tmp/shotbox-{os.getuid()}")
    try:
        d.mkdir(mode=0o700)
    except FileExistsError:
        pass
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise SessionError(f"{d} isn't a directory of your own that only you can use; "
                           "move it out of the way")
    return d


def started(pid):
    """When process `pid` started, in clock ticks since boot, or None if
    there's no such process: with the pid, which process it is, though the
    pid be used again."""
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


# mkdtemp's names: a session's scratch dir, and the MCP server's, which
# holds its sessions'.
SCRATCH = re.compile(r"(shotbox|sbm)-[a-z0-9_]{8}")
IDLE = 24 * 3600    # a lasting session untouched this long has likely been forgotten


def pid_ns():
    return os.stat("/proc/self/ns/pid").st_ino


def mark(d, **more):
    """Say in scratch dir `d` what made it: this process, by its pid and when
    it started, which `stale` tells the dir's life by."""
    (d / "shotbox.json").write_text(json.dumps(
        {"pid": os.getpid(), "started": started(os.getpid()), "ns": pid_ns(),
         "command": sys.argv, "time": time.time(), **more}))


def stale():
    """The user's scratch dirs in TMPDIR whose session has gone, and the mark
    each has (None from a shotbox that made none): gone, its maker has; with
    no mark, nothing of the user's has it in its environment. Not a dir kept
    with --keep, nor one whose maker is in another pid namespace, which this
    can't tell the life of."""
    from .mcp import ours
    me, uid = os.getpid(), os.getuid()
    envs = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            if int(p.name) != me and p.stat().st_uid == uid:
                envs.append((p / "environ").read_bytes())
        except OSError:
            continue
    out = []
    for d in sorted(Path(tempfile.gettempdir()).iterdir()):
        # kept.home()'s, without a runtime dir, isn't a scratch dir.
        if not SCRATCH.fullmatch(d.name) or d.name == f"shotbox-{uid}":
            continue
        try:
            st = d.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
                continue
            try:
                made = json.loads((d / "shotbox.json").read_text())
            except FileNotFoundError:
                made = None
            if made is None:
                # Made a moment ago, it may not have its mark yet.
                if time.time() - st.st_mtime < 60 or any(ours(e, os.fsencode(d)) for e in envs):
                    continue
            elif (made.get("kept") or made.get("ns") != pid_ns()
                  or started(made.get("pid")) == made.get("started")):
                continue
        except (OSError, ValueError, AttributeError):
            continue    # can't tell
        out.append((d, made))
    return out


def idle():
    """The lasting sessions untouched for IDLE seconds or more: (name,
    seconds). A command run with --session, and a spawn, touches its scratch
    dir."""
    out = []
    for name in names():
        try:
            since = time.time() - os.stat(find(name)["env"]["SHOTBOX_SCRATCH"]).st_mtime
        except (OSError, KeyError, SessionError):
            continue
        if since >= IDLE:
            out.append((name, since))
    return out


def size(d):
    """What `d` holds, in bytes."""
    total = 0
    for root, dirs, files in os.walk(d):
        for f in dirs + files:
            try:
                total += os.lstat(os.path.join(root, f)).st_blocks * 512
            except OSError:
                pass
    return total


def check(name):
    if not NAME.fullmatch(name or ""):
        raise SessionError(f"a session's name is letters, digits, dot, dash and "
                           f"underscore: not {name!r}")


def read(f):
    """A record, if its session is still the process that wrote it."""
    try:
        kept = json.loads(f.read_text())
        if started(kept["pid"]) == kept["started"]:
            return kept
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def find(name):
    """The kept session called `name`: its pid, env and control socket."""
    check(name)
    f = home() / f"{name}.json"
    if not f.exists():
        raise SessionError(f"no session called {name!r} (`shotbox sessions` lists them)")
    kept = read(f)
    if kept is None:
        f.unlink(missing_ok=True)
        raise SessionError(f"the session {name!r} has gone")
    return kept


def names():
    """The kept sessions there are, forgetting those that have gone."""
    out = []
    for f in sorted(home().glob("*.json")):
        if read(f):
            out.append(f.stem)
        else:
            f.unlink(missing_ok=True)
    return out


def ask(kept, request, timeout=30):
    """Send the session one request over its control socket; its answer."""
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(kept["control"])
        s.sendall(json.dumps(request).encode() + b"\n")
        line = s.makefile().readline()
    if not line:
        raise SessionError("the session didn't answer")
    answer = json.loads(line)
    if "error" in answer:
        raise SessionError(answer["error"])
    return answer


def start(name, argv):
    """Start a kept session called `name`, or the first free s1, s2... with
    none, with the session options in `argv`; wait until it's up, and
    return its name."""
    from .__main__ import self_command
    if name:
        check(name)
    taken = set(names())
    for n in range(1, 10000):
        try_name = name or f"s{n}"
        if try_name in taken:
            if name:
                raise SessionError(f"there's a session called {name!r} already")
            continue
        # Its own group and session, so it outlives this command and a
        # ctrl-C meant for this one doesn't reach it.
        proc = subprocess.Popen([*self_command(), "start", "--serve", try_name, *argv],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, start_new_session=True, text=True)
        # Until it's ready, or gone: a line that's neither is read past.
        line = proc.stdout.readline()
        while line and line.strip() != "ready":
            if line.startswith("warning "):
                sys.stderr.write(f"shotbox: {line.removeprefix('warning ')}")
            line = proc.stdout.readline()
        if line.strip() == "ready":
            proc.stdout.close()
            proc.stderr.close()
            return try_name
        proc.wait()
        err = proc.stderr.read().strip().removeprefix("shotbox: ")
        # Another start took the name first: with none asked for, the next.
        if err.endswith("already") and not name:
            taken.add(try_name)
            continue
        raise SessionError((err or "it didn't start") + f" (session {try_name!r})")
    raise SessionError("no free session name")


def claim(record, kept):
    """Make the record, `kept`, or fail if another session has the name. It's
    written whole to a file of this process's, then linked into place,
    which fails if the name is there: so of two sessions started at once,
    one has it, and neither ever reads the other's record half written
    and takes it for one whose session has gone."""
    mine = record.with_name(f".{record.stem}.{os.getpid()}")
    fd = os.open(mine, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(kept, f)
    try:
        for _ in range(2):
            try:
                os.link(mine, record)
                return
            except FileExistsError:
                if read(record):
                    break
                record.unlink(missing_ok=True)   # a session that's gone
        raise SessionError(f"there's a session called {record.stem!r} already")
    finally:
        mine.unlink()


def serve(name, session, keep=False):
    """Hold `session` open as the kept session `name`: record it, say
    "ready" on stdout, take requests until one says stop or the process
    is told to (SIGTERM, SIGHUP), and clean up."""
    record = home() / f"{name}.json"
    control = session.scratch / "control"
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.default_int_handler)
    claim(record, {"pid": os.getpid(), "started": started(os.getpid()),
                   "env": session.env, "control": str(control), "keep": keep})
    try:
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(control))
            server.listen()
            if session.unsealed:
                print(f"warning {session.unsealed}")
            print("ready", flush=True)
            # Nothing more is written there, and the starter stops reading.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            while True:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(10)
                    if not answer(session, conn):
                        return
    except KeyboardInterrupt:
        return
    finally:
        # A second TERM or HUP (logind sends both) would cut the cleanup short.
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        if (read(record) or {}).get("pid") == os.getpid():
            record.unlink(missing_ok=True)


def answer(session, conn):
    """Answer one request on `conn`; False once told to stop. Nothing a
    request does, and no client that has gone, stops the session but
    stop."""
    for proc in session._procs:   # reaps what's exited
        proc.poll()
    os.utime(session.scratch)   # for idle()
    going = False
    try:
        request = json.loads(conn.makefile().readline())
        op = request.get("op")
        if op == "stop":
            reply, going = {"ok": True}, True
        elif op == "spawn":
            if not request.get("cmd"):
                raise SessionError("nothing to spawn")
            proc = session.spawn(request["cmd"], log=request.get("log"),
                                 cwd=request.get("cwd"))
            reply = {"pid": getattr(proc, "program_pid", proc.pid)}
        else:
            reply = {"error": f"don't know {op!r}"}
    except (SessionError, OSError) as e:
        reply = {"error": str(e)}
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        reply = {"error": f"a request it can't read ({e})"}
    try:
        conn.sendall(json.dumps(reply).encode() + b"\n")
    except OSError:   # the client has gone
        pass
    return not going


def stop(name, timeout=120):
    """Stop the kept session `name`, and wait until it's gone. Its scratch
    dir, if it was started with --keep."""
    kept = find(name)
    try:
        ask(kept, {"op": "stop"})
    except (OSError, SessionError):
        # find() made sure the pid is still the session's.
        try:
            os.kill(kept["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    wait_for(f"the session {name!r} to stop",
             lambda: started(kept["pid"]) != kept["started"], timeout)
    return kept["env"]["SHOTBOX_SCRATCH"] if kept.get("keep") else None


def enter(name):
    """Put this process inside the kept session `name`, as `shotbox run`
    puts the command it runs."""
    env = find(name)["env"]
    os.utime(env["SHOTBOX_SCRATCH"])   # for idle()
    os.environ.clear()
    os.environ.update(env, SHOTBOX_FINISHES="1")


def main(argv):
    import argparse

    from .__main__ import make_session, say_usage, session_options
    p = argparse.ArgumentParser(prog="shotbox start",
                                description="Start a session that lasts until "
                                            "`shotbox stop NAME`, and say its name. "
                                            "`shotbox spawn --session NAME` runs "
                                            "programs in it, and `shotbox --session "
                                            "NAME COMMAND` puts a command inside it.")
    p.add_argument("--name", help="what to call it (s1, s2, ... by default)")
    p.add_argument("--serve", metavar="NAME", help=argparse.SUPPRESS)
    session_options(p)
    a = p.parse_args(argv)
    if a.serve:
        check(a.serve)
        with make_session(a) as s:
            serve(a.serve, s, keep=a.keep)
        say_usage(a, s)
        return 0
    print(start(a.name, argv))
    return 0
