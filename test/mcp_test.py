"""shotbox mcp: the protocol, and the tools on a real session, called as a
client calls them (JSON-RPC requests into the handler); then once over
stdio, as a client runs it. Run by test/run.sh, with GTK 4, libadwaita
and at-spi2-core; prints ok/FAIL lines like it."""

import base64
import json
import os
import re
import select
import signal
import subprocess
import sys
import tempfile
import time
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
                 f"rel = bin/thing --flag 'two words' --accent=#3584e4\n"
                 f"reader = /bin/sh -c 'cat > /dev/null'\n"
                 "where = /bin/sh -c 'echo \"$SHOTBOX_SCRATCH\"; trap \"sleep 1; exit\" TERM;"
                 " while :; do sleep 0.1; done'\n")
allowed, home = mcp.programs(allow)
check(allowed["rel"] == [str(tmp / "bin/thing"), "--flag", "two words", "--accent=#3584e4"]
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
proc, answers = serve(call_q(1, "start_session", name="hup"),
                      call_q(2, "spawn", session="hup", program="app"))
pid = int(re.search(r"pid (\d+)", answers[1]["result"]["content"][0]["text"])[1])
proc.send_signal(signal.SIGHUP)
time.sleep(0.1)
proc.send_signal(signal.SIGTERM)
proc.wait(60)
check(gone(pid), "SIGHUP, then SIGTERM in the middle of stopping: its sessions stop")
sys.exit(1 if fails else 0)
