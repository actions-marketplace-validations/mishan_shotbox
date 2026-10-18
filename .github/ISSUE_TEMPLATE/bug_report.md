---
name: Bug report
about: shotbox did something wrong, or failed when it shouldn't have
title: ''
labels: bug
assignees: ''

---

**What happened?**
What you ran, and what it printed or did. Paste the exact command and its
output.

```
$ shotbox ...
```

**What did you expect?**

**How to make it happen again**
The smallest steps that do it, ideally with a program anyone can run
(gnome-text-editor, zenity, a short script).

**Where it ran**
- shotbox version (`pip show shotbox` or `pipx list`):
- How it's used: command line, Python API, MCP server (which client), Node
  helpers or the GitHub Action:
- Distribution and version:
- X11 or `--wayland`, and other session options (`--a11y`, `--audio`, ...):
- The program's toolkit and version, if it's about one (GTK 4.24, Qt 6, Chromium):

**`shotbox doctor`**

```
(its output)
```

**Anything else**
Screenshots, recordings, logs. A picture or log of another program's
window may show what's on your desktop: check before you attach it.

For a security problem, use [private reporting](https://github.com/mishan/shotbox/security/advisories/new)
rather than an issue.
