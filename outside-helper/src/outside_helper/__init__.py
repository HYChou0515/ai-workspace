"""The outside helper (docs/plan-outside-helper.md): a program on the person's
PC that drives their Chrome to fetch pages for the AI workspace — the only
thing on that PC that reaches the internet is the browser."""

from .version import PROTOCOL, __version__

__all__ = ["PROTOCOL", "__version__"]
