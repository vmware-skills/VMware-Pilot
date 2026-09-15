"""The approval gate must cover the write surface, and keep covering it as it grows.

``review()`` decided "is this step destructive?" by testing the tool name against
seven substrings — delete / remove / destroy / force_power_off / shutdown / drop /
rollback. A hand-maintained list like that has to keep pace with fourteen sibling
skills, and it never will: measured against the family's real annotations on
2026-08-30, 120 tools declare ``readOnlyHint: False`` and only 24 of them matched
a substring. 96 write tools produced no finding at all — including 17 that their
own skill declares ``destructiveHint: True``. Meanwhile ``SKILL_CATALOG``, sitting
in this same package, carried a ``risk`` label for 69 tools that nothing read.

Two things follow, and they are tested separately below.

*Read the data pilot already has.* ``risk: high`` in the catalog is a statement
about a tool that the review was throwing away.

*Stop treating "unrecognised" as "safe".* The old rule was an allowlist by
omission: a tool was waved through unless its name matched. The rule is now
fail-closed — a step pilot cannot classify needs an approval gate ahead of it,
and says so in those words rather than pretending the tool is known to be
destructive. That property is what keeps this fixed as the surface grows, and it
is why ``test_a_tool_nobody_has_ever_heard_of_is_gated`` is the load-bearing test
here rather than the 120-name control that follows it.
"""

from __future__ import annotations

import pytest

from tests.eval.regression._family_write_surface import (
    iter_self_declared_destructive,
    iter_writes,
)
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep
from vmware_pilot.review import review

#: Findings that mean "a human has to look at this before it runs". Kept in step
#: with ``run_workflow``'s own blocking set, which is the thing that enforces it.
BLOCKING = {"ungated_destructive", "ungated_unclassified", "destructive_in_parallel_group"}


def _wf(steps: list[WorkflowStep]) -> Workflow:
    return Workflow(
        id="wf-gate", workflow_type="test", state=WorkflowState.PENDING,
        steps=steps, params={}, created_at="", updated_at="",
    )


def _ungated(skill: str, tool: str) -> Workflow:
    """One step, no approval gate in front of it — the shape under test."""
    return _wf([WorkflowStep(index=0, action=tool, skill=skill, tool=tool, params={"id": "x"})])


def _gated(skill: str, tool: str) -> Workflow:
    """The same step with a require_approval ahead of it — the control."""
    return _wf([
        WorkflowStep(0, "require_approval", "pilot", "approve", {"message": "ok?"}),
        WorkflowStep(1, tool, skill, tool, {"id": "x"}),
    ])


def _blocking_kinds(wf: Workflow) -> set[str]:
    return {f["kind"] for f in review(wf)["findings"] if f["kind"] in BLOCKING}


