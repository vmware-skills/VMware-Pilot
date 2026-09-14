"""Snapshot the MCP tools of the skills Pilot's templates dispatch to.

Pilot does not depend on its companion skills, so its tests cannot read their
registries. They read ``companion_tools.json`` instead — every tool each
companion registers, with its parameter names and which are required — and
this script is the only thing that writes it.

It reads the registries from sibling checkouts (``<family-root>/VMware-*``),
the way an agent sees them: ``mcp.list_tools()``, not the source.

    python tests/fixtures/snapshot_companion_tools.py <family-root>          # rewrite
    python tests/fixtures/snapshot_companion_tools.py <family-root> --check  # compare

``--check`` exits 1 when the committed snapshot no longer matches the live
registries (a companion renamed a tool or a parameter), and 2 when a registry
could not be read at all — which is not the same as "nothing changed".
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

FIXTURE = pathlib.Path(__file__).with_name("companion_tools.json")
TIMEOUT = 180

#: Skill name as templates spell it -> (repo directory, Python package).
COMPANIONS = {
    "aiops": ("VMware-AIops", "vmware_aiops"),
    "aria": ("VMware-Aria", "vmware_aria"),
    "avi": ("VMware-AVI", "vmware_avi"),
    "monitor": ("VMware-Monitor", "vmware_monitor"),
    "nsx": ("VMware-NSX", "vmware_nsx"),
    "nsx-security": ("VMware-NSX-Security", "vmware_nsx_security"),
    "storage": ("VMware-Storage", "vmware_storage"),
    "vks": ("VMware-VKS", "vmware_vks"),
}

_PROBE = r"""
import asyncio, importlib, json, sys
m = importlib.import_module(sys.argv[1] + ".mcp_server.server")
s = getattr(m, "mcp", None) or m.build_server()
out = {}
for t in asyncio.run(s.list_tools()):
    schema = t.inputSchema or {}
    out[t.name] = {"properties": sorted(schema.get("properties", {})),
                   "required": sorted(schema.get("required", []))}
print(json.dumps(out))
"""


def read_live(root: pathlib.Path) -> tuple[dict, list[str]]:
    """Every companion's registry, and a message for each one that could not be read."""
    live, broken = {}, []
    for skill, (repo, pkg) in sorted(COMPANIONS.items()):
        path = root / repo
        if not (path / "pyproject.toml").is_file():
            broken.append(f"{skill}: no checkout at {path}")
            continue
        try:
            cmd = ["uv", "run", "--project", str(path), "python", "-c", _PROBE, pkg]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            broken.append(f"{skill}: reading the registry did not finish in {TIMEOUT}s")
            continue
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        if proc.returncode != 0 or not lines:
            broken.append(f"{skill}: could not read the registry ({proc.stderr.strip()[-200:]})")
            continue
        tools = json.loads(lines[-1])
        if not tools:
            broken.append(f"{skill}: the registry is empty")
            continue
        live[skill] = tools
    return live, broken


def diff(committed: dict, live: dict) -> list[str]:
    out = []
    for skill in sorted(set(committed) | set(live)):
        old, new = committed.get(skill, {}), live.get(skill, {})
        for tool in sorted(set(old) - set(new)):
            out.append(f"{skill}: {tool} is in the snapshot but no longer registered")
        for tool in sorted(set(new) - set(old)):
            out.append(f"{skill}: {tool} is registered but missing from the snapshot")
        for tool in sorted(set(old) & set(new)):
            if old[tool] != new[tool]:
                out.append(f"{skill}: {tool} parameters changed {old[tool]} -> {new[tool]}")
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    root, check = pathlib.Path(argv[0]), "--check" in argv[1:]
    live, broken = read_live(root)
    if broken:
        for b in broken:
            print(f"BROKEN {b}")
        return 2
    if check:
        committed = json.loads(FIXTURE.read_text(encoding="utf-8"))
        changes = diff(committed, live)
        for c in changes:
            print(c)
        total = sum(len(t) for t in live.values())
        print(f"checked={total} tools in {len(live)} skills, changed={len(changes)}")
        return 1 if changes else 0
    FIXTURE.write_text(json.dumps(live, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE.name}: {sum(len(t) for t in live.values())} tools in {len(live)} skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
