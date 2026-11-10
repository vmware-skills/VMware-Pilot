"""Interpret dispatch frames without mistaking preview or unknown effects for success."""

from __future__ import annotations

import json


from typing import Any


from vmware_pilot.renames import _server


_PREVIEW_ONLY = (
    "step only previewed; nothing was changed. The tool returned "
    "action='preview' because it was called without confirm=True — the template "
    "must pass confirm=True (Pilot's approve step is the human decision). The "
    "preview's blast_radius is kept in this step's result."
)


def _top_level_payloads(result: Any) -> list[dict[str, Any]]:
    """The dicts a dispatch result may carry at its top level.

    A plain dict, or an MCP ``CallToolResult``'s ``structuredContent`` (and the
    ``{"result": ...}`` wrapper FastMCP puts around non-object returns), or its
    single text block when that block is a JSON object. Never recurses: a
    nested ``action`` is data.
    """
    if isinstance(result, dict):
        return [result]
    payloads: list[dict[str, Any]] = []
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        payloads.append(structured)
        if isinstance(structured.get("result"), dict):
            payloads.append(structured["result"])
    content = getattr(result, "content", None)
    if isinstance(content, list) and len(content) == 1:
        text = getattr(content[0], "text", None)
        if isinstance(text, str) and text.lstrip().startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                payloads.append(parsed)
    return payloads


_EXIT_CODE_TOOLS = frozenset(
    {
        ("aiops", "run_vm_guest_command"),
        ("aiops", "run_vm_guest_command_with_output"),
    }
)


def _guest_payloads(result: Any, skill: str, tool: str) -> list[dict[str, Any]]:
    bare = _server(skill).removeprefix("vmware-") if isinstance(skill, str) else ""
    return _top_level_payloads(result) if (bare, tool) in _EXIT_CODE_TOOLS else []


def _guest_unknown(result: Any, skill: str, tool: str) -> str | None:
    """A guest command that may still be running: timed out, or its process vanished.

    aiops does not kill the guest process on timeout, and exit_code -1 means it
    could not read one — either way the command may still be changing the guest.
    """
    for payload in _guest_payloads(result, skill, tool):
        if payload.get("timed_out") is True or payload.get("exit_code") == -1:
            return (
                f"{tool}: the guest command timed out or its process could not be read "
                f"(exit_code {payload.get('exit_code')!r}); it may still be running, so its "
                "effect is UNKNOWN — check the guest before retrying or rolling back."
            )
    return None


def _guest_failure(result: Any, skill: str, tool: str) -> str | None:
    """A guest command that ran to completion and exited non-zero."""
    for payload in _guest_payloads(result, skill, tool):
        code = payload.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            stderr = str(payload.get("stderr") or "")[:300]
            return f"{tool}: the command exited with exit_code {code}. stderr: {stderr}"
    return None


def _returned_failure(result: Any, skill: str = "", tool: str = "") -> str | None:
    """Return the failure message if this dispatch result reports failure.

    Only a TOP-LEVEL, non-empty ``error`` counts: ``{"error": None}`` is an
    explicit "no error", and an ``error`` nested inside a row (an alarm's
    ``error_count``, say) is data, not a failed call. A TOP-LEVEL
    ``"action": "preview"`` is a failure too: the tool changed nothing. So is a
    ``-> list`` tool's error shape, a list holding exactly one ``{"error": ...}``
    row. A string is never read as failure (see the note above).
    """
    if any(p.get("action") == "preview" for p in _top_level_payloads(result)):
        return _PREVIEW_ONLY
    if getattr(result, "isError", False):
        # Unwrap the content block if there is one: the skill's own teaching
        # message is the useful part, not the repr of the transport object.
        content = getattr(result, "content", None)
        if isinstance(content, list) and content and getattr(content[0], "text", None):
            return str(content[0].text)
        return str(content or "the tool reported isError")
    for payload in _top_level_payloads(result):
        err = payload.get("error")
        if err not in (None, "", [], {}):
            return str(err)
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
        err = result[0].get("error")
        if err not in (None, "", [], {}):
            return str(err)
    return _guest_failure(result, skill, tool)
