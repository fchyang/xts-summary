# Package marker – no runtime code needed.

"""Top‑level package for **summary_tool**.

Provides a ``__version__`` attribute that reflects the version declared in
``pyproject.toml`` (or ``setup.cfg``).  When the package is not installed – e.g.
when running directly from a source checkout – a sensible fallback is used.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # ``__name__`` evaluates to "summary_tool", which matches the distribution name.
    __version__: str = version(__name__)
except PackageNotFoundError:  # pragma: no cover – not installed in editable mode
    __version__ = "0.0.0-dev"

__all__ = ["__version__"]