@pytest.mark.unit
class TestFailClosed:
    """The property that survives the surface growing, tested without a list."""

    def test_a_tool_nobody_has_ever_heard_of_is_gated(self):
        """A name in no catalog and matching no pattern must not read as safe.

        This is the whole fix in one assertion. Every tool added to any sibling
        skill after today arrives here first, and arrives gated — no list has to
        be edited for that to happen. It is deliberately a name that cannot be
        satisfied by an earlier branch: not in SKILL_CATALOG, no destructive
        substring, no read-only prefix.
        """
        assert _blocking_kinds(_ungated("nsx", "quiesce_appliance")) == {"ungated_unclassified"}

    def test_the_unknown_finding_says_what_it_actually_knows(self):
        """It must not claim the tool is destructive — only that it cannot tell.

        A gate that overstates its evidence gets ignored, and this one is
        reporting an absence of information, which is a different fact from
        danger. The message has to carry the remedy too, since 'add it to the
        catalog' is the fix that makes the noise go away permanently.
        """
        finding = next(
            f for f in review(_ungated("nsx", "quiesce_appliance"))["findings"]
            if f["kind"] == "ungated_unclassified"
        )
        message = finding["message"]
        assert "quiesce_appliance" in message
        assert "destructive" not in message.lower().split("cannot")[0]
        assert "SKILL_CATALOG" in message
        assert "require_approval" in message

    def test_declared_blind_spot_a_read_hint_can_match_by_accident(self):
        """Pinned, not fixed: substring matching still has one silent failure.

        ``_READONLY_HINTS`` matches anywhere in the name, so any tool whose name
        happens to contain "get" — ``..._tar-get``, ``..._wid-get`` — is read as
        an inspection when the catalog has nothing to say about it. Four tools in
        the measured family surface match a read hint only by accident, and three
        of them are writes; all four are covered today because the catalog places
        them first. This asserts the hole is still exactly where we think it is,
        so it cannot widen without a test going red.

        It is not closed here because closing it means either a whole-word rule —
        which drops ``vs_status`` and ``ssl_expiry_check`` into ``unknown`` for no
        gain — or another hand-kept exception list, which is the thing this change
        removed.
        """
        from vmware_pilot.review import TIER_READ, classify_step

        tier, _ = classify_step("nowhere", "quiesce_widget")
        assert tier == TIER_READ

    def test_an_unclassified_step_makes_the_verdict_needs_revision(self):
        """Severity, not just kind. Two consumers read this finding differently.

        ``run_workflow`` blocks on the finding's ``kind``; the ``verdict`` a
        human or agent reads is computed from its ``severity``. Downgrading the
        severity would leave the run blocked while the review said "approved" —
        the two halves disagreeing is exactly the state that gets a gate
        overridden on sight.
        """
        result = review(_ungated("nsx", "quiesce_appliance"))
        assert result["verdict"] == "needs_revision"

    def test_an_unrecognised_tool_behind_a_gate_is_accepted(self):
        """Fail-closed, not fail-always: the gate is what it is asking for."""
        assert _blocking_kinds(_gated("nsx", "quiesce_appliance")) == set()

    def test_review_reports_how_much_of_the_workflow_it_could_classify(self):
        """An 'approved' verdict must not be able to mean 'I did not look'.

        The summary carries the coverage so a caller can tell a reviewed
        workflow from an unreviewable one, which the old summary could not.
        """
        wf = _wf([
            WorkflowStep(0, "list_vms", "monitor", "list_virtual_machines", {}),
            WorkflowStep(1, "require_approval", "pilot", "approve", {"message": "ok?"}),
            WorkflowStep(2, "frob", "nsx", "quiesce_appliance", {}),
        ])
        summary = review(wf)["summary"]
        assert summary["unclassified_steps"] == 1
        assert summary["classified_steps"] == 1  # the approval step is neither


@pytest.mark.unit
class TestCatalogRiskIsRead:
    """``SKILL_CATALOG``'s risk labels stop being decorative."""

    def test_catalog_high_risk_is_gated_even_with_an_innocuous_name(self):
        """``vm_clean_slate`` reverts a VM to a baseline. No substring says so."""
        assert "ungated_destructive" in _blocking_kinds(_ungated("aiops", "vm_clean_slate"))

    def test_catalog_low_risk_is_not_gated(self):
        """The read tier still passes clean — the gate did not become a blanket."""
        assert _blocking_kinds(_ungated("monitor", "vm_info")) == set()

    def test_every_high_risk_catalog_entry_is_gated(self):
        """Every one of them, not the handful whose names contain 'delete'.

        Thirteen carried the label before this change and none of them was read;
        more were added when the catalog was reconciled against the siblings'
        own annotations (``aiops.vm_power_off``, the four guest tools, and both
        VKS kubeconfig tools).
        """
        from vmware_pilot.mcp_server._catalog import SKILL_CATALOG

        missed = [
            (skill, tool)
            for skill, entry in SKILL_CATALOG.items()
            for tool, meta in entry["tools"].items()
            if meta["risk"] == "high" and not _blocking_kinds(_ungated(skill, tool))
        ]
        assert missed == []


@pytest.mark.unit
class TestLabelsAgreeWithWhatTheSiblingsPublish:
    """Catalog entries whose label had drifted from the owning skill's own claim.

    Re-read from the sibling sources on 2026-09-11, not from the 2026-08-30
    snapshot in ``_family_write_surface`` (which predates both changes):

    * ``VMware-AIops/vmware_aiops/mcp_server/tools/guest.py`` publishes
      ``vm_guest_exec``, ``vm_guest_exec_output``, ``vm_guest_upload`` and
      ``vm_guest_provision`` with ``destructiveHint: True``. Pilot labelled all
      four ``medium``, so a custom workflow whose only step was
      ``vm_guest_exec /bin/rm -rf /`` was saved and dispatched with no gate.
    * ``VMware-VKS/vmware_vks/mcp_server/server.py`` publishes
      ``get_supervisor_kubeconfig`` with ``readOnlyHint: False`` — it returns a
      live Supervisor bearer token. It was absent from the catalog, so the
      ``get`` read-hint placed it in the read tier: confidently wrong.
    """

    @pytest.mark.parametrize(
        "tool", ["vm_guest_exec", "vm_guest_exec_output", "vm_guest_upload", "vm_guest_provision"]
    )
    def test_guest_tools_are_gated(self, tool):
        assert "ungated_destructive" in _blocking_kinds(_ungated("aiops", tool))

    def test_guest_download_stays_ungated(self):
        """Control: AIops keeps ``vm_guest_download`` at destructiveHint False."""
        from vmware_pilot.mcp_server._catalog import SKILL_CATALOG

        assert SKILL_CATALOG["aiops"]["tools"].get("vm_guest_download", {}).get("risk") != "high"

    @pytest.mark.parametrize("tool", ["get_supervisor_kubeconfig", "get_tkc_kubeconfig"])
    def test_both_kubeconfig_tools_are_gated_as_credential_access(self, tool):
        from vmware_pilot.review import classify_step

        tier, why = classify_step("vks", tool)
        assert tier == "destructive", why
        assert "catalog" in why, "decided by the catalog, not by a name substring"
        assert "ungated_destructive" in _blocking_kinds(_ungated("vks", tool))


