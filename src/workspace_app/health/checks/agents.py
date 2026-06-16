"""Agent function-calling probes — can this preset's model CALL TOOLS?

An agent whose model can't emit tool calls degrades into a chatbot
that narrates instead of acting (the workspace agent "discusses"
running commands, the KB agent claims it can't access the KB, the wiki
reader answers from memory instead of navigating the wiki). The probe
offers ONE neutral synthetic tool and asks the model to use it; the
assertion is that a tool call for that function comes back.

The tool/prompt are parameterised so a probe can mirror the capability
that matters for a given preset — a generic `lookup` by default; the
wiki reader is asked to `search_wiki`, the wiki maintainer to
`write_file` (act, don't narrate). Only the FACT of the call is
asserted, not its arguments, so a single synthetic parameter suffices.

Streams (per the always-stream rule) and accumulates the tool-call
deltas; never executes anything — the probe IS the assertion.
"""

from __future__ import annotations

from ..protocol import CheckResult, ISanityCheck

_LOOKUP_PROMPT = (
    "Use the lookup tool to find the handbook entry for 'reflow'. "
    "Do not answer from memory — call the tool."
)


class ToolCallCheck(ISanityCheck):
    def __init__(
        self,
        *,
        check_id: str,
        description: str,
        model: str | None,
        base_url: str | None = None,
        api_key: str | None = None,
        tool_name: str = "lookup",
        tool_description: str = "Look up a term in the internal handbook and return its entry.",
        param_name: str = "term",
        param_description: str = "term to look up",
        prompt: str = _LOOKUP_PROMPT,
    ) -> None:
        self.check_id = check_id
        self.description = description
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._tool_name = tool_name
        self._prompt = prompt
        self._tool = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        param_name: {"type": "string", "description": param_description}
                    },
                    "required": [param_name],
                },
            },
        }

    def run(self) -> CheckResult:
        import litellm

        if not self._model:
            return CheckResult(check_id=self.check_id, status="skip", detail="not configured")
        called: list[str] = []
        text_parts: list[str] = []
        for chunk in litellm.completion(
            model=self._model,
            messages=[{"role": "user", "content": self._prompt}],
            tools=[self._tool],
            stream=True,
            api_base=self._base_url,
            api_key=self._api_key,
        ):
            delta = chunk.choices[0].delta
            for tc in delta.tool_calls or []:
                name = getattr(getattr(tc, "function", None), "name", None)
                if name:
                    called.append(name)
            if delta.content:
                text_parts.append(delta.content)
        if self._tool_name in called:
            return CheckResult(check_id=self.check_id, status="pass", detail="tool call emitted")
        prose = "".join(text_parts).strip()[:120]
        return CheckResult(
            check_id=self.check_id,
            status="fail",
            detail="the model answered in prose instead of calling the offered tool — "
            f"agents on this model would narrate instead of acting (got: {prose!r})",
        )
