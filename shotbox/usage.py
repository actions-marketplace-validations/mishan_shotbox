"""What a session's programs used, for --usage: their peak memory, all of
them at once, and their CPU time.

Where the user's systemd can make scopes, each program runs in one, in a
slice of the session's, whose memory.peak and cpu.stat count every
process it ever had, a spike that lasts a moment included. Elsewhere, and
always for the session's own processes (its display, buses and sound
server), /proc is read five times a second: a peak between two reads is
missed, and so is the CPU time of a process that comes and goes between
them, which the report says.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path


# What systemd-run finds the user's systemd by.
BUS = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")


class Sampler:
    """Reads /proc five times a second, for the process groups it's given,
    by kind, and whatever they start, even in groups of its own: their
    resident memory together at its peak, and each process's CPU time, as
    last seen."""

    def __init__(self, every=0.2):
        self.every = every
        self.groups = {"programs": set(), "session": set()}
        self.peak = {kind: 0 for kind in self.groups}
        self.cpu = {kind: {} for kind in self.groups}
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def add(self, kind, pgid):
        self.groups[kind].add(pgid)

    def _run(self):
        while not self._done.wait(self.every):
            self.sample()

    def sample(self):
        page, tick = os.sysconf("SC_PAGE_SIZE"), os.sysconf("SC_CLK_TCK")
        procs = {}
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                stat = Path(f"/proc/{pid}/stat").read_text()
            except OSError:
                continue        # gone between listing and reading
            # After the command's name, in parentheses, which may hold any.
            fields = stat[stat.rindex(")") + 2:].split()
            procs[int(pid)] = (int(fields[1]), int(fields[2]),
                               int(fields[11]) + int(fields[12]), int(fields[21]))
        # A process is its group's kind, or its parent's: one that's made
        # a group, or a session, of its own (setsid) is still counted.
        kind_of = {pid: kind for pid, (_, pgrp, _, _) in procs.items()
                   for kind, groups in self.groups.items() if pgrp in groups}
        grew = True
        while grew:
            grew = False
            for pid, (ppid, _, _, _) in procs.items():
                if pid not in kind_of and ppid in kind_of:
                    kind_of[pid] = kind_of[ppid]
                    grew = True
        now = {kind: 0 for kind in self.groups}
        for pid, kind in kind_of.items():
            _, _, cpu, rss = procs[pid]
            now[kind] += rss * page
            self.cpu[kind][pid] = cpu / tick
        for kind in now:
            self.peak[kind] = max(self.peak[kind], now[kind])

    def read(self, kind):
        self.sample()
        return {"peak": self.peak[kind], "cpu": sum(self.cpu[kind].values()),
                "how": "sampled, resident"}

    def close(self):
        self._done.set()


class Slice:
    """A systemd slice of the user's for the session's programs, which
    `wrap` runs each in a scope of: made, and held while the session
    lasts, by a scope with nothing in it but a sleep. OSError where the
    user's systemd can't make one."""

    def __init__(self, name):
        # A dash in a slice's name is a slice it's in: shotbox, the session.
        self.name = f"shotbox-{name.removeprefix('shotbox-')}-programs.slice"
        self.holder = subprocess.Popen(self.wrap(["sleep", "infinity"]), start_new_session=True,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        end = time.monotonic() + 5
        while time.monotonic() < end and self.holder.poll() is None:
            try:
                cgroups = Path(f"/proc/{self.holder.pid}/cgroup").read_text()
            except OSError:
                cgroups = ""
            if "0::" not in cgroups:
                break           # cgroup v1 alone: no memory.peak
            cgroup = cgroups.split("0::", 1)[1].split("\n")[0]
            if self.name in cgroup:
                self.dir = Path("/sys/fs/cgroup" + cgroup).parent
                if (self.dir / "memory.peak").exists():
                    return
                break
            time.sleep(0.02)
        self.close()
        raise OSError("the user's systemd made no slice with memory.peak")

    def wrap(self, cmd, env=None):
        """`cmd`, to run in a scope of the slice's. systemd-run finds the
        user's systemd by the runtime dir and bus outside the session, and
        runs `cmd` with its own environment: the session's (`env`) are put
        back for it."""
        host = {k: os.environ[k] for k in BUS if k in os.environ}
        back = []
        if env is not None:
            # systemd-run's own, which the program's environment hasn't.
            back = ["env", "-uINVOCATION_ID", *[f"-u{k}" for k in host if k not in env],
                    *[f"{k}={env[k]}" for k in host if k in env]]
        # A description, so the user's journal says that, not the command
        # with the session's environment in it.
        return ["env", *[f"{k}={v}" for k, v in host.items()], "systemd-run", "--user",
                "--scope", "--quiet", f"--slice={self.name}",
                "--description=a program in a shotbox session", "--", *back, *cmd]

    def _holds(self, pid):
        try:
            return f"/{self.name}/" in Path(f"/proc/{pid}/cgroup").read_text()
        except OSError:
            return False

    def read(self):
        cpu = dict(line.split() for line in (self.dir / "cpu.stat").read_text().splitlines())
        return {"peak": int((self.dir / "memory.peak").read_text()),
                "cpu": int(cpu["usage_usec"]) / 1e6, "how": "cgroup, with page cache"}

    def close(self, finishing=list):
        """Stop the slice, and the session's above it, which systemd keeps
        after their last scope has gone. What `finishing()` lists (pids)
        goes first, each to a scope of its own outside them, to finish as
        it would without --usage: a recording's keeper and what it starts,
        or a command entered with --session."""
        try:
            self._spare(finishing)
        finally:
            self._stop()

    def _spare(self, finishing):
        # One a call: a pid gone by the time systemd reads it fails the
        # whole call. Each is looked for until it's out, as what's being
        # moved starts more.
        moved, end = set(), time.monotonic() + 2
        while time.monotonic() < end:
            inside = {p for p in finishing() if self._holds(p)}
            if not inside:
                return
            for pid in inside - moved:
                scope = self.name.removesuffix("-programs.slice") + f"-finishing-{pid}.scope"
                try:
                    if subprocess.run(["busctl", "--user", "call", "org.freedesktop.systemd1",
                                       "/org/freedesktop/systemd1",
                                       "org.freedesktop.systemd1.Manager", "StartTransientUnit",
                                       "ssa(sv)a(sa(sv))", scope, "fail", "2", "PIDs", "au", "1",
                                       str(pid), "Description", "s",
                                       "what a shotbox session left to finish", "0"],
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL,
                                      timeout=max(end - time.monotonic(), 0.1)).returncode == 0:
                        moved.add(pid)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            time.sleep(0.05)
        sys.stderr.write("shotbox: what was still finishing as the session ended, a recording "
                         "say, was stopped with it: busctl --user couldn't move it out of the "
                         "session's slice\n")

    def _stop(self):
        if self.holder.poll() is None:
            self.holder.kill()
            self.holder.wait()
        # What's left in it, killed first, as the session kills what its
        # programs leave: one that ignores SIGTERM would hold the stop 90 s.
        session = self.name.removesuffix("-programs.slice") + ".slice"
        for what in (["kill", "--signal=KILL"], ["stop"]):
            subprocess.run(["systemctl", "--user", *what, session],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def line(used):
    """`Session.usage()` as a line to print."""
    def one(u):
        return f"peak memory {u['peak'] / 2**20:.0f} MB, CPU {u['cpu']:.1f}s ({u['how']})"
    return (f"used, over {used['seconds']:.1f}s: its programs {one(used['programs'])}; "
            f"the session's own {one(used['session'])}")
