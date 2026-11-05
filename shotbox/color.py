"""Color for a person reading a terminal. Never into a pipe or a file, so
what a program reads stays as it was; NO_COLOR turns it off everywhere,
and FORCE_COLOR on (https://no-color.org, https://force-color.org)."""
import os

STYLES = {"fail": "31", "pass": "32", "warn": "33", "name": "36", "bold": "1", "dim": "2"}


def on(stream):
    """Whether what's written to `stream` is colored."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return os.environ.get("TERM") != "dumb" and stream.isatty()


def paint(stream, text, style):
    """`text` in `style`, one of STYLES, if `stream` is colored."""
    return f"\033[{STYLES[style]}m{text}\033[0m" if text and on(stream) else text
