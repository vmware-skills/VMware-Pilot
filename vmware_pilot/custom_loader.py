"""Custom workflow loader — load user-defined workflows from YAML files.

Users create YAML files in ``~/.vmware/workflows/`` and pilot auto-loads them
alongside the built-in templates. Supports ``{{variable}}`` placeholders
that are filled from params at runtime.

Example YAML (``~/.vmware/workflows/restart_db_cluster.yaml``)::

    name: restart_db_cluster
    description: Rolling restart of database cluster
    steps:
      - action: check_health
        skill: monitor
        tool: get_alarms
        params:
          target: "{{target}}"

      - action: stop_replica
        skill: aiops
        tool: vm_power_off
        params:
          vm_name: "{{replica_vm}}"
        rollback_tool: vm_power_on
        rollback_params:
          vm_name: "{{replica_vm}}"

      - action: require_approval
        skill: pilot
        tool: approve
        params:
          message: "Replica stopped. Proceed with primary {{primary_vm}}?"

      - action: restart_primary
        skill: aiops
        tool: vm_power_off
        params:
          vm_name: "{{primary_vm}}"
          force: false
        rollback_tool: vm_power_on
        rollback_params:
          vm_name: "{{primary_vm}}"

Usage:
    plan_workflow("restart_db_cluster", {
        "target": "vcenter1",
        "replica_vm": "db02",
        "primary_vm": "db01"
    })
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, new_workflow_id

_log = logging.getLogger("vmware-pilot.custom")

_WORKFLOWS_DIR = Path("~/.vmware/workflows").expanduser()
_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def load_custom_templates() -> dict[str, Any]:
    """Scan ~/.vmware/workflows/ for YAML workflow definitions.

    Returns a dict mapping workflow name → loader function (same interface as built-in templates).
    """
    templates: dict[str, Any] = {}

    if not _WORKFLOWS_DIR.exists():
        return templates

    try:
        import yaml
    except ImportError:
        _log.warning("pyyaml not installed, custom workflows disabled")
        return templates

    for path in sorted(_WORKFLOWS_DIR.glob("*.yaml")):
        try:
            with open(path, encoding="utf-8") as fh:
                spec = yaml.safe_load(fh)
            if not spec or "name" not in spec or "steps" not in spec:
                _log.warning("Skipping invalid workflow: %s (missing name or steps)", path.name)
                continue

            name = spec["name"]
            templates[name] = _make_loader(spec, path)
            _log.debug("Loaded custom workflow: %s from %s", name, path.name)
        except Exception:
            _log.warning("Failed to load workflow from %s", path.name, exc_info=True)

    return templates


def _make_loader(spec: dict[str, Any], source: Path) -> Any:
    """Create a template loader function from a YAML spec."""
    description = spec.get("description", "")
    raw_steps = spec["steps"]

    def loader(**params: Any) -> Workflow:
        now = datetime.now(tz=timezone.utc).isoformat()
        steps = []

        for i, raw in enumerate(raw_steps):
            step_params = _substitute(raw.get("params", {}), params)
            rollback_params = _substitute(raw.get("rollback_params", {}), params)

            steps.append(
                WorkflowStep(
                    index=i,
                    action=raw.get("action", f"step_{i}"),
                    skill=raw.get("skill", "unknown"),
                    tool=raw.get("tool", "unknown"),
                    params=step_params,
                    rollback_tool=raw.get("rollback_tool", ""),
                    rollback_params=rollback_params,
                )
            )

        return Workflow(
            id=new_workflow_id(),
            workflow_type=spec["name"],
            state=WorkflowState.PENDING,
            steps=steps,
            params={
                **params,
                "_source": source.name,
                "_description": description,
            },
            created_at=now,
            updated_at=now,
        )

    loader.__doc__ = description or f"Custom workflow from {source.name}"
    loader.__name__ = spec["name"]
    return loader


def _substitute(obj: Any, params: dict[str, Any]) -> Any:
    """Recursively substitute {{var}} placeholders in dicts/lists/strings."""
    if isinstance(obj, str):

        def _replace(m: re.Match) -> str:
            key = m.group(1)
            val = params.get(key, m.group(0))  # keep placeholder if not found
            return str(val)

        return _VAR_PATTERN.sub(_replace, obj)

    if isinstance(obj, dict):
        return {k: _substitute(v, params) for k, v in obj.items()}

    if isinstance(obj, list):
        return [_substitute(item, params) for item in obj]

    return obj


def list_custom_workflows() -> list[dict[str, str]]:
    """List available custom workflow files (for discovery)."""
    if not _WORKFLOWS_DIR.exists():
        return []

    result = []
    try:
        import yaml
    except ImportError:
        return []

    for path in sorted(_WORKFLOWS_DIR.glob("*.yaml")):
        try:
            with open(path, encoding="utf-8") as fh:
                spec = yaml.safe_load(fh)
            if spec and "name" in spec:
                result.append(
                    {
                        "name": spec["name"],
                        "description": spec.get("description", ""),
                        "file": path.name,
                        "steps": len(spec.get("steps", [])),
                    }
                )
        except Exception:
            pass

    return result
