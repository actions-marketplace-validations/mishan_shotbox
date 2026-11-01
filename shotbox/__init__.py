"""shotbox, from Python: `Session` starts a sealed session, `here()` is the
display of the `shotbox run` a script is inside, and both are a `Screen`, to
wait on, drive and take pictures of. See shotbox/screen.py."""

__version__ = "0.10.0"

from .mutation import MutationReport, mutate  # noqa: E402
from .screen import Screen, SessionError, here  # noqa: E402
from .session import Session  # noqa: E402

__all__ = ["MutationReport", "Screen", "Session", "SessionError", "here", "mutate", "__version__"]
