"""The design catalog covers every companion skill, and the docs quote real counts.

``get_skill_catalog`` listed eight skills after the family had grown to
thirteen with an MCP server: debug, harden, log-insight, privateai and vdi were
missing, so an agent designing a workflow was never shown them. The snapshot the
tests read had the same eight, so every test passed by not knowing the other
five existed.

The counts quoted in the docs ("69 curated tools across 8 skills", "aiops alone
has 49 tools") were prose with nothing behind them, and had drifted: aiops
registers 60. They are now checked against the snapshot and the catalog.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from vmware_pilot.mcp_server._catalog import SKILL_CATALOG

PILOT = pathlib.Path(__file__).parents[3]
FIXTURE = PILOT / "tests" / "fixtures" / "companion_tools.json"
DOCS = PILOT / "skills" / "vmware-pilot"


def _registry() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data and all(data.values()), f"{FIXTURE.name} is empty — the check would verify nothing"
    return data


def _catalog_total() -> int:
    return sum(len(entry["tools"]) for entry in SKILL_CATALOG.values())


@pytest.mark.unit
def test_every_snapshotted_companion_has_a_catalog_entry():
    missing = sorted(set(_registry()) - set(SKILL_CATALOG))
    assert not missing, f"companion skills absent from SKILL_CATALOG: {missing}"


@pytest.mark.unit
def test_the_catalog_names_no_skill_the_snapshot_lacks():
    extra = sorted(set(SKILL_CATALOG) - set(_registry()))
    assert not extra, f"SKILL_CATALOG names skills with no snapshot: {extra}"


@pytest.mark.unit
def test_capabilities_table_matches_the_snapshot_and_catalog():
    text = (DOCS / "references" / "capabilities.md").read_text(encoding="utf-8")
    registry = _registry()
    matches = [
        (m.group(1), (int(m.group(2)), int(m.group(3))))
        for m in re.finditer(r"^\|\s*vmware-([a-z-]+)\s*\|\s*`[^`]+`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", text, re.M)
    ]
    assert matches, "no skill rows parsed from capabilities.md — the check would verify nothing"
    names = [name for name, _counts in matches]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    # A dict would keep only the last row, so a stale duplicate would pass.
    assert not duplicates, f"capabilities.md lists these skills more than once: {duplicates}"
    rows = dict(matches)
    assert set(rows) == set(registry), f"table skills {sorted(rows)} != snapshot {sorted(registry)}"
    wrong = {
        skill: {"doc": rows[skill], "real": (len(registry[skill]), len(SKILL_CATALOG[skill]["tools"]))}
        for skill in rows
        if rows[skill] != (len(registry[skill]), len(SKILL_CATALOG[skill]["tools"]))
    }
    assert not wrong, f"capabilities.md counts drifted: {wrong}"

    heading = re.search(r"^## Orchestrated Skills \((\d+)\)", text, re.M)
    totals = re.search(r"\*\*Totals\*\*: (\d+) tools across the (\d+) companion skills; "
                       r"the design catalog covers (\d+) of them", text)
    assert heading and totals, "heading or Totals line not found"
    assert int(heading.group(1)) == len(registry)
    assert (int(totals.group(1)), int(totals.group(2)), int(totals.group(3))) == (
        sum(len(t) for t in registry.values()), len(registry), _catalog_total())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("doc", "pattern"),
    [
        ("SKILL.md", r"\((\d+) curated tools across (\d+) skills\)"),
        ("SKILL.md", r"surfaces (\d+) hand-picked building blocks across (\d+) skills"),
        ("references/integration-patterns.md", r"offers (\d+) curated building blocks across (\d+)\s+skills"),
    ],
)
def test_prose_counts_match_the_catalog(doc, pattern):
    text = (DOCS / doc).read_text(encoding="utf-8")
    found = re.findall(pattern, text)
    assert found, f"{doc}: count sentence not found — pattern {pattern!r}"
    # Every occurrence, not the first: a second, stale copy of the sentence
    # would otherwise pass.
    wrong = [(int(a), int(b)) for a, b in found if (int(a), int(b)) != (_catalog_total(), len(SKILL_CATALOG))]
    assert not wrong, f"{doc}: stale counts {wrong}, catalog is {(_catalog_total(), len(SKILL_CATALOG))}"


@pytest.mark.unit
def test_the_aiops_example_quotes_real_numbers():
    text = (DOCS / "SKILL.md").read_text(encoding="utf-8")
    found = re.search(r"aiops alone has (\d+) tools; the catalog lists (\d+)", text)
    assert found, "aiops example sentence not found"
    assert (int(found.group(1)), int(found.group(2))) == (
        len(_registry()["aiops"]), len(SKILL_CATALOG["aiops"]["tools"]))
