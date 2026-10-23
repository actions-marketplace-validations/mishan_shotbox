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
                 f"long = {here / 'a11y_app.py'} long\n"
                 f"wrapped = /bin/sh -c '{here / 'a11y_app.py'}; exit $?'\n"
                 f"daemon = /bin/sh -c '(setsid {here / 'window.py'} daemon &)'\n"
                 f"keys = {here / 'a11y_app.py'} keys\n"
                 f"freeze = {here / 'a11y_app.py'} freeze\n"
                 f"ready = {here / 'a11y_app.py'} ready\n"
                 f"fading = {here / 'a11y_app.py'} faint fading\n"
                 f"rel = bin/thing --flag 'two words' --accent=#3584e4 \\#hash # a comment\n"
                 f"reader = /bin/sh -c 'cat > /dev/null'\n"
                 f"held = {here / 'held.py'} held {tmp / 'held.log'}\n"
                 f"padread = {here / 'pad_read.py'} {tmp / 'pad.log'}\n"
                 f"tone = paplay {tmp / 'tone.wav'}\n"
                 f"late_tone = /bin/sh -c 'sleep 0.5; exec paplay {tmp / 'tone.wav'}'\n"
                 f"talk = {sys.executable} -c \"print('one\\\\rtwo'); print('x' * 600)\"\n"
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


def boxes(said):
    """take_screenshot's labels: each name's box on the picture."""
    return {m[1]: tuple(map(int, m.groups()[1:])) for m in
            re.finditer(r'^\d+ [a-z ]+ "([^"]*)".* @(\d+),(\d+) (\d+)x(\d+)$', said, re.M)}


def pixel(content, x, y):
    """The RGB of a pixel of a call's image."""
    png = tmp / "pixel.png"
    png.write_bytes(base64.b64decode(next(c for c in content if c["type"] == "image")["data"]))
    return subprocess.run([*mcp.CONVERT, str(png), "-crop", f"1x1+{x}+{y}", "-depth", "8",
                           "rgb:-"], capture_output=True, check=True).stdout


init = rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
check(init["protocolVersion"] == "2025-03-26" and "tools" in init["capabilities"],
      "initialize: the version asked for, and tools")
check(rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"]
      == mcp.PROTOCOLS[0], "initialize: an unknown version gets the newest")
check([rpc("initialize", {"protocolVersion": v})["result"]["protocolVersion"]
       for v in ("2025-11-25", "2025-06-18", "2024-11-05", "2026-07-28")]
      == ["2025-11-25", "2025-06-18", "2024-11-05", "2025-11-25"]
      and "resultType" not in init,
      "initialize: each version it knows, as before; a stateless one gets the newest that isn't")


def modern(method, version="2026-07-28", **params):
    meta = {"io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientCapabilities": {}}
    return rpc(method, {"_meta": meta, **params})


found = modern("server/discover")["result"]
check(found["instructions"] == mcp.INSTRUCTIONS and "tools" in found["capabilities"]
      and found["supportedVersions"] == ["2026-07-28"]
      and found["resultType"] == "complete" and found["cacheScope"] == "public"
      and found["ttlMs"] > 0
      and found["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "shotbox",
      "server/discover: the versions, the tools, the instructions, how long to keep it")
tools = modern("tools/list")["result"]
called = modern("tools/call", name=mcp.TOOLS[0][0], arguments=[1])["result"]
check(tools["resultType"] == called["resultType"] == "complete"
      and tools["tools"] == mcp.tool_list(allowed) and tools["ttlMs"] > 0
      and called["isError"] and "resultType" not in rpc("tools/list")["result"],
      "2026-07-28: the version in _meta, no initialize, every result complete;"
      " the older versions' results as they were")
refused = modern("tools/list", "1900-01-01")["error"]
check(refused["code"] == -32022 and refused["data"]["requested"] == "1900-01-01"
      and refused["data"]["supported"] == list(mcp.STATELESS + mcp.PROTOCOLS)
      and modern("server/discover", "1900-01-01")["error"]["code"] == -32022,
      "a version it doesn't speak: refused, with the ones it does")
check(rpc("server/discover")["error"]["code"]
      == rpc("tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion":
                                      "2026-07-28"}})["error"]["code"] == -32602
      and "result" in rpc("initialize", {"protocolVersion": "2025-06-18"})
      and modern("initialize", protocolVersion="2025-06-18")["error"]["code"] == -32601,
      "server/discover without a version, or 2026-07-28 without capabilities: malformed;"
      " initialize still answered after, but not as 2026-07-28, which has none")
listed = {t["name"]: t["inputSchema"]["properties"] for t in rpc("tools/list")["result"]["tools"]}
check({"start_session", "spawn_program", "get_accessible_tree", "take_screenshot",
       "click_element"} <= set(listed),
      f"tools/list ({len(listed)} tools)")
check(listed["spawn_program"]["program"]["enum"] == listed["get_program_output"]["program"]["enum"]
      == list(allowed) and all("enum" not in t["inputSchema"]["properties"].get("program", {})
                               for t in mcp.tool_list()),
      "tools/list: program is one of the allowed programs, unless there are none")
check(all("description" in p for t in listed.values() for p in t.values()),
      "tools/list: every parameter described")
