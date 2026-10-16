"""shotbox mcp: the protocol, and the tools on a real session, called as a
client calls them (JSON-RPC requests into the handler); then once over
stdio, as a client runs it. Run by test/run.sh, with GTK 4, libadwaita
and at-spi2-core; prints ok/FAIL lines like it."""

import base64
import json
import math
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parent))
from shotbox import mcp  # noqa: E402

fails = 0


def check(cond, what):
    global fails
    print(("ok    " if cond else "FAIL  ") + what)
    fails += not cond


tmp = Path(tempfile.mkdtemp())
allow = tmp / "programs"
# `where` says where its session is, and takes a second to go when told to.
allow.write_text(f"# what the agent may run\napp = {here / 'a11y_app.py'}  # the window\n"
                 f"rel = bin/thing --flag 'two words' --accent=#3584e4 \\#hash # a comment\n"
                 f"reader = /bin/sh -c 'cat > /dev/null'\n"
                 f"held = {here / 'held.py'} held {tmp / 'held.log'}\n"
                 f"padread = {here / 'pad_read.py'} {tmp / 'pad.log'}\n"
                 f"tone = paplay {tmp / 'tone.wav'}\n"
                 f"late_tone = /bin/sh -c 'sleep 0.5; exec paplay {tmp / 'tone.wav'}'\n"
                 "where = /bin/sh -c 'echo \"$SHOTBOX_SCRATCH\"; trap \"sleep 1; exit\" TERM;"
                 " while :; do sleep 0.1; done'\n")
allowed, home = mcp.programs(allow)
check(allowed["rel"] == [str(tmp / "bin/thing"), "--flag", "two words", "--accent=#3584e4",
                         "#hash"]
      and home == tmp and allowed["app"] == [str(here / "a11y_app.py")],
      "the allow file: shell words, comments, a # in a word, a relative command")
bad_allow = tmp / "bad"
bad_allow.write_text("x = 'unclosed\n")
try:
    mcp.programs(bad_allow)
    check(False, "the allow file: a line it can't read, said, with its number")
except mcp.SessionError as e:
    check(":1:" in str(e), "the allow file: a line it can't read, said, with its number")

server = mcp.Server(allowed, home)
ids = iter(range(1, 1000))


def rpc(method, params=None):
    return mcp.handle(server, {"jsonrpc": "2.0", "id": next(ids), "method": method,
                               "params": {} if params is None else params})


def tool(which, /, **args):
    """The call's text, and whether it failed."""
    got = rpc("tools/call", {"name": which, "arguments": args})["result"]
    texts = [c["text"] for c in got["content"] if c["type"] == "text"]
    return "\n".join(texts), bool(got.get("isError")), got["content"]


init = rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
check(init["protocolVersion"] == "2025-03-26" and "tools" in init["capabilities"],
      "initialize: the version asked for, and tools")
check(rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"]
      == mcp.PROTOCOLS[0], "initialize: an unknown version gets the newest")
names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
check({"start_session", "spawn", "accessible_tree", "screenshot", "click"} <= set(names),
      f"tools/list ({len(names)} tools)")
check(rpc("nope")["error"]["code"] == -32601, "an unknown method is an error")
check(mcp.handle(server, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None,
      "a notification gets no answer")

said, bad, _ = tool("accessible_tree", session="none")
check(bad and "no session called 'none'" in said, "a session that isn't there: said")
check(rpc("tools/list", "oops")["error"]["code"] == -32602
      and mcp.handle(server, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                              "params": {"name": "lint", "arguments": [1]}})
      ["result"]["isError"], "params and arguments that aren't objects: said, not fatal")

