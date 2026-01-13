"""Utility functions for diff_tool package."""


def is_url(s: str) -> bool:
    """Return True if *s* looks like an HTTP/HTTPS URL.

    Simple check based on the scheme prefix.
    """
    return s.startswith(("http://", "https://"))