check(rpc("nope")["error"]["code"] == -32601, "an unknown method is an error")
check(mcp.handle(server, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None,
      "a notification gets no answer")


def tree(*rows):
    return [mcp.Node(depth=d, role=r, name=n, description="", states=s) for d, r, n, s in rows]


rows = [(0, "frame", "w", []), (1, "button", "Save", ["focused"]), (1, "label", "Row 1", []),
        (1, "check box", "Wrap", ["checked"])]
window = tree(*rows)
unfocused = [rows[0], (1, "button", "Save", []), *rows[2:]]
dialog = tree((1, "dialog", "Quit?", []), (2, "button", "OK", []))
items = [(0, "frame", "w", []), (1, "list", "", []), (2, "list item", "", []),
       (3, "label", "A", []), (2, "list item", "", []), (3, "label", "B", [])]
hung = [mcp.Node(depth=0, role="frame", name="hang", description="", states=[], pid=7),
        *tree((1, "button", "Freeze", []))]
quiet = mcp.Node(depth=0, role="application", name="", description="", states=[], pid=7,
                 quiet=True)
for before, after, want, what in (
        (window, window, [], "nothing"),
        (window, tree((0, "frame", "w", []), (1, "button", "Save", []), (1, "label", "Row 2", []),
                      (1, "check box", "Wrap", ["focused"])),
         ['~ button "Save", was [focused]', '~ label "Row 2", was "Row 1"',
          '~ check box "Wrap" [focused], was [checked]'], "renamed, other states"),
        (window, tree((0, "frame", "w", []), (1, "label", "Row 0", []), *rows[1:]) + dialog
         + tree((0, "frame", "x", [])),
         ['+ label "Row 0"', '+ dialog "Quit?"', '+   button "OK"', '+ frame "x"'],
         "added, above what was there and below, indented only under what's printed"),
        (window, window[:1] + window[2:], ['- button "Save" [focused]'], "gone"),
        (window + dialog, window, ['- dialog "Quit?" and 1 inside'], "a subtree gone whole"),
        (tree(*items), tree(*items[:2], (2, "list item", "", []), (3, "label", "New", []),
                             *items[2:]),
         ['+ list item ""', '+   label "New"'], "a row added, from its start"),
        (window + tree(*unfocused), tree(*unfocused, *rows),
         ['in frame "w" (window 1):', '~ button "Save", was [focused]',
          'in frame "w" (window 2):', '~ button "Save" [focused], was []'],
         "changes in two windows the same, each under which it is"),
        (window + hung, window + [quiet],
         ["! application \"\" (pid 7) didn't answer: busy, or hung, so its windows aren't "
          "compared"], "a program that didn't answer, not its windows gone")):
    got = mcp.tree_changes(before, after)
    check(got == want, f"what changed in the tree: {what} ({got})")

said, bad, _ = tool("get_accessible_tree", session="none")
check(bad and "no session called 'none'" in said, "a session that isn't there: said")
check(rpc("tools/list", "oops")["error"]["code"] == -32602
      and mcp.handle(server, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                              "params": {"name": "check_accessible_names", "arguments": [1]}})
      ["result"]["isError"], "params and arguments that aren't objects: said, not fatal")

said, bad, _ = tool("start_session", name="small", text_scale=0.05)
check(bad and "at least 0.1" in said and "small" not in server.sessions,
      "start_session: a text_scale under 0.1, refused")
