"""Importing this skill must not disable another skill's environment rules.

`vmware_policy`'s environment resolver is a single process-global slot. Every
skill that registers one at *import* time therefore overwrites whatever the
previously-imported skill installed — and an MCP host that loads several of
these skills into one process is the normal deployment, not an exotic one.

vmware-pilot used to register a resolver that answered ``"local"`` for every
target, unconditionally, at module import. Loading it after (say) vmware-monitor
made ``prod-vc01`` resolve to ``"local"`` instead of ``"production"``, and every
``deny`` rule scoped to ``environments: [production]`` stopped matching. The
real-hardware round measured a rule going DENY → ALLOW that way. Nothing warned:
under-matching a deny rule is silent by construction.

A single-module test cannot see this. The reproduction below needs two skills in
one process — a stand-in for the sibling, then a genuine fresh execution of this
skill's server module — which is why the import here is forced rather than
implicit, and why the fresh execution is asserted rather than assumed.
"""

from __future__ import annotations

import importlib
import sys
import textwrap

import pytest
from vmware_policy.environment import resolve_environment, set_environment_resolver
from vmware_policy.policy import PolicyEngine

SERVER_MODULE = "vmware_pilot.mcp_server.server"

#: A deny rule of the shape this defect neutralised: scoped to an environment,
#: so it fires only when the target resolves to the label it names.
_RULES = textwrap.dedent(
    """
    deny:
      - name: no_destructive_writes_in_production
        operations: ["vm_delete", "delete_*"]
        environments: ["production"]
        reason: "Destructive work in production needs a change record."
    """
)


def _sibling_resolver(target: str) -> str | None:
    """Stand-in for a sibling skill that reads environments from its own config."""
    return {"prod-vc01": "production", "lab-vc02": "lab"}.get(target or "")


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(_RULES, encoding="utf-8")
    return PolicyEngine(path)


@pytest.fixture
def clean_resolver():
    """Leave the process-global slot exactly as it was found."""
    yield
    set_environment_resolver(None)


@pytest.fixture
def restore_server_module():
    """Undo the forced re-import so later tests keep the module object they patch.

    Both bindings have to go back, not just ``sys.modules``. ``importlib`` also
    rebinds the attribute on the parent package, and code that reaches the module
    as ``from vmware_x.mcp_server import server`` reads *that* — so restoring only
    ``sys.modules`` leaves the re-executed copy installed where it matters and
    silently breaks every later test that patches ``server._store``.
    """
    parent_name, _, attr = SERVER_MODULE.rpartition(".")
    parent = importlib.import_module(parent_name)
    original = sys.modules.get(SERVER_MODULE)
    yield
    if original is not None:
        sys.modules[SERVER_MODULE] = original
        setattr(parent, attr, original)
    else:
        sys.modules.pop(SERVER_MODULE, None)


def _import_freshly(name: str):
    """Import ``name`` in a way that is guaranteed to execute its module body.

    A plain ``import`` of an already-imported module is a no-op, and a test that
    silently does nothing is the failure mode this whole file exists to catch.
    The module is dropped from ``sys.modules`` first and the resulting object is
    returned so the caller can assert it is genuinely a new one.
    """
    previous = sys.modules.pop(name, None)
    assert name not in sys.modules, f"{name} still cached — the import would be a no-op"
    fresh = importlib.import_module(name)
    assert fresh is not previous, f"{name} was not re-executed"
    return fresh


def _denied(engine: PolicyEngine, target: str) -> bool:
    """Would the production deny rule refuse ``vm_delete`` against ``target``?"""
    return not engine.check_allowed(
        "vm_delete", env=resolve_environment(target), risk_level="high"
    ).allowed


@pytest.mark.unit
class TestCrossSkillResolverIsolation:
    def test_positive_control_rule_denies_before_anything_is_loaded(
        self, engine, clean_resolver
    ):
        """The measurement itself works: with the sibling's resolver, DENY."""
        set_environment_resolver(_sibling_resolver)
        assert resolve_environment("prod-vc01") == "production"
        assert _denied(engine, "prod-vc01") is True
        # ...and the rule is scoped, not a blanket deny.
        assert _denied(engine, "lab-vc02") is False

    def test_negative_control_a_hijacked_resolver_really_does_flip_deny_to_allow(
        self, engine, clean_resolver
    ):
        """The assertion below is live: a clobbering resolver *would* be caught."""
        set_environment_resolver(_sibling_resolver)
        assert _denied(engine, "prod-vc01") is True
        set_environment_resolver(lambda target: "local")  # what the defect did
        assert _denied(engine, "prod-vc01") is False

    def test_importing_the_pilot_server_leaves_the_sibling_resolver_governing(
        self, engine, clean_resolver, restore_server_module
    ):
        """The load-bearing case: two skills, one process, first one still rules."""
        set_environment_resolver(_sibling_resolver)
        assert _denied(engine, "prod-vc01") is True

        _import_freshly(SERVER_MODULE)

        assert resolve_environment("prod-vc01") == "production", (
            "importing vmware-pilot overwrote the sibling skill's environment "
            "resolver — its targets now resolve to pilot's answer"
        )
        assert _denied(engine, "prod-vc01") is True, (
            "importing vmware-pilot turned an environment-scoped deny rule into "
            "an allow for another skill's production target"
        )

    def test_starting_the_server_also_leaves_the_sibling_resolver_governing(
        self, engine, clean_resolver, restore_server_module, monkeypatch
    ):
        """Not just import: actually starting to serve must be safe too.

        Moving a bad registration from import time into ``main()`` would keep
        this defect alive for every host that runs the server, which is every
        host that serves it. The fix has to remove the registration, not
        relocate it. ``mcp.run`` is stubbed because the real one blocks on
        stdio; everything ``main`` does before that still executes.
        """
        module = _import_freshly(SERVER_MODULE)
        monkeypatch.setattr(module.mcp, "run", lambda **kwargs: None)
        set_environment_resolver(_sibling_resolver)

        module.main()

        assert resolve_environment("prod-vc01") == "production"
        assert _denied(engine, "prod-vc01") is True

    def test_importing_the_pilot_server_registers_no_resolver_of_its_own(
        self, clean_resolver, restore_server_module
    ):
        """Nothing is registered at import — not even when the slot is empty.

        Pilot has no target config and no connection, so it has no basis to
        answer "what environment is target X in?" for any X. Registering an
        answer anyway is what made the hijack harmful; the honest answer is the
        shared default, which is "unlabeled" and matches no env-scoped rule.
        """
        set_environment_resolver(None)
        _import_freshly(SERVER_MODULE)
        assert resolve_environment("prod-vc01") == ""