try:
    said, bad, _ = tool("start_session", name="t")
    check(not bad and "app" in said, f"start_session says what it can spawn ({said})")
    said, bad, _ = tool("spawn", session="t", program="rm -rf ~")
    check(bad and "isn't one of the programs" in said and "app" in said,
          "spawn: what isn't on the list is refused, saying what is")
    said, bad, _ = tool("spawn", session="t", program="app")
    check(not bad, "spawn: a program on the list")
    said, bad, _ = tool("wait_for", session="t", what="window", value="x", timeout="nan")
    check(bad and "number" in said, "wait_for: a timeout that's no number is refused")
    said, bad, _ = tool("wait_for", session="t", what="window", value="a11y-test", timeout="20")
    check(not bad, f"wait_for: a window, its timeout given as text ({said})")
    said, bad, _ = tool("accessible_tree", session="t", window="a11y-test")
    check('"Save" [focusable, focused]' in said, "accessible_tree: the tree, as text")
    said, bad, _ = tool("click", session="t", name="Announce", window="a11y-test")
    check(not bad and "Announce" in said, "click: by a node's name")
    said, bad, _ = tool("wait_for", session="t", what="announcement", value="notes")
    check(not bad, "wait_for: an announcement")
    said, _, _ = tool("announcements", session="t")
    check(said == "polite: Saved to notes.md", f"announcements ({said!r})")
    said, bad, content = tool("screenshot", session="t", window="a11y-test")
    png = [c for c in content if c["type"] == "image"]
    check(len(png) == 1 and png[0]["mimeType"] == "image/png"
          and base64.b64decode(png[0]["data"])[:4] == b"\x89PNG",
          "screenshot: an image, returned")
    said, bad, content = tool("screenshot", session="t", window="a11y-test", vision="tritanopia")
    check(not bad and "as seen with tritanopia" in said
          and any(c["type"] == "image" for c in content), "screenshot: with a kind of color vision")
    said, bad, content = tool("screenshot", session="t", window="a11y-test", burst=3, every=0.1)
    png = [c for c in content if c["type"] == "image"]
    check(not bad and len(png) == 1 and said.startswith("3 pictures") and "0.10" in said,
          f"screenshot: a burst, as one sheet ({said})")
    said, bad, content = tool("screenshot", session="t", burst=12, crop="20x800+0+0")
    png = base64.b64decode([c for c in content if c["type"] == "image"][0]["data"])
    w, h = struct.unpack(">II", png[16:24])
    check(not bad and max(w, h) <= 1920, f"screenshot: a burst of tall pictures, shrunk to fit ({w}x{h})")
    said, bad, _ = tool("screenshot", session="t", burst=40)
    check(bad and "2 to 12" in said, "screenshot: a burst too big, refused")
    said, bad, content = tool("record", session="t", seconds=1, window="a11y-test")
    saved = re.match(r"Saved (.+?), ([\d.]+) seconds", said)
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", saved[1]], capture_output=True,
                                  text=True).stdout or 0) if saved else 0
    times = re.search(r"at (.*) seconds\.", said)
    check(not bad and abs(length - 1) < 0.15 and float(saved[2]) == round(length, 2)
          and saved[1].startswith(str(server.sessions["t"].scratch))
          and [c["type"] for c in content] == ["image", "text"]
          and len(times[1].split(", ")) == 8,
          f"record: a clip of the length asked for, and a sheet of 8 frames ({said})")
    said, bad, _ = tool("record", session="t", seconds=0.1, path="clips/one.mp4")
    times = re.search(r"at (.*) seconds\.", said)
    check(not bad and (tmp / "clips/one.mp4").stat().st_size
          and str(tmp / "clips/one.mp4") in said and 1 <= len(times[1].split(", ")) < 8,
          f"record: a short clip, saved at a path in the allow file's directory ({said})")
    mcp.RECORD_SIZE, size = 1, mcp.RECORD_SIZE
    try:
        said, bad, _ = tool("record", session="t", seconds=3)
    finally:
        mcp.RECORD_SIZE = size
    saved = re.match(r"Saved (.+?), ([\d.]+) seconds", said)
    check(not bad and float(saved[2]) < 2 and "it reached" in said,
          f"record: a clip stopped at its size, said ({said})")
    (tmp / "dir.mp4").mkdir()
    refused = [tool("record", session="t", seconds=secs, path=p, sound=a)[:2]
               for secs, p, a in ((61, None, False), ("", None, False), (1, "/tmp/x.mp4", False),
                                  (1, "../" * 20 + "tmp/x.mp4", False), (1, "x.webm", False),
                                  (1, None, True), (1, "clips/one.mp4", False),
                                  (1, "dir.mp4", False), (1, "clips/one.mp4/x.mp4", False))]
    check(all(bad for _, bad in refused) and "0.1 to 60, not 61" in refused[0][0]
          and "needs seconds" in refused[1][0]
          and all("by its path from there" in said for said, _ in refused[2:5])
          and "start a session with audio" in refused[5][0]
          and "a file at" in refused[6][0] and "a directory at" in refused[7][0]
          and "is a file, not a directory" in refused[8][0],
          "record: too long or no length, a path outside the allow file's directory, not an "
          ".mp4 or there already, and sound without audio, refused")
    said, bad, _ = tool("screenshot", session="t", vision="martian")
    check(bad and "deuteranopia" in said, "screenshot: a vision there isn't, refused saying which")
    said, _, _ = tool("lint", session="t", window="a11y-test", ignore="image")
    check(len(said.splitlines()) == 2 and "with no name" in said,
          f"lint, ignoring images given as text ({said!r})")
    said, _, _ = tool("contrast", session="t", window="a11y-test")
    check(said == "No problems.", f"contrast ({said!r})")
    said, _, _ = tool("tab_walk", session="t", window="a11y-test", visible=True)
    check("focus not shown" in said and said.endswith("Round again after 6."),
          "tab_walk: the order, and where the focus doesn't show")
    said, bad, _ = tool("press_keys", session="t", keys="Tab Tab")
    check(not bad and said == "Pressed Tab, Tab.", "press_keys: keys given as text")
    # Held keys and buttons, across calls: on Wayland too, since a session
    # keeps its input connection.
    # A session of its own: in "t", the window clicked by name keeps the keyboard.
    tool("start_session", name="h")
    tool("spawn", session="h", program="held")
    tool("wait_for", session="h", what="window", value="held", timeout="20")
    tool("wait_for", session="h", what="stable", window="held")
    said, bad, _ = tool("wait_for", session="h", what="change", window="held", timeout=1)
    check(bad and "to change" in said, f"wait_for: change gives up on a still window ({said})")
    _, _, content = tool("screenshot", session="h", window="held")
    png = next(c for c in content if c["type"] == "image")
    (tmp / "held.png").write_bytes(base64.b64decode(png["data"]))
    said, bad, _ = tool("wait_for", session="h", what="match", value="held.png", window="held")
    refused = [tool("wait_for", session="h", what="match", value=v)[:2]
               for v in ("/etc/passwd", "../" * 20 + "etc/hostname", "nope.png")]
    check(not bad and "matches held.png" in said
          and all(bad and "no file" in said for said, bad in refused),
          f"wait_for: match, a picture in the allow file's directory and nowhere else ({said})")
    (tmp / "game.log").write_text("beacon_claimed 1\n")
    with open(tmp / "game.log", "a") as log:
        threading.Timer(1, lambda: log.write("beacon_claimed 2\n") and log.flush()).start()
        said, bad, _ = tool("wait_for", session="h", what="line", value="beacon_claimed",
                            file="game.log")
    refused = [tool("wait_for", session="h", what="line", value="x", file=f)[:2]
               for f in ("/etc/hostname", "../" * 20 + "etc/hostname", ".")]
    threading.Timer(0.5, (tmp / "later.log").symlink_to, ["/etc/hostname"]).start()
    later, later_bad, _ = tool("wait_for", session="h", what="line", value="", file="later.log")
    check(not bad and said == "beacon_claimed 2"
          and all(bad and "by its path from there" in said for said, bad in refused)
          and later_bad and "outside" in later,
          f"wait_for: line, a new one, in a file in the allow file's directory only ({said})")
    with open(tmp / "game.log", "a") as log:
        threading.Timer(1, lambda: log.write("run_end 3\n") and log.flush()).start()
        said, bad, _ = tool("record", session="h", until_line="run_", file="game.log",
                            tail=0.5)
    saved = re.match(r"Saved (.+?), ([\d.]+) seconds", said)
    capped, capped_bad, _ = tool("record", session="h", until_line="nope", file="game.log",
                                 seconds=0.5)
    threading.Timer(0.5, (tmp / "later2.log").symlink_to, ["/etc/hostname"]).start()
    refused = [tool("record", session="h", until_line="x", file=f)[:2]
               for f in ("later2.log", "/etc/hostname", None)]
    check(not bad and saved and 1.2 <= float(saved[2]) < 2.5
          and said.endswith("It ended on the line: run_end 3")
          and not capped_bad and "No line matching 'nope' came in its 0.5 seconds" in capped
          and "outside" in refused[0][0] and "by its path from there" in refused[1][0]
          and "needs a file" in refused[2][0] and all(bad for _, bad in refused),
          f"record: until_line, ended by a new line, and tail; else by seconds; its file in "
          f"the allow file's directory only ({said})")
    steps = [("click", dict(x=50, y=50, window="held")),
             ("press_keys", dict(keys="w", state="down")),
             ("click", dict(x=60, y=60, window="held", state="down")),
             ("click", dict(x=60, y=60, window="held", state="up")),
             ("press_keys", dict(keys="w", state="up")),
             ("press_keys", dict(keys="d", hold="0.3"))]
    said = [tool(name, session="h", **args) for name, args in steps]
    time.sleep(0.3)
    # GTK may say a button twice, and a key held long enough repeats: a
    # press of what's already down is dropped.
    events, down = [], set()
    for what, name in (line.split()[:2] for line in (tmp / "held.log").read_text().splitlines()):
        if (what == "press") == (name in down):
            continue
        (down.add if what == "press" else down.discard)(name)
        events.append([what, name])
    want = [["press", "button1"], ["release", "button1"], ["press", "w"],
            ["press", "button1"], ["release", "button1"], ["release", "w"],
            ["press", "d"], ["release", "d"]]
    check(not any(bad for _, bad, _ in said) and events == want
          and said[1][0] == "Pressed and left down w." and said[5][0] == "Held for 0.3 s d.",
          f"press_keys and click: held down across calls, and for a while ({events})")
    said, bad, _ = tool("press_keys", session="h", keys="w", hold=1, state="down")
    check(bad and "not both" in said, "press_keys: hold and state together, refused")
    said, bad, _ = tool("press_keys", session="h", keys="w", hold=120)
    check(bad and "0 to 60" in said, "press_keys: a hold too long, refused")
    said, bad, _ = tool("click", session="h", x=5, y=5, double=True, hold=1)
    check(bad and "not together" in said, "click: double with hold, refused")
    tool("stop_session", session="h")
    if os.access("/dev/uinput", os.W_OK) and shutil.which("bwrap") and subprocess.run(
            ["bwrap", "--dev-bind", "/", "/", "true"], capture_output=True).returncode == 0:
        said, bad, _ = tool("start_session", name="g", a11y=False, gamepads=1)
        check(not bad and "1 gamepad" in said, f"start_session with a gamepad ({said})")
        said, _, _ = tool("spawn", session="g", program="padread")
        pid = re.search(r"pid (\d+)", said)[1]
        log, end = tmp / "pad.log", time.monotonic() + 10
        while not (log.exists() and "ready" in log.read_text()) and time.monotonic() < end:
            time.sleep(0.1)
        # Once it's ready: before then, bwrap's child may not be the program yet.
        check(Path(f"/proc/{pid}/comm").read_text().strip() != "bwrap"
              and "pad_read" in Path(f"/proc/{pid}/cmdline").read_text(),
              "spawn in a gamepad session: the program's own pid, not the sandbox's")
        said = [tool("gamepad", session="g", action="press", buttons="a"),
                tool("gamepad", session="g", action="stick", which="left", x=1, y=0, hold=0.1),
                tool("gamepad", session="g", action="press", buttons="y", state="down"),
                tool("gamepad", session="g", action="neutral")]
        time.sleep(0.3)
        # The kernel passes on only what changes: y stays 0, so says nothing.
        got = [line for line in log.read_text().splitlines() if line.startswith(("key", "abs"))]
        check(not any(bad for _, bad, _ in said)
              and got == ["key 304 1", "key 304 0", "abs 0 32767", "abs 0 0",
                          "key 308 1", "key 308 0"],
              f"gamepad: press, a stick held a while, a button left down, neutral ({got})")
        said, bad, _ = tool("gamepad", session="g", action="press", buttons="z")
        check(bad and "no button z" in said, "gamepad: a button there isn't, said")
        refused = [tool("gamepad", session="g", action="press", buttons="a", pad=7),
                   tool("gamepad", session="g", action="stick", which="left", x=5, y=0),
                   tool("gamepad", session="g", action="trigger", which="left", value=-1),
                   tool("start_session", name="g9", gamepads=9)]
        check(all(bad for _, bad, _ in refused) and "1 to 4, not 7" in refused[0][0]
              and "-1 to 1, not 5" in refused[1][0] and "0 to 4, not 9" in refused[3][0],
              "gamepad: a pad, stick, trigger or count out of range, refused")
        tool("stop_session", session="g")
    else:
        print("skip  gamepad: no writable /dev/uinput or no bwrap")
    if shutil.which("pipewire") and shutil.which("wireplumber") and shutil.which("paplay"):
        with wave.open(str(tmp / "tone.wav"), "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(48000)
            w.writeframes(b"".join(struct.pack("<hh", v, v) for v in (
                int(8000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(24000))))
        tool("start_session", name="a", a11y=False, audio=True)
        said, bad, _ = tool("sound", session="a", action="stop")
        check(bad and "action start first" in said, "sound: stop before start, refused")
        tool("sound", session="a", action="start")
        tool("spawn", session="a", program="tone")
        time.sleep(1.5)
        said, bad, _ = tool("sound", session="a", action="stop")
        check(not bad and "No gaps, clipping or silence." in said
              and re.search(r"peak -12\.\d dBFS", said),
              f"sound: what a program played, heard ({said})")
        tool("spawn", session="a", program="late_tone")
        said, bad, content = tool("record", session="a", seconds=1.5, sound=True)
        check(not bad and said.startswith("Saved") and "No gaps, clipping or silence." in said
              and re.search(r"peak -12\.\d dBFS", said) and content[0]["type"] == "image",
              f"record: with sound, what was heard said too ({said})")
        tool("stop_session", session="a")
        said, bad, _ = tool("sound", session="t", action="start")
        check(bad and "start a session with audio" in said, "sound: no audio, refused")
    else:
        print("skip  sound: no PipeWire, WirePlumber or paplay")
    tool("start_session", name="u", a11y=False, usage=True)
    said, bad, _ = tool("stop_session", session="u")
    check(not bad and "used, over" in said and "peak memory" in said,
          f"stop_session: what a session with usage used, said ({said})")
    said, bad, _ = tool("click", session="t", nmae="Save")
    check(bad and "doesn't take nmae" in said, "an argument a tool doesn't take: said")
    said, bad, _ = tool("program_output", session="t", program="app")
    check(not bad and "still running" in said, "program_output")
    scratch = server.sessions["t"].scratch
    said, bad, _ = tool("stop_session", session="t")
    check(not bad and not scratch.exists() and not server.sessions,
          "stop_session: gone, and its scratch dir")
finally:
    server.close()

# Over stdio, as a client runs it: nothing but the protocol on stdout, a
# program reading stdin doesn't get the requests, and its sessions stop
# when stdin closes, or on SIGHUP.
def serve(*requests):
    """Start the server, send `requests`, and read an answer for each that
    has an id: (the server, its answers)."""
    proc = subprocess.Popen([sys.argv[1], "mcp", "--allow", str(allow)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    answers = []
    for q in requests:
        proc.stdin.write(json.dumps(q) + "\n")
        proc.stdin.flush()
        if "id" in q:
            # A server that never answers fails the test, not hangs it.
            if not select.select([proc.stdout], [], [], 120)[0]:
                proc.kill()
                raise SystemExit(f"FAIL  over stdio: no answer to {q['id']}")
            answers.append(json.loads(proc.stdout.readline()))
    return proc, answers


def call_q(rid, which, /, **args):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": which, "arguments": args}}


def gone(pid):
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True


proc, answers = serve({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                      call_q(2, "start_session", name="io"),
                      call_q(3, "spawn", session="io", program="reader"),
                      call_q(4, "spawn", session="io", program="app"),
                      {"jsonrpc": "2.0", "id": 5, "method": "ping"})
check([a["id"] for a in answers] == [1, 2, 3, 4, 5]
      and not any(a["result"].get("isError") for a in answers[1:4]),
      "over stdio: an answer a request, a program reading stdin notwithstanding")
pid = int(re.search(r"pid (\d+)", answers[3]["result"]["content"][0]["text"])[1])
proc.stdin.close()
proc.wait(60)
check(proc.returncode == 0 and gone(pid), "over stdio: its sessions stop when stdin closes")
def pid_of(answer):
    return int(re.search(r"pid (\d+)", answer["result"]["content"][0]["text"])[1])


def until_gone(*paths_or_pids, wait=5):
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if all(gone(p) if isinstance(p, int) else not Path(p).exists() for p in paths_or_pids):
            return True
        time.sleep(0.1)
    return False


# As Claude Code stops a server: SIGINT, SIGTERM 100ms on, SIGKILL 400ms
# after, with a program that takes a second to go on TERM. And a SIGKILL
# alone. The watchdog stops what the server can't, either way.
for how in ("claude", "kill"):
    proc, answers = serve(call_q(1, "start_session", name=how, wayland=how == "kill"),
                          call_q(2, "spawn", session=how, program="app"),
                          call_q(3, "spawn", session=how, program="where"),
                          call_q(4, "wait_for", session=how, what="stable"),
                          call_q(5, "program_output", session=how, program="where"))
    app, where = pid_of(answers[1]), pid_of(answers[2])
    scratch = answers[4]["result"]["content"][0]["text"].splitlines()[1]
    # Not the session's: a shell that cd'd there, its PWD in the directory.
    onlooker = subprocess.Popen(["sleep", "300"], cwd=scratch,
                                env=dict(os.environ, PWD=scratch))
    if how == "claude":
        proc.send_signal(signal.SIGINT)
        time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.4)
    proc.kill()
    proc.wait(10)
    check(scratch.startswith("/") and until_gone(app, where, scratch),
          f"stopped {'as Claude Code stops it' if how == 'claude' else 'by SIGKILL'}: "
          "its sessions go anyway, and their scratch dirs")
    check(onlooker.poll() is None, "the watchdog leaves a process that's only in its dir")
    onlooker.kill()
    onlooker.wait()
proc, answers = serve(call_q(1, "start_session", name="hup"),
                      call_q(2, "spawn", session="hup", program="app"))
pid = int(re.search(r"pid (\d+)", answers[1]["result"]["content"][0]["text"])[1])
proc.send_signal(signal.SIGHUP)
proc.wait(60)
check(until_gone(pid), "SIGHUP: its sessions stop")
shutil.rmtree(tmp)
sys.exit(1 if fails else 0)