try:
    said, bad, _ = tool("start_session", name="t")
    check(not bad and "app" in said, f"start_session says what it can spawn ({said})")
    said, bad, _ = tool("spawn_program", session="t", program="rm -rf ~")
    check(bad and "isn't one of the programs" in said and "app" in said,
          "spawn_program: what isn't on the list is refused, saying what is")
    said, bad, _ = tool("spawn_program", session="t", program="app")
    check(not bad, "spawn_program: a program on the list")
    said, bad, _ = tool("wait_for", session="t", what="window", value="x", timeout="nan")
    check(bad and "number" in said, "wait_for: a timeout that's no number is refused")
    began = time.monotonic()
    said, bad, _ = tool("wait_for", session="t", what="window", value="nowhere", timeout=0)
    check(bad and time.monotonic() - began < 5, "wait_for: a timeout of 0 gives up at once")
    said, bad, _ = tool("wait_for", session="t", what="window", value="a11y-test", timeout="20")
    check(not bad, f"wait_for: a window, its timeout given as text ({said})")
    said, bad, _ = tool("wait_for", session="t", what="change", ignore=["10x10"], timeout=1)
    check(bad and "WIDTHxHEIGHT" in said, f"wait_for: an ignore that's no box, refused ({said})")
    said, bad, _ = tool("wait_for", session="t", what="change", margin=2, timeout=1)
    check(bad and "margin goes with match" in said, f"wait_for: a margin for a change, refused ({said})")
    # Off the screen's corner, where the window's x,y and the screen's differ.
    subprocess.run(["xdotool", "search", "--name", "^a11y-test$", "windowmove", "--sync",
                    "230", "170"], env=server.sessions["t"].env, check=True)
    said, bad, _ = tool("get_accessible_tree", session="t", window="a11y-test")
    check('"Save" [focusable, focused]' in said, "get_accessible_tree: the tree, as text")
    said, bad, _ = tool("click_element", session="t", name="Announce", window="a11y-test")
    check(not bad and "Announce" in said, "click_element: by a node's name")
    said, bad, _ = tool("wait_for", session="t", what="announcement", value="notes")
    check(not bad, "wait_for: an announcement")
    said, bad, _ = tool("wait_for", session="t", what="node", value="^Wrap", window="a11y-test")
    check(not bad and said.startswith("It's there: check box \"Wrap lines\""),
          f"wait_for: a node ({said})")
    said, _, _ = tool("list_announcements", session="t")
    check(said == "| polite: Saved to notes.md", f"list_announcements, fenced ({said!r})")
    said, bad, content = tool("take_screenshot", session="t", window="a11y-test")
    png = [c for c in content if c["type"] == "image"]
    check(len(png) == 1 and png[0]["mimeType"] == "image/png"
          and base64.b64decode(png[0]["data"])[:4] == b"\x89PNG"
          and re.fullmatch(r"A picture of the window 'a11y-test', \d+x\d+\.", said),
          f"take_screenshot: an image, returned, and its size ({said})")
    said, bad, content = tool("take_screenshot", session="t", window="a11y-test",
                              vision="tritanopia")
    check(not bad and "as seen with tritanopia" in said
          and any(c["type"] == "image" for c in content),
          "take_screenshot: with a kind of color vision")
    said, bad, content = tool("take_screenshot", session="t", window="a11y-test", burst=3,
                              every=0.1)
    png = [c for c in content if c["type"] == "image"]
    # every is the least gap: under load, more. Times to two places.
    times = [float(t) for t in re.search(r"taken at (.*) seconds", said)[1].split(", ")]
    check(not bad and len(png) == 1 and said.startswith("3 pictures") and len(times) == 3
          and all(b - a >= 0.09 for a, b in zip(times, times[1:])),
          f"take_screenshot: a burst, as one sheet ({said})")
    said, bad, content = tool("take_screenshot", session="t", burst=12, crop="20x800+0+0")
    png = base64.b64decode([c for c in content if c["type"] == "image"][0]["data"])
    w, h = struct.unpack(">II", png[16:24])
    check(not bad and (w, h) == (240, 800) and "12 to a row" in said
          and said.startswith("12 pictures of 0,0 to 20,800 of the whole screen,")
          and said.endswith("; 240x800."),
          f"take_screenshot: a burst of tall pictures, in one row, not shrunk ({said})")
    whole, bad, content = tool("take_screenshot", session="t", window="a11y-test", labels=True)
    full = boxes(whole)
    cropped = boxes(tool("take_screenshot", session="t", window="a11y-test", labels=True,
                         crop="200x150+50+100")[0])
    x, y, w, h = full["Announce"]
    check(not bad and re.search(r'^1 (push )?button "Save" .*@\d+,\d+ \d+x\d+$', whole, re.M)
          and not re.search(r'^\d+ (frame|label "Save")', whole, re.M) and "Wrap lines" in full
          and pixel(content, x, y + h // 2) == b"\xff\x00\xff"
          and cropped["Announce"] == (max(0, x - 50), y - 100, min(x + w, 250) - max(x, 50), h),
          f"take_screenshot: labels, a number a named node, its box drawn where the legend says, "
          f"and moved with a crop ({whole}; {cropped})")
    zoomed, bad, content = tool("take_screenshot", session="t", window="a11y-test", labels=True,
                                zoom=[x - 12, y - 12, x + w + 12, y + h + 12])
    m = re.search(r"is (?:(\d+) \+ )?x/([\d.]+), (?:(\d+) \+ )?y/([\d.]+), with the same "
                  r"window\.\n", zoomed)
    ox, kx, oy, ky = (float(v or 0) for v in m.groups()) if m else (0, 1, 0, 1)
    zx, zy, zw, zh = boxes(zoomed).get("Announce", (0, 0, 0, 0))
    check(not bad and m and pixel(content, zx, zy + zh // 2) == b"\xff\x00\xff"
          and all(abs(a - b) <= 1 for a, b in zip(
              (ox + zx / kx, oy + zy / ky, zw / kx, zh / ky), (x, y, w, h))),
          f"take_screenshot: labels on a zoom, a box drawn where the legend says, mapped back by "
          f"the answer to where the tree has it ({zoomed})")
    # Past what a model sees unscaled: shrunk, as Anthropic's vision docs
    # have 1920x1080 seen, with the scale to map an x,y back by.
    said, bad, _ = tool("start_session", name="big", screen="1920x1080")
    to = "click_element's x,y for a point at x,y on it is"
    for args, size, want in (
            ({}, (1456, 819), "the whole screen, 1456x819, scaled by 0.7583 from 1920x1080: "
             f"{to} x/0.7583, y/0.7583."),
            ({"crop": "1800x1000+100+50"}, (1462, 812), "100,50 to 1900,1050 of the whole "
             "screen, 1462x812, scaled by 0.8122 across and 0.812 down from 1800x1000: "
             f"{to} 100 + x/0.8122, 50 + y/0.812."),
            ({"zoom": "0,0,1920,1080"}, (1456, 819), "0,0 to 1920,1080 of the whole screen, "
             f"1456x819, scaled by 0.7583 from 1920x1080: {to} x/0.7583, y/0.7583."),
            # Thin: each side rounded on its own.
            ({"zoom": [100, 0, 110, 1080]}, (15, 1568), "100,0 to 110,1080 of the whole screen, "
             f"15x1568, scaled by 1.5 across and 1.452 down from 10x1080: {to} 100 + x/1.5, "
             "y/1.452."),
            ({"zoom": [7.6, 0, 9.6, 2]}, (16, 16), "8,0 to 10,2 of the whole screen, 16x16, "
             f"scaled by 8 from 2x2: {to} 8 + x/8, y/8.")):
        said, bad, content = tool("take_screenshot", session="big", **args)
        png = base64.b64decode([c for c in content if c["type"] == "image"][0]["data"])
        check(not bad and said == f"A picture of {want}"
              and struct.unpack(">II", png[16:24]) == size,
              f"take_screenshot {args}: its size and how to map an x,y back said ({said})")
    said, bad, content = tool("take_screenshot", session="big", burst=4)
    png = base64.b64decode([c for c in content if c["type"] == "image"][0]["data"])
    check(not bad and "2 to a row" in said and struct.unpack(">II", png[16:24]) == (1456, 819)
          and said.endswith("; 1456x819, scaled by 0.3792 from 3840x2160."),
          f"take_screenshot: a burst of wide screens, two to a row, shrunk least ({said})")
    tool("spawn_program", session="big", program="app")
    tool("wait_for", session="big", what="window", value="a11y-test", timeout=20)
    x, y = map(int, re.search(r'"Announce" .*@(\d+),(\d+) ', tool(
        "get_accessible_tree", session="big", bounds=True)[0]).groups())
    said, bad, content = tool("take_screenshot", session="big", labels=True)
    k = 1456 / 1920
    # Its corner: GTK 4.14.5 says its box runs past what it's in, which cuts it.
    check(not bad and boxes(said)["Announce"][:2] == (round(x * k), round(y * k)),
          f"take_screenshot: labels on a picture shrunk, their corners moved with it ({said})")
    tool("stop_session", session="big")
    said, bad, _ = tool("start_session", name="huge", a11y=False, screen="7681x4320")
    check(bad and "at most 7680x4320, not 7681x4320" in said and "huge" not in server.sessions,
          f"start_session: a screen past 8K, refused ({said})")
    said, bad, _ = tool("take_screenshot", session="t", burst=40)
    check(bad and "2 to 12" in said, "take_screenshot: a burst too big, refused")
    said, bad, content = tool("record_video", session="t", seconds=1, window="a11y-test")
    saved = re.match(r"Saved (.+?), ([\d.]+) seconds", said)
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", saved[1]], capture_output=True,
                                  text=True).stdout or 0) if saved else 0
    times = re.search(r"at (.*) seconds; \d+x\d+", said)
    check(not bad and abs(length - 1) < 0.15 and float(saved[2]) == round(length, 2)
          and saved[1].startswith(str(server.sessions["t"].scratch))
          and [c["type"] for c in content] == ["image", "text"]
          and len(times[1].split(", ")) == 8,
          f"record_video: a clip of the length asked for, and a sheet of 8 frames ({said})")
    said, bad, _ = tool("record_video", session="t", seconds=0.1, path="clips/one.mp4")
    times = re.search(r"at (.*) seconds; \d+x\d+", said)
    check(not bad and (tmp / "clips/one.mp4").stat().st_size
          and str(tmp / "clips/one.mp4") in said and 1 <= len(times[1].split(", ")) < 8,
          f"record_video: a short clip, saved at a path in the allow file's directory ({said})")
    mcp.RECORD_SIZE, size = 1, mcp.RECORD_SIZE
    try:
        said, bad, _ = tool("record_video", session="t", seconds=3)
    finally:
        mcp.RECORD_SIZE = size
    saved = re.match(r"Saved (.+?), ([\d.]+) seconds", said)
    check(not bad and float(saved[2]) < 2 and "it reached" in said,
          f"record_video: a clip stopped at its size, said ({said})")
    (tmp / "dir.mp4").mkdir()
    refused = [tool("record_video", session="t", seconds=secs, path=p, sound=a)[:2]
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
          "record_video: too long or no length, a path outside the allow file's directory, not an "
          ".mp4 or there already, and sound without audio, refused")
    said, bad, _ = tool("take_screenshot", session="t", vision="martian")
    check(bad and "deuteranopia" in said,
          "take_screenshot: a vision there isn't, refused saying which")
    # Read off a zoom as an agent would: the buttons, darker than the window
    # in a column left of any label, mapped back by the answer, are where an
    # unzoomed picture has them; and Save, clicked there, gets the focus
    # (Announce has it).
    def darker(content, cx, rows=None):
        pic = tmp / "pic.png"
        pic.write_bytes(base64.b64decode([c for c in content if c["type"] == "image"][0]["data"]))
        w, h = mcp.png_size(pic)
        gray = subprocess.run(["convert", str(pic), "-colorspace", "gray", "-depth", "8",
                               "gray:-"], capture_output=True, check=True).stdout
        column = [gray[row * w + cx] for row in rows or range(h)]
        return [m.span() for m in re.finditer("x+", "".join(".x"[max(column) - v > 8]
                                                          for v in column))], h
    tree, _, _ = tool("get_accessible_tree", session="t", window="a11y-test", bounds=True)
    (x, y, _, _), (ax, ay, aw, ah) = ([int(n) for n in re.search(
        rf'"{b}".*@(\d+),(\d+) (\d+)x(\d+)', tree).groups()] for b in ("Save", "Announce"))
    cx = x + 10
    _, _, whole = tool("take_screenshot", session="t", window="a11y-test")
    for region in ([x - 12, y - 12, x + aw + 12, ay + ah + 12],
                   [x - 12, y - 12, 10**30, ay + ah + 12],
                   [cx - 2, -50, cx + 2, ay + ah + 12]):
        said, bad, content = tool("take_screenshot", session="t", window="a11y-test",
                                  zoom=region)
        m = re.search(r"is (?:(\d+) \+ )?x/([\d.]+), (?:(\d+) \+ )?y/([\d.]+), with the same "
                      r"window\.$", said)
        ox, kx, oy, ky = (float(v or 0) for v in m.groups()) if m else (0, 1, 0, 1)
        top = max(0, region[1])
        want = [(top + a, top + b) for a, b in darker(whole, cx, range(
            top, min(darker(whole, cx)[1], region[3])))[0]]
        got = [(oy + a / ky, oy + b / ky) for a, b in darker(content, round((cx - ox) * kx))[0]]
        check(not bad and m and len(want) >= 3 and len(got) == len(want)
              and all(abs(a - b) <= 1 for w, g in zip(want, got) for a, b in zip(w, g)),
              f"take_screenshot: a zoom on {region}, its buttons mapped back to where they are "
              f"({said}; {got}, not {want})")
        if region[2] == x + aw + 12 and got:
            tool("click_element", session="t", window="a11y-test", x=cx, y=round(sum(got[0]) / 2))
            tree, _, _ = tool("get_accessible_tree", session="t", window="a11y-test")
            check('"Save" [focusable, focused]' in tree, "take_screenshot: Save read off a "
                  "zoom, clicked where the answer maps it")
    tool("click_element", session="t", window="a11y-test", x=ax + aw // 2, y=ay + ah // 2)
    tree, _, _ = tool("get_accessible_tree", session="t", window="a11y-test", bounds=True)
    check('"Announce" [focusable, focused]' in tree
          and re.search(r'^frame "a11y-test".* @0,0 ', tree, re.M),
          f"get_accessible_tree: with window, bounds in its x,y, as click_element's ({tree})")
    said, bad, _ = tool("click_element", session="t", window="a11y-test", name="Save|Announce")
    listed = re.findall(r'^  (?:push )?button ("\w+").* (@\d+,\d+ \d+x\d+)$', said, re.M)
    check(bad and len(listed) == 2 and all(re.search(rf'^ *(push )?button {b}.* {at}$', tree, re.M)
                                           for b, at in listed),
          f"click_element: two nodes matching listed with bounds in the window's x,y ({said})")
    refused = [tool("take_screenshot", session="t", window="a11y-test", **a)[:2]
               for a in (dict(zoom=[0, 0, 10]), dict(zoom="300x200+10+10"),
                         dict(zoom={"x": 1}), dict(zoom=[10, 0, 5, 5]),
                         dict(zoom=[0, 0, 5, 5], crop="5x5+0+0"),
                         dict(zoom=[0, 0, 5, 5], burst=2), dict(zoom=[700, 500, 900, 700]),
                         dict(crop="10x10+700+500"), dict(crop="10x10+700+500", burst=2),
                         dict(crop="100x100"), dict(crop="100x100", burst=2))]
    check(all(bad for _, bad in refused)
          and all("zoom is [x0, y0, x1, y1]" in said for said, _ in refused[:3])
          and "right of and below" in refused[3][0]
          and all("not with crop or burst" in said for said, _ in refused[4:6])
          and all("has none of the window 'a11y-test' in it, which is" in said
                  for said, _ in refused[6:9])
          and all("a crop is WIDTHxHEIGHT+X+Y" in said for said, _ in refused[9:]),
          f"take_screenshot: a zoom that isn't 4 corners, or with crop or burst, a region with "
          f"nothing taken in it, or a crop that isn't WIDTHxHEIGHT+X+Y, refused ({refused})")
    said, _, _ = tool("check_accessible_names", session="t", window="a11y-test", ignore="image")
    check(len(said.splitlines()) == 2
          and all(re.fullmatch(r'[a-z ]+ "" .*@\d+,\d+ \d+x\d+: [a-z ]+ with no name',
                               line) for line in said.splitlines())
          and "isn't one" in tool("check_accessible_names", session="t", ignore="(")[0],
          f"check_accessible_names: tree lines with bounds, ignoring images given as text ({said!r})")
    said, _, _ = tool("check_accessible_names", session="t", window="a11y-test", ignore=["image", '^panel ""'])
    check(len(said.splitlines()) == 1 and "button with no name" in said,
          f"check_accessible_names: ignore found in a tree line, by a path ({said!r})")
    said, bad, _ = tool("check_contrast", session="t", window="a11y-test", passes=True)
    check(not bad and "not measured, which doesn't fail" in said and "reports where" in said
          and re.search(r"^\d+ pass:\n.*: text passes at ", said, re.M),
          f"check_contrast: GTK 4, its text found and passing, the rest not measured, said ({said!r})")
    said, _, _ = tool("check_focus_order", session="t", window="a11y-test", visible=True,
                      ignore=['button "(Minimize|Maximize|Close)"'])
    check("focus not shown" in said and said.endswith("round again after 6\nfocus not shown on 1"),
          "check_focus_order: the order, and where the focus doesn't show")
    said, bad, _ = tool("check_focus_order", session="t", window="a11y-test", document=True)
    check(bad and "no web document" in said,
          f"check_focus_order: document, in a window with none, refused ({said!r})")
    if shutil.which("orca"):
        said, bad, _ = tool("get_speech", session="t", window="a11y-test")
        check(not bad and re.search(r'^(push )?button "Save" .*  says "Save (push )?button\.?"$', said, re.M),
              f"get_speech: what Orca says at each stop ({said!r})")
    said, bad, _ = tool("press_keys", session="t", keys="Tab Tab")
    check(not bad and said == "Pressed Tab, Tab.", "press_keys: keys given as text")
    unchecked, bad, _ = tool("click_element", session="t", name="Wrap lines", changes=True)
    said, _, _ = tool("click_element", session="t", name="Wrap lines", changes=True)
    check(not bad and re.search(r'^~ check box "Wrap lines" \[focusable, focused\], was '
                                r'\[focusable(, focused)?, checked\]$', unchecked, re.M)
          and re.search(r'\nChanged in the tree, read \d+\.\d s after, once the screen had '
                        r'held still for 0\.5 s:\n~ check box "Wrap lines" \[focusable, '
                        r'focused, checked\], was \[focusable, focused\]\Z', said),
          f"click_element: with changes, what it changed in the tree ({unchecked}; {said})")
    # A program that stops answering is said to, not its window gone.
    tool("start_session", name="z")
    tool("spawn_program", session="z", program="freeze")
    tool("wait_for", session="z", what="window", value="a11y-test", timeout=20)
    said, bad, _ = tool("click_element", session="z", name="Freeze", changes=True)
    check(not bad and re.match(r'Clicked (push )?button "Freeze"', said) and "\n- " not in said
          and re.search(r'^! application "" \(pid \d+\) didn\'t answer', said, re.M),
          f"click_element: with changes, a program that stops answering, said ({said})")
    tool("stop_session", session="z")
    # Held keys and buttons, across calls: on Wayland too, since a session
    # keeps its input connection.
    # A session of its own: in "t", the window clicked by name keeps the keyboard.
    tool("start_session", name="h")
    tool("spawn_program", session="h", program="held")
    tool("wait_for", session="h", what="window", value="held", timeout="20")
    tool("wait_for", session="h", what="stable", window="held")
    said, bad, _ = tool("wait_for", session="h", what="change", window="held", timeout=1)
    check(bad and "to change" in said, f"wait_for: change gives up on a still window ({said})")
    _, _, content = tool("take_screenshot", session="h", window="held")
    png = next(c for c in content if c["type"] == "image")
    (tmp / "held.png").write_bytes(base64.b64decode(png["data"]))
    said, bad, _ = tool("wait_for", session="h", what="match", value="held.png", window="held")
    refused = [tool("wait_for", session="h", what="match", value=v)[:2]
               for v in ("/etc/passwd", "../" * 20 + "etc/hostname", "nope.png")]
    check(not bad and "matches held.png" in said and said.endswith(", at 0,0.")
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
    check(not bad and said == "The line:\n| beacon_claimed 2"
          and all(bad and "by its path from there" in said for said, bad in refused)
          and later_bad and "outside" in later,
          f"wait_for: line, a new one, in a file in the allow file's directory only ({said})")
    with open(tmp / "game.log", "a") as log:
        threading.Timer(1, lambda: log.write("run_end 3\n") and log.flush()).start()
        said, bad, _ = tool("record_video", session="h", until_line="run_", file="game.log",
                            tail=0.5)
    saved = re.match(r"Saved (.+?), ([\d.]+) seconds", said)
    capped, capped_bad, _ = tool("record_video", session="h", until_line="nope", file="game.log",
                                 seconds=0.5)
    threading.Timer(0.5, (tmp / "later2.log").symlink_to, ["/etc/hostname"]).start()
    refused = [tool("record_video", session="h", until_line="x", file=f)[:2]
               for f in ("later2.log", "/etc/hostname", None)]
    check(not bad and saved and 1.2 <= float(saved[2]) < 2.5
          and said.endswith("It ended on the line:\n| run_end 3")
          and not capped_bad and "No line matching 'nope' came in its 0.5 seconds" in capped
          and "outside" in refused[0][0] and "by its path from there" in refused[1][0]
          and "needs a file" in refused[2][0] and all(bad for _, bad in refused)
          and "tail goes with until_line" in tool("record_video", session="h", seconds=1,
                                                  tail=1)[0],
          f"record_video: until_line, ended by a new line, and tail; else by seconds; its file in "
          f"the allow file's directory only ({said})")
    steps = [("click_element", dict(x=50, y=50, window="held")),
             ("press_keys", dict(keys="w", state="down")),
             ("click_element", dict(x=60, y=60, window="held", state="down")),
             ("click_element", dict(x=60, y=60, window="held", state="up")),
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
          f"press_keys and click_element: held down across calls, and for a while ({events})")
    said, bad, _ = tool("press_keys", session="h", keys="w", hold=1, state="down")
    check(bad and "not both" in said, "press_keys: hold and state together, refused")
    said, bad, _ = tool("press_keys", session="h", keys="w", hold=120)
    check(bad and "0 to 60" in said, "press_keys: a hold too long, refused")
    said, bad, _ = tool("click_element", session="h", x=5, y=5, double=True, hold=1)
    check(bad and "not together" in said, "click_element: double with hold, refused")
    tool("stop_session", session="h")
    tool("start_session", name="l")
    tool("spawn_program", session="l", program="long")
    tool("wait_for", session="l", what="window", value="a11y-test", timeout="20")
    before, before_bad, _ = tool("click_element", session="l", name="Row 25", timeout=1)
    said, bad, _ = tool("scroll_wheel", session="l", name="Row 0", dy=10)
    tool("wait_for", session="l", what="stable", window="a11y-test")
    after, after_bad, _ = tool("click_element", session="l", name="Row 25")
    back, back_bad, _ = tool("scroll_wheel", session="l", name="Row 25", dy=-10)
    tool("wait_for", session="l", what="stable", window="a11y-test")
    labeled = boxes(tool("take_screenshot", session="l", window="a11y-test", labels=True)[0])
    check("Row 0" in labeled and "Row 25" not in labeled,
          f"take_screenshot: labels only on rows in view, not those scrolled out ({labeled})")
    refused = [tool("scroll_wheel", session="l", name="Row 0")[:2],
               tool("scroll_wheel", session="l", x=5, y=5, dy=1000)[:2]]
    check(before_bad and "outside its window" in before and not bad
          and said.startswith('Scrolled 10 down over label "Row 0"') and not after_bad
          and not back_bad and back.startswith('Scrolled 10 up over label "Row 25"')
          and all(bad for _, bad in refused) and "nothing to scroll" in refused[0][0]
          and "-100 to 100, not 1000" in refused[1][0],
          f"scroll_wheel: by a node's name, a row past the end into view, and back; none or too "
          f"far, refused ({said}; {back})")
    mcp.LINES, most = 5, mcp.LINES
    try:
        said, bad, _ = tool("get_accessible_tree", session="l")
    finally:
        mcp.LINES = most
    check(not bad and len(said.splitlines()) == 6
          and re.fullmatch(r"… \d+ more lines: narrow it with window", said.splitlines()[-1]),
          f"get_accessible_tree: capped, saying how many more and how to narrow it ({said})")
    tool("stop_session", session="l")
    # By the name it was spawned by, a program behind a shell, whose window's
    # title isn't that; and a title that isn't there, the window that is said.
    for wayland in (False, True):
        tool("start_session", name="w", wayland=wayland)
        said, _, _ = tool("spawn_program", session="w", program="wrapped")
        up, up_bad, _ = tool("wait_for", session="w", what="window", value="wrapped",
                             timeout=20)
        none, none_bad, _ = tool("wait_for", session="w", what="window", value="a11y",
                                 timeout=0)
        tool("spawn_program", session="w", program="daemon")
        daemon, daemon_bad, _ = tool("wait_for", session="w", what="window", value="daemon",
                                     timeout=20)
        tool("stop_session", session="w")
        check("wait_for window 'wrapped'" in said and not up_bad
              and up.startswith("'a11y-test' is up") and none_bad
              and 'there are: "a11y-test"' in none
              and not daemon_bad and daemon.startswith("'daemon' is up"),
              f"wait_for window{' on Wayland' if wayland else ''}: by the name spawned, or a "
              f"daemon's by its title, and giving up, the windows there are "
              f"({up}; {none}; {daemon})")
    if os.access("/dev/uinput", os.W_OK) and shutil.which("bwrap") and subprocess.run(
            ["bwrap", "--dev-bind", "/", "/", "true"], capture_output=True).returncode == 0:
        said, bad, _ = tool("start_session", name="g", a11y=False, gamepads=1)
        check(not bad and "1 gamepad" in said, f"start_session with a gamepad ({said})")
        said, _, _ = tool("spawn_program", session="g", program="padread")
        pid = re.search(r"pid (\d+)", said)[1]
        log, end = tmp / "pad.log", time.monotonic() + 10
        while not (log.exists() and "ready" in log.read_text()) and time.monotonic() < end:
            time.sleep(0.1)
        # Once it's ready: before then, bwrap's child may not be the program yet.
        check(Path(f"/proc/{pid}/comm").read_text().strip() != "bwrap"
              and "pad_read" in Path(f"/proc/{pid}/cmdline").read_text(),
              "spawn_program in a gamepad session: the program's own pid, not the sandbox's")
        said = [tool("use_gamepad", session="g", action="press", buttons="a"),
                tool("use_gamepad", session="g", action="stick", which="left", x=1, y=0, hold=0.1),
                tool("use_gamepad", session="g", action="press", buttons="y", state="down"),
                tool("use_gamepad", session="g", action="neutral")]
        time.sleep(0.3)
        # The kernel passes on only what changes: y stays 0, so says nothing.
        got = [line for line in log.read_text().splitlines() if line.startswith(("key", "abs"))]
        check(not any(bad for _, bad, _ in said)
              and got == ["key 304 1", "key 304 0", "abs 0 32767", "abs 0 0",
                          "key 308 1", "key 308 0"],
              f"use_gamepad: press, a stick held a while, a button left down, neutral ({got})")
        said, bad, _ = tool("use_gamepad", session="g", action="press", buttons="z")
        check(bad and "no button z" in said, "use_gamepad: a button there isn't, said")
        refused = [tool("use_gamepad", session="g", action="press", buttons="a", pad=7),
                   tool("use_gamepad", session="g", action="stick", which="left", x=5, y=0),
                   tool("use_gamepad", session="g", action="trigger", which="left", value=-1),
                   tool("start_session", name="g9", gamepads=9)]
        check(all(bad for _, bad, _ in refused) and "1 to 4, not 7" in refused[0][0]
              and "-1 to 1, not 5" in refused[1][0] and "0 to 4, not 9" in refused[3][0],
              "use_gamepad: a pad, stick, trigger or count out of range, refused")
        tool("stop_session", session="g")
    else:
        print("skip  use_gamepad: no writable /dev/uinput or no bwrap")
    if shutil.which("pipewire") and shutil.which("wireplumber") and shutil.which("paplay"):
        with wave.open(str(tmp / "tone.wav"), "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(48000)
            w.writeframes(b"".join(struct.pack("<hh", v, v) for v in (
                int(8000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(24000))))
        tool("start_session", name="a", a11y=False, audio=True)
        said, bad, _ = tool("record_audio", session="a", action="stop")
        check(bad and "action start first" in said, "record_audio: stop before start, refused")
        tool("record_audio", session="a", action="start")
        tool("spawn_program", session="a", program="tone")
        time.sleep(1.5)
        said, bad, _ = tool("record_audio", session="a", action="stop")
        check(not bad and "No gaps, clipping or silence." in said
              and re.search(r"peak -12\.\d dBFS", said),
              f"record_audio: what a program played, heard ({said})")
        tool("spawn_program", session="a", program="late_tone")
        said, bad, content = tool("record_video", session="a", seconds=1.5, sound=True)
        check(not bad and said.startswith("Saved") and "No gaps, clipping or silence." in said
              and re.search(r"peak -12\.\d dBFS", said) and content[0]["type"] == "image",
              f"record_video: with sound, what was heard said too ({said})")
        tool("stop_session", session="a")
        said, bad, _ = tool("record_audio", session="t", action="start")
        check(bad and "start a session with audio" in said, "record_audio: no audio, refused")
    else:
        print("skip  record_audio: no PipeWire, WirePlumber or paplay")
    # Names as a calculator's buttons have them: as they are first, then as regexes.
    tool("start_session", name="n")
    tool("spawn_program", session="n", program="keys")
    tool("wait_for", session="n", what="window", value="a11y-test", timeout=20)
    said = [tool("click_element", session="n", name=v, timeout=0.5)[:2]
            for v in (".", "+", "(Draft)")]
    check(all(not bad and f'button {json.dumps(v)} ' in got
              for (got, bad), v in zip(said, (".", "+", "(Draft)"))),
          f"click_element: a name as it is, before it as a regex ({said})")
    refused = [tool("click_element", session="n", timeout=0.5, **a)[:2]
               for a in (dict(name="[12]"), dict(name="Draft"), dict(name="*"),
                         dict(name="1", role="("))]
    check(all(bad for _, bad in refused)
          and re.search(r'2 nodes match.*\n  [a-z ]*button "1" .*@\d+,\d+ \d+x\d+\n  '
                        r'[a-z ]*button "2" .*@.*\nNarrow it with a role or a window', refused[0][0])
          and 'The nearest names: "(Draft)"' in refused[1][0]
          and "'*' (exactly: it isn't a regex" in refused[2][0]
          and "role '(' (exactly: it isn't a regex" in refused[3][0]
          and "nearest" not in refused[3][0],
          f"click_element: two matches with where they are, the nearest names to none, and what "
          f"isn't a regex, said ({refused})")
    said, bad, _ = tool("wait_for", session="n", what="sleep")
    check(bad and said.startswith("wait_for waits for window") and "not 'sleep'" in said,
          f"wait_for: a choice there isn't, said before what it lacks ({said})")
    tool("stop_session", session="n")
    tool("start_session", name="r")
    tool("spawn_program", session="r", program="ready")
    tool("wait_for", session="r", what="window", value="a11y-test", timeout=20)
    said = [tool("click_element", session="r", name="Soon", window="a11y-test", timeout=1)[:2],
            tool("click_element", session="r", name="Soon", force=True)[:2],
            tool("scroll_wheel", session="r", name="Soon", dy=1, force=True)[:2]]
    check(said[0][1] and re.match(r'gave up after 1s: [a-z ]*button "Soon" \[focusable, disabled\] '
                                  r'isn\'t enabled$', said[0][0])
          and not said[1][1] and not said[2][1],
          f"click_element: a disabled button waited for and said, and with force clicked, as "
          f"scroll_wheel's is ({said})")
    said = [tool("click_element", session="r", name="Unplaced", action=v)[:2]
            for v in ("click", "nope", "true", "", 0)]
    check(not said[0][1] and re.match(r'Did the action "click" of [a-z ]*button "Unplaced" '
                                      r'\[focusable\], not a click\.$', said[0][0])
          and said[1][1] and said[1][0].endswith(
              '"Unplaced" [focusable] has no "nope"; its actions: click (action NAME does one)')
          and said[2][1] and said[2][0].endswith(
              '"Unplaced" [focusable] takes an action by its name; its actions: click '
              '(action NAME does one)')
          and all(bad and "an action's name" in got for got, bad in said[3:]),
          f"click_element: with action, a node with no position done by its action, and one "
          f"it hasn't, one not named or empty, said ({said})")
    tool("stop_session", session="r")
    tool("start_session", name="f")
    tool("spawn_program", session="f", program="fading")
    tool("wait_for", session="f", what="window", value="a11y-test", timeout=20)
    said, bad, _ = tool("check_contrast", session="f", window="a11y-test")
    check(not bad and re.search(r"^1 changing, which doesn't fail:\n.*: text changing: "
                                r"\d\.\d\d:1 to \d+\.\d\d:1 \(at least 4\.5\); found .*\n\d+ not measured", said, re.M)
          and "Its contrast changed over 6 pictures" in said,
          f"check_contrast: text fading, said apart with its range, and why ({said!r})")
    tool("stop_session", session="f")
    tool("start_session", name="u", a11y=False, usage=True)
    said, bad, _ = tool("stop_session", session="u")
    check(not bad and "used, over" in said and "peak memory" in said,
          f"stop_session: what a session with usage used, said ({said})")
    said, bad, _ = tool("click_element", session="t", nmae="Save")
    check(bad and "doesn't take nmae" in said, "an argument a tool doesn't take: said")
    unknown = [rpc("tools/call", {"name": n, "arguments": {}})["error"]
               for n in ("click", "nope", ["click"])]
    check([e["code"] for e in unknown] == [-32602] * 3
          and [e["message"] for e in unknown]
          == ["Unknown tool: click; it's click_element now", "Unknown tool: nope",
              "Unknown tool: ['click']"],
          f"a tool it doesn't have: a protocol error, an old name's new one said ({unknown})")
    said, bad, _ = tool("get_program_output", session="t", program="app")
    check(not bad and "still running" in said, "get_program_output")
    tool("spawn_program", session="t", program="talk")
    time.sleep(1)
    said, bad, _ = tool("get_program_output", session="t", program="talk")
    check(not bad and said.split("\n", 1)[1]
          == "| one\n| two\n| " + "x" * 500 + "… (100 more characters)",
          f"get_program_output: each line fenced, a long one cut ({said!r})")
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
                      call_q(3, "spawn_program", session="io", program="reader"),
                      call_q(4, "spawn_program", session="io", program="app"),
                      {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": [1]},
                      {"jsonrpc": "2.0", "method": "notifications/cancelled",
                       "params": {"requestId": [2]}},
                      {"jsonrpc": "2.0", "id": [1], "method": "tools/list"},
                      {"jsonrpc": "2.0", "id": 5, "method": "ping"})
check([a["id"] for a in answers] == [1, 2, 3, 4, [1], 5]
      and not any(a["result"].get("isError") for a in answers[1:4]),
      "over stdio: an answer a request, a program reading stdin notwithstanding, and "
      "malformed cancellations and ids taken in stride")
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
                          call_q(2, "spawn_program", session=how, program="app"),
                          call_q(3, "spawn_program", session=how, program="where"),
                          call_q(4, "wait_for", session=how, what="stable"),
                          call_q(5, "get_program_output", session=how, program="where"))
    app, where = pid_of(answers[1]), pid_of(answers[2])
    scratch = answers[4]["result"]["content"][0]["text"].splitlines()[1].removeprefix("| ")
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
# A long call, cancelled: a ping on the way answered at once, progress
# while it waits, no answer to it, and the session still there after.
proc, _ = serve(call_q(1, "start_session", name="long"))
wait_q = call_q(2, "wait_for", session="long", what="window", value="never", timeout=20)
wait_q["params"]["_meta"] = {"progressToken": "w"}
sent = time.monotonic()
for q in (wait_q, {"jsonrpc": "2.0", "id": 3, "method": "ping"}):
    proc.stdin.write(json.dumps(q) + "\n")
proc.stdin.flush()
got = []
while select.select([proc.stdout], [], [], 7)[0]:
    got.append((json.loads(proc.stdout.readline()), time.monotonic() - sent))
    if got[-1][0].get("method") == "notifications/progress":
        break
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                             "params": {"requestId": 2}}) + "\n")
proc.stdin.write(json.dumps(call_q(4, "wait_for", session="long", what="stable")) + "\n")
proc.stdin.flush()
while select.select([proc.stdout], [], [], 30)[0]:
    got.append((json.loads(proc.stdout.readline()), time.monotonic() - sent))
    if got[-1][0].get("id") == 4:
        break
