"""IEnvProvider — the seam a deploy plugs "log in, get the variables" into (#750).

A tool declares the environment variables it wants by NAME. Someone still has
to produce them, and for a whole class of them the person at the panel cannot:
they know their own account and password, while the tool wants the token those
exchange for. This is where a deploy puts that exchange.

**The tool never names an implementation here.** It declares variable names; an
implementation declares the names it produces; the panel joins the two on the
name. That direction is deliberate. A tool naming a provider would be writing
into an identifier namespace shared with a second party it has never met, and
the landing site of a collision is a login dialog — one deploy's ``sap-login``
reaching a different SAP than the tool's author meant, with the user typing a
password into it and nothing going wrong loudly. Worse in kind, it would let a
third party choose which credential OUR interface asks for, in a sentence
carrying our credibility. A variable name is something the tool's own code
already had to write, so joining on it invents no namespace at all.

The trust boundary is: **password -> this implementation -> variable -> tool.**
The credential is handled by the deploy's own code and is never stored, never
logged, and never returned. What the tool eventually sees is the product.

⚠️ The product is not narrow: ``tooling.registry._tool_env`` hands the item's
whole environment to every dispatched tool, so a tool that never asked for a
variable still receives it. That is the pre-existing shape of `env_vars` rather
than something this seam introduces, but an implementation author should know
that what they mint is visible to every tool the item runs.

Nothing here is a gate. A deploy with no implementations simply has no buttons;
every variable can still be typed by hand, which is the path that always works.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class InputField:
    """One thing to ask the person for, so the provider can render its own form.

    The provider owns this list because only it knows what its system needs —
    an account and a password, a single API key, a host plus a certificate. A
    tool has no business describing the form for a login it does not own."""

    name: str
    label: str
    secret: bool = False
    """Render masked. A convenience for the person typing, NOT a security
    property: the value still travels to the server, and what protects it is
    that nothing stores it."""


class IEnvProvider(abc.ABC):
    """One way to obtain environment variables from something a person types.

    Resolved at startup from the ``server.env_providers`` dotted paths, so an
    implementation must be constructible with no arguments.
    """

    @property
    @abc.abstractmethod
    def id(self) -> str:
        """A stable key for this provider, used by the panel to name which one
        to run. The deploy owns both ends of this name — it writes the
        implementation and lists it in config — so unlike a tool-authored
        identifier there is no one to collide with."""

    @property
    @abc.abstractmethod
    def label(self) -> str:
        """What the button says, e.g. "SAP production login". Shown next to the
        variables it will fill, so a person can tell two similar systems apart
        BEFORE typing a password into one of them."""

    @property
    @abc.abstractmethod
    def produces(self) -> frozenset[str]:
        """The variable names this can fill — the ONLY join with a tool.

        Names only, never values: this is answered while drawing a settings
        panel, so it must be cheap, side-effect free, and safe to ask at any
        time. The values come from ``resolve``."""

    @property
    @abc.abstractmethod
    def inputs(self) -> tuple[InputField, ...]:
        """What the dialog collects before calling ``resolve``."""

    @abc.abstractmethod
    async def resolve(self, values: dict[str, str]) -> dict[str, str]:
        """Exchange what the person typed for environment variables.

        ``async`` because the case this exists for is a network call to somebody
        else's login. **Bound your own latency**: the platform cannot know what
        is reasonable for your gateway, and a bound chosen here would refuse
        exchanges that were merely slow.

        Raising is reported to the person, and nothing is filled in. That is the
        whole failure mode — the panel keeps whatever it had, because this
        writes nothing anywhere: the caller puts the result into the form and
        the person still presses Save.

        WHAT YOU RETURN IS WRITTEN AS-IS, including names no tool declared.
        Filtering to declared names would drop exactly what an incomplete
        declaration most needs to keep.

        ``values`` holds the credential. Do not log it, do not persist it, and
        do not put it in the exception you raise.

        ⚠️ **A returned value may not contain a newline.** The panel edits these
        as ``.env`` text, one line per variable, so a multi-line value would
        read back as its first line — silently, leaving someone with 30
        characters of certificate header saved as their credential. The panel
        refuses such a result whole and names the variable rather than storing
        part of it. If what you mint is a PEM, this seam cannot deliver it; give
        it to the tool some other way."""