@pytest.mark.unit
class TestCatalogLookup:
    """How a tool is found in the catalog, including when the answer is unclear."""

    def test_a_tool_named_by_a_skill_the_catalog_does_not_carry_still_resolves(self):
        """A step may name a skill the catalog does not carry.

        A skill added after the catalog, or a misspelt skill name, would otherwise
        fall to ``unknown``
        even for a tool the catalog knows perfectly well under another skill.
        """
        from vmware_pilot.review import TIER_DESTRUCTIVE, classify_step

        tier, why = classify_step("some-skill-not-in-the-catalog", "vm_clean_slate")
        assert tier == TIER_DESTRUCTIVE
        assert "catalog" in why

    def test_an_ambiguous_bare_name_resolves_to_the_highest_risk(self, monkeypatch):
        """Guessing downward is the only guess that can hide a destructive step.

        No tool name is currently duplicated across two catalog skills, so this
        path has no natural fixture — hence the constructed one. Without it the
        tie-break is untested code that could silently invert.
        """
        from vmware_pilot import review as review_mod

        monkeypatch.setattr(
            review_mod,
            "SKILL_CATALOG",
            {
                "a": {"description": "", "tools": {"shared_name": {"risk": "low", "desc": ""}}},
                "b": {"description": "", "tools": {"shared_name": {"risk": "high", "desc": ""}}},
            },
        )
        tier, why = review_mod.classify_step("neither", "shared_name")
        assert tier == review_mod.TIER_DESTRUCTIVE
        assert "high" in why


@pytest.mark.unit
class TestMeasuredFamilySurface:
    """Control against the 120 write tools actually published by the siblings.

    Not the gate — the gate is fail-closed and needs no inventory. This is how we
    get to say a number with names behind it instead of an adjective.
    """

    def test_every_self_declared_destructive_tool_is_gated(self):
        """41 of 41. Seventeen of these produced no finding whatsoever before."""
        missed = [
            f"{skill}.{tool}"
            for skill, tool in iter_self_declared_destructive()
            if not _blocking_kinds(_ungated(skill, tool))
        ]
        assert missed == [], (
            f"{len(missed)} tools that their own skill marks destructiveHint=True "
            f"pass pilot's approval gate unremarked: {missed}"
        )

    def test_no_write_tool_is_positively_classified_as_read_only(self):
        """The worse failure: not 'unknown', but confidently wrong.

        ``get_tkc_kubeconfig`` hands back a live Supervisor credential, yet it
        opens with ``get_`` — the read-only name heuristic claimed it. A tool
        pilot mislabels safe is invisible; a tool pilot cannot label is at least
        loud. (``get_supervisor_kubeconfig`` is the same shape; it became a
        write after this snapshot was taken and is pinned in
        ``TestLabelsAgreeWithWhatTheSiblingsPublish``.)
        """
        from vmware_pilot.review import classify_step

        mislabelled = [
            f"{skill}.{tool}"
            for skill, tool in iter_writes()
            if classify_step(skill, tool)[0] == "read"
        ]
        assert mislabelled == []

    def test_writes_are_either_gated_or_classified_but_never_ignored(self):
        """Every one of the 120 gets an answer, and the answer is recorded here.

        Medium-tier writes (create / scale / enable) are deliberately not gated:
        the family gates destructive work, and requiring approval ahead of every
        state change flips four built-in templates whose whole design is
        create-in-staging → approve → apply. The point of this assertion is that
        the split is a decision with a number attached, not an accident.
        """
        from vmware_pilot.review import classify_step

        tiers = {}
        for skill, tool in iter_writes():
            tiers.setdefault(classify_step(skill, tool)[0], []).append(f"{skill}.{tool}")
        assert sorted(tiers) == ["destructive", "unknown", "write"]
        assert len(tiers["destructive"]) + len(tiers["unknown"]) + len(tiers["write"]) == 120
        # Nothing may drift back into the silent tier.
        assert "read" not in tiers


