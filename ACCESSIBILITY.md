# Accessibility

shotbox checks other programs' accessibility, so its own should hold up
too. This says what it does for people using it, what's known not to,
and how to tell me about a barrier.

## The command and its output

- Everything shotbox says is plain text, one finding to a line, with no
  color, cursor movement or progress animation, so it reads the same in a
  screen reader, a braille display, a log or a pipe. Problems say what's
  wrong and what to do in words, not symbols.
- The checks' exit statuses carry the result: 0 passed, 1 failed, and 2
  couldn't check (as `a11y contrast` when nothing could be measured), so
  a script or CI doesn't need to read the text.
- `--json` gives the accessibility checks' findings in a form other tools
  can present however their user needs.

## The docs

- They're Markdown in the repository: headings in order, lists and tables
  rather than layout, and links that say where they go.
- Pictures have text alternatives; the logo, which is decoration, has an
  empty one.
- The README's demo is a GIF with captions burned in and a text
  alternative, and an MP4 with sound. Its sound scene is about a dropout
  you hear; the caption and the `listen` output on screen say what it
  found, but there's no audio description.

## Known gaps

- The demo has no audio description, and its captions are part of the
  picture rather than a text track.
- `shotbox term` makes pictures of terminals; what's in them is the
  program's, at the size and colors the person asks for.

## Reporting a barrier

Open an [issue](https://github.com/mishan/shotbox/issues/new) and label it
`accessibility`, or say so in the title: what you were doing, what you
use (screen reader, magnifier, keyboard only, voice control), and what
got in the way. Barriers in shotbox's own output and docs are bugs, and
come before new features.
