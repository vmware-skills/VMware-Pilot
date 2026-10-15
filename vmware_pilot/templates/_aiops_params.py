"""Map template inputs onto the parameters vmware-aiops tools actually accept.

Pilot cannot import vmware-aiops, and aiops publishes closed schemas
(``additionalProperties: false``), so a key pilot invents is not ignored — it is
a rejection at dispatch. For ``clone_and_test`` that rejection arrives after the
staging clone already exists. These helpers refuse the same inputs while the
workflow is still being planned, when nothing has been created yet.

The accepted-key sets are copied from the aiops signatures (checked
2026-09-11); they are what to update if aiops changes them:

* ``reconfigure_vm(vm_name, cpu, memory_mb, target)`` — no ``memory_gb``.
* ``run_vm_guest_command(vm_name, command, username, arguments, password,
  working_directory, target, confirm)`` — ``username`` required, no default
  account. ``confirm`` is not a change_spec key: the template adds
  ``confirm=True`` itself (``_gated``), because the step runs after Pilot's
  approval gate.
"""

from __future__ import annotations

from typing import Any

#: change_spec keys that route to ``reconfigure_vm``. ``memory_gb`` is pilot's
#: convenience and is converted; aiops itself only takes ``memory_mb``.
RECONFIGURE_KEYS = frozenset({"cpu", "memory_mb", "memory_gb"})

#: change_spec keys a guest change may carry (``vm_name`` and ``target`` are
#: supplied by the template, so a spec may not override them).
GUEST_EXEC_KEYS = frozenset({"command", "username", "arguments", "password", "working_directory"})

_MB_PER_GB = 1024

_CHANGE_SPEC_SHAPES = (
    "Pass either a resize — {'cpu': 4} and/or {'memory_mb': 32768} "
    "(or {'memory_gb': 32}) — or one guest command — {'command': '/usr/bin/apt-get', "
    "'arguments': '-y upgrade', 'username': '<guest account>', 'password': '...'}."
)


def require_guest_username(username: Any, where: str) -> str:
    """Return ``username`` or raise a teaching error; there is no default account."""
    if not isinstance(username, str) or not username.strip():
        raise ValueError(
            f"{where}: a guest username is required. vmware-aiops runs guest "
            "commands as the account you name and has no default, so pilot will "
            "not assume root either. Pass the guest OS account "
            "(e.g. username='ops') and its password, then call create_workflow_from_template again."
        )
    return username


def plan_change(change_spec: Any) -> tuple[str, dict[str, Any]]:
    """Return ``(aiops_tool, params_without_vm_name_or_target)`` for a change_spec.

    Raises ``ValueError`` with the remedy for any spec the chosen tool would
    reject. Never mutates ``change_spec``.
    """
    if not isinstance(change_spec, dict) or not change_spec:
        raise ValueError(
            f"clone_and_test: change_spec must be a non-empty dict (got "
            f"{change_spec!r:.40}), or there is nothing to apply to the staging VM. "
            f"{_CHANGE_SPEC_SHAPES} Then call create_workflow_from_template again."
        )
    if set(change_spec) & RECONFIGURE_KEYS:
        return "reconfigure_vm", _reconfigure_params(change_spec)
    return "run_vm_guest_command", _guest_exec_params(change_spec)


def _reconfigure_params(change_spec: dict[str, Any]) -> dict[str, Any]:
    foreign = sorted(set(change_spec) - RECONFIGURE_KEYS)
    if foreign:
        raise ValueError(
            f"clone_and_test: change_spec mixes a resize with {foreign}, which "
            "vmware-aiops reconfigure_vm does not accept (it takes cpu and memory_mb "
            "only). Plan the resize and the guest command as two separate "
            "create_workflow_from_template('clone_and_test', ...) calls."
        )
    if "memory_gb" in change_spec and "memory_mb" in change_spec:
        raise ValueError(
            "clone_and_test: change_spec sets both memory_gb and memory_mb. Keep one "
            "— reconfigure_vm takes memory_mb, and memory_gb is converted to it — "
            "and call create_workflow_from_template again."
        )
    params = {k: v for k, v in change_spec.items() if k != "memory_gb"}
    if "memory_gb" in change_spec:
        params["memory_mb"] = _gb_to_mb(change_spec["memory_gb"])
    return params


def _gb_to_mb(memory_gb: Any) -> int:
    if isinstance(memory_gb, bool) or not isinstance(memory_gb, (int, float)):
        raise ValueError(
            f"clone_and_test: memory_gb must be a number, got {memory_gb!r}. "
            "Pass e.g. {'memory_gb': 32}, or memory_mb as a whole number, to "
            "create_workflow_from_template."
        )
    memory_mb = memory_gb * _MB_PER_GB
    if memory_mb <= 0 or memory_mb != int(memory_mb):
        raise ValueError(
            f"clone_and_test: memory_gb={memory_gb!r} is {memory_mb!r} MB, and "
            "reconfigure_vm takes memory_mb as a positive whole number. Pass "
            "memory_mb directly (e.g. {'memory_mb': 4096}) to create_workflow_from_template."
        )
    return int(memory_mb)


def _guest_exec_params(change_spec: dict[str, Any]) -> dict[str, Any]:
    foreign = sorted(set(change_spec) - GUEST_EXEC_KEYS)
    if foreign:
        raise ValueError(
            f"clone_and_test: change_spec has {foreign}, which is neither a resize "
            f"key nor a vmware-aiops run_vm_guest_command argument "
            f"({', '.join(sorted(GUEST_EXEC_KEYS))}). {_CHANGE_SPEC_SHAPES} Then call "
            "create_workflow_from_template again."
        )
    command = change_spec.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(
            "clone_and_test: a guest change_spec needs 'command' — the full path of "
            "the program to run in the guest (e.g. '/usr/bin/apt-get'); put its "
            f"arguments in 'arguments', and call create_workflow_from_template again. "
            f"{_CHANGE_SPEC_SHAPES}"
        )
    require_guest_username(change_spec.get("username"), "clone_and_test change_spec")
    return dict(change_spec)