@pytest.mark.unit
class TestControlsStillHold:
    """The existing behaviour the fix must not have traded away."""

    def test_a_legitimately_gated_destructive_step_stays_accepted(self):
        assert _blocking_kinds(_gated("nsx", "delete_segment")) == set()

    def test_an_ungated_destructive_step_is_still_blocked(self):
        assert "ungated_destructive" in _blocking_kinds(_ungated("nsx", "delete_segment"))

    def test_a_read_only_workflow_is_still_approved(self):
        wf = _wf([
            WorkflowStep(0, "list_vms", "monitor", "list_virtual_machines", {}),
            WorkflowStep(1, "get_alarms", "monitor", "get_alarms", {}),
        ])
        assert review(wf)["verdict"] == "approved"

    def test_every_builtin_template_still_reviews_clean(self):
        """The authored surface must not regress — measured, not assumed.

        None of the built-in templates names a tool pilot cannot classify, so
        fail-closed costs them nothing. If that stops being true, this fails
        before a user meets it.
        """
        import inspect

        from vmware_pilot.templates import BUILTIN_TEMPLATES

        checked, offenders = 0, []
        for name, fn in BUILTIN_TEMPLATES.items():
            kwargs = {}
            for param, spec in inspect.signature(fn).parameters.items():
                if spec.default is inspect.Parameter.empty:
                    kwargs[param] = {
                        "dict": {}, "list": [], "int": 1, "float": 1.0, "bool": True,
                    }.get(getattr(spec.annotation, "__name__", ""), "x")
            try:
                wf = fn(**kwargs)
            except Exception:  # noqa: BLE001 — templates needing richer args are skipped
                continue
            checked += 1
            blocking = _blocking_kinds(wf)
            if blocking:
                offenders.append((name, sorted(blocking)))
        assert checked >= 10, f"only {checked} templates were exercised — the check went hollow"
        assert offenders == []


@pytest.mark.unit
class TestRunWorkflowEnforcesTheSameSet:
    """The finding is only worth as much as the thing that acts on it.

    ``review()`` is advisory; ``run_workflow`` is the enforcement point, and it
    keeps its own tuple of blocking finding kinds. The two lists drifting apart
    is a whole class of defect on its own — a review that reports a blocking
    finding while the runner dispatches the step anyway — so the runner is
    exercised here directly rather than trusted to have been updated.
    """

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        import vmware_pilot.mcp_server.server as server
        from vmware_pilot.executor import WorkflowExecutor
        from vmware_pilot.models import WorkflowStore

        s = WorkflowStore(tmp_path / "wf.db")
        monkeypatch.setattr(server, "_store", s)
        monkeypatch.setattr(server, "_executor", WorkflowExecutor(s))
        return s

    def _persist(self, store, tool):
        wf = _ungated("nsx", tool)
        wf.id = f"wf-{tool}"
        store.save(wf)
        return wf.id

    def test_an_ungated_unclassified_step_is_refused(self, store):
        import vmware_pilot.mcp_server.server as server

        result = server.run_workflow(self._persist(store, "quiesce_appliance"))
        assert "error" in result
        kinds = [f["kind"] for f in result["blocking_findings"]]
        assert kinds == ["ungated_unclassified"]
        assert "require_approval" in result["hint"]

    def test_force_does_not_override_it_on_a_custom_workflow(self, store):
        """``workflow_type="test"`` is custom. This used to assert force ran it.

        A custom workflow's missing gate is fixed by inserting one step, and
        that step is the human consent force would stand in for — so force no
        longer stands in for it.
        """
        import vmware_pilot.mcp_server.server as server

        result = server.run_workflow(self._persist(store, "quiesce_appliance"), force=True)
        assert [f["kind"] for f in result["blocking_findings"]] == ["ungated_unclassified"]

    def test_force_still_overrides_it_on_a_builtin(self, store):
        """Fail-closed with an audited escape hatch, not a dead end — for built-ins."""
        import vmware_pilot.mcp_server.server as server

        wf_id = self._persist(store, "quiesce_appliance")
        wf = store.load(wf_id)
        wf.workflow_type = "clone_and_test"
        store.save(wf)
        result = server.run_workflow(wf_id, force=True)
        assert "blocking_findings" not in result
        assert "error" not in result

    def test_a_classified_read_step_still_runs_without_force(self, store):
        """Control: the runner did not simply start refusing everything."""
        import vmware_pilot.mcp_server.server as server

        result = server.run_workflow(self._persist(store, "list_segments"))
        assert "blocking_findings" not in result