ids = [m.get("id", m.get("method")) for m, _ in got]
check(ids == [3, "notifications/progress", 4] and got[0][1] < 1
      and got[1][0]["params"]["progressToken"] == "w" and got[1][1] < 10
      and not got[2][0]["result"].get("isError") and got[2][1] < 15,
      f"over stdio: a ping answered during a long call, progress, and a cancelled call "
      f"unanswered, its session left usable ({got})")
# Requests piped in, stdin closed after them: each answered, a null line too.
piped = subprocess.run([sys.argv[1], "mcp"], capture_output=True, text=True, timeout=120,
                       input="".join(json.dumps(q) + "\n" for q in (
                           {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                           None, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                           call_q(3, "start_session", name="piped"))))
ids = [json.loads(line).get("id") for line in piped.stdout.splitlines()]
check(ids == [1, None, 2, 3] and "is up" in piped.stdout,
      f"over stdio: requests piped in before stdin closes are all answered ({ids})")
# From a virtualenv that can't see the system's PyGObject, as plain pipx
# install and uvx make: the session starts, saying what won't work and why.
venv = tmp / "venv"
subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
blind = subprocess.run([str(venv / "bin/python"), sys.argv[1], "mcp"], capture_output=True,
                       text=True, timeout=120, input="".join(json.dumps(q) + "\n" for q in (
                           call_q(1, "start_session", name="blind"),
                           call_q(2, "get_accessible_tree", session="blind"))))
started, tree = (json.loads(line)["result"] for line in blind.stdout.splitlines())
check("is up" in started["content"][0]["text"] and "Note: the accessibility tools "
      "(get_accessible_tree, check_*, list_announcements, and anything by a node's name) "
      "won't work: needs PyGObject and AT-SPI from the system, which this virtualenv can't "
      "see: make it with --system-site-packages." in started["content"][0]["text"]
      and tree["isError"] and "--system-site-packages" in tree["content"][0]["text"],
      f"start_session with no PyGObject in sight: up, saying what won't work and how to fix "
      f"it ({started}; {tree})")
# stdin closed during a long click by name: called off, not waited out.
proc.stdin.write(json.dumps(call_q(5, "click_element", session="long", name="never",
                                   timeout=60)) + "\n")
proc.stdin.flush()
time.sleep(1)
sent = time.monotonic()
proc.stdin.close()
proc.wait(60)
check(time.monotonic() - sent < 20, f"over stdio: stdin closed during a long call stops the "
      f"server then ({time.monotonic() - sent:.1f} s)")
proc, answers = serve(call_q(1, "start_session", name="hup"),
                      call_q(2, "spawn_program", session="hup", program="app"))
pid = int(re.search(r"pid (\d+)", answers[1]["result"]["content"][0]["text"])[1])
proc.send_signal(signal.SIGHUP)
proc.wait(60)
check(until_gone(pid), "SIGHUP: its sessions stop")
shutil.rmtree(tmp)
sys.exit(1 if fails else 0)
