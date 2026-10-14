"""Pre-2.0 companion tool names, read from workflows saved before the rename (HLD §16 D-15, [M4]).

In 2.0 ten companion skills renamed their MCP tools verb-first and expose only
the new names. A workflow saved earlier — a ``workflows.db`` row, a YAML file,
an undo descriptor — still names the old one. ``WorkflowStep`` calls
:func:`current_tool_name` for its ``tool`` and ``rollback_tool`` when it is
built, and every path builds one (templates, the YAML loader, drafts,
``create_workflow``, loading a stored row), so review, the approval gate,
dispatch and rollback all see the 2.0 name.

The map is vmware-policy's, keyed per skill: ``pool_list`` is AVI's
``list_lb_pools`` and VDI's ``list_desktop_pools``, and vks's old
``list_vm_snapshots`` is aiops/monitor's new name. No server's old names
overlap its new ones, so translating a name that is already new is a no-op.
"""

from __future__ import annotations

import logging

from vmware_policy import renames

_log = logging.getLogger("vmware-pilot.renames")


def _server(skill: str) -> str:
    """``"aiops"`` / ``"vmware-aiops"`` / ``"log_insight"`` -> ``"vmware-<skill>"``."""
    bare = skill.strip().lower().removeprefix("vmware-").replace("_", "-")
    return f"vmware-{bare}"


def current_tool_name(skill: str, tool: str) -> str:
    """``tool``'s 2.0 name in ``skill``; ``tool`` unchanged when it was not renamed there."""
    if not isinstance(skill, str) or not isinstance(tool, str) or not tool:
        return tool
    new = renames.table().get(_server(skill), {}).get(tool)
    if new is None:
        return tool
    _log.info("step names %s.%s, renamed in 2.0; running it as %s", skill, tool, new)
    return new
