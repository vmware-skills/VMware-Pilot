## v1.8.12 — the test suite runs on a non-UTF-8 machine, and the guardrail tests with it


**The suite now runs on a cp936 machine.** Round 3 of the VCF 9 field testing ran
on Windows Server 2025 with locale cp936. Across the family four repos' suites --
1687 tests -- never executed at all, dying at collection reading our own UTF-8
sources, and 101 more failed the same way. Most of those were the tests that
verify the destructive-operation guardrails: the guardrails were fine, the tests
that check them could not open a file. On the UTF-8 CI every one of them was
green. A security test that cannot run is not a security test.

Every text read and write here names its encoding now, `tests/` included -- the
previous round fixed only the package, which is why this came back. A gate in
`family_smoke` scans both trees by AST, and the whole family's suites were re-run
under an ASCII locale to confirm: 15 of 15 green, from 1 of 15.

**`--help` no longer dies on a console that cannot encode it.** On any console
whose encoding cannot carry the characters in our own help text, `--help` exited
with a `UnicodeEncodeError` traceback -- unavailable exactly on the machines
where it is most needed. Four repos were affected; the handler is now relaxed in
all fifteen so a glyph degrades instead of killing the command.

**Unknown tool arguments are refused instead of dropped.** The schema declared
`additionalProperties: false` and the runtime accepted them anyway, so a filter
argument whose name a model guessed wrong returned the *unfiltered* result with
nothing to indicate anything had been discarded. Fixed in vmware-policy 1.12.0
and in force here.

Requires vmware-policy 1.12.0.

## v1.8.11 — the approval gate stops being an allowlist by omission

Two defects from the 2026-08-30 real-hardware re-test. 

**Importing this package no longer disables another skill's policy rules.**
`vmware_pilot.mcp_server.server` registered a `vmware_policy` environment
resolver at import time, and that resolver answered `local` for every target it
was asked about. `set_environment_resolver` writes to one process-global slot
shared by every skill in the interpreter, so in an MCP host that loads several of
these skills — the normal deployment — importing vmware-pilot replaced whatever
a sibling had installed and every one of that sibling's targets began resolving
to `local`. A rule scoped to `environments: [production]` then matches nothing
and denies nothing. The re-test measured a rule going DENY to ALLOW that way.

The registration is gone rather than moved: pilot has no target config and no
connection, so it has no basis to answer the question for any target, and
`resolve_environment`'s documented default — unlabeled, matching no
environment-scoped rule, never refused for lack of a label — is the honest
answer. Nothing is lost; the gate on the real infrastructure change applies
downstream, in the target skill's own process.

**`review_workflow` now gates what it cannot classify.** The check for
"destructive step with no approval gate" tested the tool name against seven
substrings and treated every other answer as safe. Measured against the family's
own MCP annotations, 120 tools declare `readOnlyHint: False` and 24 matched a
substring: 96 write tools produced no finding at all, 17 of them tools their own
skill marks `destructiveHint: True` — `vm_clean_slate`, `pool_push_image`,
`machine_reset`, `session_logoff`, `ako_restart`, `vgpu_assign`. Meanwhile
`SKILL_CATALOG`, in this same package, carried a `risk` label for 69 tools that
nothing read, including all 13 marked `risk: high`.

Adding the seventeen names would have fixed today's list and lost again on the
next release, so the shape changed instead. The catalog's risk is now read, and a
step whose tool pilot cannot place is reported as `ungated_unclassified` and
needs an approval gate — stated as "cannot classify", not as "destructive",
because the evidence is an absence rather than a danger. That is what keeps this
fixed as the surface grows: a tool added to any sibling tomorrow is gated with no
list edited. `run_workflow` refuses on it like the other blocking findings, and
`force=True` still overrides, audited.

All 41 self-declared destructive tools are now gated, up from 24. Medium-tier
writes (create / scale / enable) stay ungated deliberately — the family gates
destructive work, and four built-in templates create in staging *before* asking
for approval — but they are classified and counted rather than invisible. The
summary gained `classified_steps` and `unclassified_steps` so an "approved"
verdict can no longer mean "nothing here was legible".

Two catalog labels were reconciled with the siblings' own annotations:
`aiops.vm_power_off` (medium to high) and `vks.get_tkc_kubeconfig`, which returns
a live Supervisor credential, is marked destructive by vmware-vks, and was being
read as an inspection because it starts with `get_`.


## v1.8.10 — the schema an agent reads now carries the descriptions

Parameter descriptions reach the JSON schema for the first time. An MCP client
sees the schema, not the docstring, and this repo's coverage of `description`
and `additionalProperties` was 0% — while nearly every parameter was already
described in an `Args:` block no client ever receives.

Measured on a real VCF 9.1 estate, the gap produced a silent failure with no
error at any stage: a parameter name guessed wrong is discarded and the tool
returns the full unfiltered result; a value guessed wrong (`power_state=
"running"`) returns 0 rows where there were 11.

vmware-policy 1.10.0's `describe_tool_parameters` copies what is already
written, so the docstring is now load-bearing and the two cannot drift apart. It
removes the `Args:` block from the description once copied — both travel in
every `tools/list` response, so leaving it bills the same sentences twice
against the manifest's token budget. `additionalProperties` is closed: an open
schema is room for a model to invent arguments that are then silently
discarded, which is the other half of the same failure.

**The `vmware-policy` floor moves to >=1.10.0.** Older releases have no
`describe_tool_parameters`, and resolving one gives an ImportError at server
start rather than a missing feature.


## v1.8.9 — two wrong numbers: the server's own version, and the advertised tool count

Both defects were invisible to the test suites and both were user-facing.

- **The MCP server reported the SDK's version as its own.** `FastMCP` accepts no
  `version` argument and leaves the lowlevel server's at `None`; with it `None`
  the SDK answers `initialize` with its OWN version. Every skill in the family
  therefore told its client it was mcp 1.29.1 — a number that exists for no
  package here, and one that would change with an SDK bump and no code change of
  ours. Verified end to end rather than by reading: unset the field and a probe
  server reports the installed SDK's version; set it and it reports ours.

Also new: this repo is installable as a Claude Code plugin
(`/plugin install vmware-pilot@vmware-skills`). The skill and its MCP server arrive in
one step; nothing is duplicated, the manifest points at the existing `skills/`
tree. family_smoke gained three gates — the server's reported version, the plugin
manifest's agreement with pyproject, and the advertised tool count against the
live registration.

## v1.8.8 — moved to vmware-skills org + MCP Registry namespace io.github.vmware-skills/vmware-pilot

Repo transferred from github.com/zw008 to github.com/vmware-skills (redirects preserve old links).
MCP Registry server renamed to `io.github.vmware-skills/*`; the old `io.github.zw008/*` entry is deprecated.
All in-repo links updated. No functional code change on this line beyond the org move.

## v1.8.7 (2026-07-21) — the skill-level read-only switch is removed; read/write authorization is the vCenter account's job (RBAC)

### Removed: `VMWARE_READ_ONLY` / `read_only:` — give the agent a read-only service account instead

The skill-level read-only switch is gone. It was enforced only on the MCP tool
registry, and any agent with a shell (every SKILL.md grants `allowed-tools: Bash`)
could reach the same change one CLI command away — so it withheld the *tool*, not
the *capability*. It was never a real boundary.

To run an agent read-only, give it a **read-only vCenter/NSX service account
(RBAC)**. Writes are then refused at the platform, un-bypassably, regardless of
surface or shell — the one place read/write control cannot be stepped around. A
config still carrying `read_only: true` is ignored, with a one-time warning that
names the replacement (no silent behavior change).

### Removed: approval tiers and the declared-environment gate (via vmware-policy)

The graduated-autonomy approval tiers (`confirm`/`dual`/`review`) and the "declare
an environment or be refused" baseline are removed — they only ever fired on the
rarest configuration while carrying the family's most complex machinery. Opt-in
`deny` rules and the maintenance window remain, and apply identically wherever a
tool runs.

### Added: offline / air-gapped install docs

The README now covers installing from source without editable mode (for older
`pip`) and building wheels to carry onto an air-gapped host — the modern PEP 517
layout has no `setup.py` by design, which is expected, not a missing file.

This release also carries the accumulated fixes staged since 1.8.5.

## v1.8.5 (2026-07-20) — the two fixes v1.8.4 announced now actually work

Four adversarial reviews of v1.8.4 found that both of its headline fixes were
incomplete in ways the release notes did not reflect. This release makes them
real. If you are on 1.8.4, this is the one to take.

### Fixed — a failure that was *returned* was still audited as a success

vmware-policy 1.8.4 added `report_tool_failure()` for tools that catch an
exception and return an error payload instead of raising. **No skill called it.**

Every string-returning tool therefore kept doing exactly what 1.8.4 said it had
stopped doing: writing `status=ok` to `~/.vmware/audit.db` for an operation that
failed, recording an undo token for a change that never happened, and telling the
circuit breaker the call succeeded so repeated failures never tripped it.

The surface this covered is not marginal:

| Skill | What was mis-audited |
|---|---|
| vmware-aiops | 25 of 49 tools, including **every undo-bearing write** — a failed `vm_power_on` left an undo token saying "power it back off" |
| vmware-avi | all 28 tools, including `vs_toggle` and `ako_restart` |
| vmware-storage | all 4 write tools |
| vmware-nsx | the 5 delete tools |

vmware-avi is worth calling out: before 1.8.4 its exceptions propagated and the
audit was correct. 1.8.4 caught them and returned a string, so **that release made
its audit trail worse than it had been.**

Skills whose tools already return dict payloads (vmware-monitor, vmware-vks,
vmware-aria, vmware-log-insight, vmware-harden, vmware-debug, vmware-pilot) were
already detected correctly. They gained a test proving it rather than a redundant
call.

### Fixed — narrowing `OSError` did not close the leak it was meant to close

1.8.4 narrowed the `_safe_error` passthrough because bare `OSError` let TLS and
DNS failures reach the agent with hostnames and certificate subjects in them.
That narrowing had no effect on the error it was written for:

```
ssl.SSLCertVerificationError → ssl.SSLError → OSError, ValueError
```

`ValueError` has been on every allowlist since long before 1.8.4, so a
certificate failure kept passing through — the commonest self-signed-certificate
failure in this family, carrying the hostname it was checked against. An
allowlist structurally cannot express "not this one".

Where `ssl.SSLError` can actually surface — the pyVmomi skills — it is now
reduced *ahead* of the allowlist. In the httpx skills TLS arrives wrapped as
`httpx.ConnectError`, and in vmware-avi as `requests.exceptions.SSLError`, so the
guard cannot fire there; in those skills the leak was the raw exception
interpolated into an already-allowlisted `*ApiError`, and that is now authored
text naming the config target and `verify_ssl` instead of the exception.

The missing-password error — this family's most common first-run failure, whose
entire remedy is the environment variable name it carries — keeps its message
through a narrow `ConfigError(OSError)` rather than the base class. Connection
failures are translated at the connection layer into an authored remedy that
names the target and the setting to change, with the raw detail left on
`__cause__` for the server log.

### Also fixed

- **vmware-vks**: the quickstart documented a password variable the code never
  reads — following `README.md` verbatim produced "Password not found". Five
  places, plus six references to a `doctor` command this CLI has never had, two
  descriptions promising fields the tools do not return, and eight teaching
  messages that `RuntimeError` was masking.
- **vmware-nsx**: an error cited `--route-advertisement`; the flag is `--advertise`.
- **vmware-pilot**: `get_workflow_status` told the model to call `approve` — a
  tool the read-only gate withholds — as the required next step; and a hint
  pointed at a filename that could never appear in that message.
- **vmware-aiops**: `vm_task_status` polling a *failed task* returned
  `{"state": "error", "error": ...}` from a successful read, which the new
  detection read as the call itself failing. The field is now `task_error`.
  **This is a breaking change for anything parsing that payload.**
- Several remedies that were still being cut by the 300-character cap the 1.8.4
  notes claimed to have addressed.

### Known and not fixed

`ConnectionError` remains one type from two sources in several skills — a
skill's own authored message and urllib3's `HTTPSConnectionPool(host=..., port=...)`
share it, and an allowlist cannot separate them. vmware-vks is converted; the
rest need their own domain type and are deferred rather than half-done.

## v1.8.4 (2026-07-20) — errors that teach, and tool descriptions a small model can route from

A capability eval was rolled out across the family and asked two open questions:
when a call fails, is the model told enough to fix it, and can it pick the right
tool from the description alone? Both answers were worse than anyone thought, and
in several places the reason was that the measurement was looking somewhere other
than where the model reads.

### Fixed — teaching messages were being discarded on the way to the agent

`_safe_error` reduces unrecognised exceptions to `"<Class>: operation failed."`
so raw API text, credentials in URLs and internal paths cannot reach an agent.
Its allowlist held only the builtin validation errors — so this skill's **own**
domain exceptions, the ones that exist precisely to carry a corrected next step,
had their messages replaced by their class names.

The effect was invisible from the CLI, which prints those messages in full.

The worst case was shared by nine skills: `config.py` raises exactly one
`OSError`, the missing-password error, whose entire remedy is the environment
variable name it names. An agent hitting an unconfigured target received
`OSError: operation failed.` and had nothing to act on. That is the family's most
common first-run failure, and it landed one release after the documented variable
names were corrected — so the message that would have unstuck the operator was
the one being thrown away.

The rule is now the property it always meant: **every exception this skill raises
on purpose passes through**, and only genuinely unplanned ones are reduced.
`RuntimeError` stays reduced — it is the generic catch-all and in several skills
carries raw upstream text.

### Fixed — error messages now carry the correction

Every message that reported a failure without saying how to recover was
rewritten: it names the offending value, gives an imperative remedy, and names
something concrete to act on — a tool that exists, a real CLI command, a config
file, an environment variable. Recovery becomes an instruction-following problem
rather than an inference one, which is what a weak model can still do.

Three classes of defect surfaced while doing it:

- **Remedies that were never delivered.** `_safe_error` truncates with no
  ellipsis, so a message longer than the cap loses its closing sentence
  silently. One message had been shipping at 396 characters against a 300-char
  cap — its remedy had never once reached an agent. Messages now lead with the
  remedy so a long interpolated value truncates the expendable detail instead.
- **Commands that do not exist.** One skill's error hints named a `doctor`
  subcommand it does not have.
- **Tools that do not exist.** A tool description pointed at two sibling-skill
  tools that had been renamed, and another named a tool that had moved to a
  different skill entirely.

### Improved — tool descriptions state when to use them and what to call next

The description is the API for a small model: an unstated routing rule is a
routing rule that does not exist, and a tool with no stated next hop is one the
model stops at. Descriptions now say when to prefer this tool over a sibling,
what shape comes back, the caveat that bites, and which tool to call after.

**Manifest size did not grow.** Descriptions load into every session, so the
routing clauses were paid for by cutting duplicated reference material —
repeated boilerplate, examples that restated the parameter list, and prose
copies of the pagination contract.

### Note

Every tool and CLI command named anywhere in this release was verified against
the live MCP registry and the live command tree, not against documentation.

## v1.8.3 (2026-07-20) — credentials resolve as a pair; documented env vars now exist

### Changed — version alignment

No functional change in this skill. The family release adds an env-var override for the per-target username in the credential-bearing skills; this package has no per-target credentials.

## v1.8.2 (2026-07-20) — the MCP server moves into the package namespace

### Fixed — co-installing two skills broke all but the last one

Every skill shipped its MCP server as a **top-level `mcp_server` package**. Python
has one top-level namespace, so installing any two of them into one environment let
the second overwrite the first — silently, with no error and no warning.

    uv tool install vmware-aiops   ->  49 tools   (correct)
    uv pip  install vmware-aiops   ->  27 tools   (Monitor's read-only server)

vmware-aiops depends on vmware-monitor, so this was not an edge case: **every pip
install hit it**, and the operator got 27 read-only tools where 49 were expected,
with all 35 write tools missing. Docker images, shared MCP hosts and CI runners that
install more than one skill were affected the same way.

The server now lives at `vmware_<skill>/mcp_server/`, a name only this package can
claim. Introduced 2026-02-26; it survived 70 releases because every test ran against
a single package in its own repo, where the local directory shadows site-packages —
the conflict was invisible by construction.

**Migration.** Console scripts are unchanged: `vmware-<skill>` and
`vmware-<skill>-mcp` work exactly as before, as does `"command": "vmware-<skill>",
"args": ["mcp"]` in an MCP client config. Only a direct `python -m mcp_server`
breaks; use `python -m vmware_<skill>.mcp_server`.

### Added — `references/agent-guardrails.md` in every skill

The operating rules for local and small models (Llama 3.3 70B, Qwen, Mistral via
Goose / Ollama / OpenShift AI) existed in two skills. They now ship in all 13, each
with its own tool counts and failure modes, and are linked from every SKILL.md.

## v1.8.1 (2026-07-19) — read-only mode reaches the surfaces that teach it

v1.8.0 put read-only mode in the code and documented it in the README only.
Every other layer was empty, and each serves a different reader: SKILL.md is what
the agent loads, setup-guide is what an operator reads while configuring, `doctor`
is where they verify it took. The gap had two concrete costs.

An agent read SKILL.md, called a write tool the gate had withheld, and got nothing
back — with no way to learn that the absence was a deliberate lockdown rather than
a fault. It reads as a broken tool, so the model retries or hunts for a workaround.

An operator who set the switch had no way to confirm it. The only signal was a line
in the MCP server's start-up log.

### Added — the feature is now documented where each reader looks

SKILL.md, setup-guide and capabilities now cover read-only mode. Pilot's case is
the counter-intuitive one and is called out explicitly: **orchestration is its write
surface.** Read-only withholds 9 of 13 tools, leaving only the 4 query tools — a
read-only pilot cannot author, plan, run, approve, roll back or cancel anything.
Env vars are the only switch here; this package has no config file.

## v1.8.0 (2026-07-18) — read-only mode, working policy defaults, declared environments

Family release driven by [VMware-AIops#31](https://github.com/vmware-skills/VMware-AIops/issues/31),
where an operator running Llama 3.3 70B (Goose / OpenShift AI, on-prem H100) had to
hand-write 17 prompt guardrails to make tool calling reliable. A prompt is advisory — a
model can ignore it. Every guardrail that could move into the harness has.

### Added
- **Read-only mode.** Set `VMWARE_PILOT_READ_ONLY=true` (or the family-wide
  `VMWARE_READ_ONLY=true`) and every write tool is removed from the MCP registry
  at start-up. `list_tools()` never offers them, so the model cannot call what it cannot
  see. **Off by default** — nothing changes unless you turn it on. Fail-closed: if the
  mode is requested but cannot be guaranteed, the server refuses to start rather than
  running open. vmware-pilot reads no config file, so these environment variables are the
  only switch — there is no `read_only:` config key here.
- **Declared environment for policy scoping.** Policy rules scope by environment. Pilot
  owns no targets and no connection, so it registers a constant `local` rather than
  reading `environment:` from a config target the way the connected skills do. Its writes
  land in the local workflow DB; the approval gate on the real change applies downstream,
  in the target skill's process, against that skill's declared environment.

### Changed — migration, read this
- **Approval tiers now actually run.** They shipped in v1.6.0 but the engine only ever
  read `~/.vmware/rules.yaml`, and a fresh install has no such file — so every deny rule,
  maintenance window and approval tier had been inert on every install that never
  hand-authored one. A packaged baseline now loads when you have written no rules of your
  own. Writes at medium risk and above are stamped with their tier in the audit log;
  irreversible work and guest execution against a target declared `production` require a
  named approver via `VMWARE_AUDIT_APPROVED_BY`.
- **`environment:` will become required for writes — no action needed in pilot.** A
  state-changing operation against a target that declares no environment runs with a
  warning today, and the next major release refuses it. That requirement lands on the
  skills that own targets; pilot owns none and reports a constant `local`, so it is
  unaffected either way. Declare `environment:` in the config of each connected skill
  (aiops, monitor, nsx, …) and check what applies before upgrading:
  `vmware-audit policy --operation vm_delete --env <env>`.

### Fixed
- **Policy glob patterns with a leading wildcard silently matched nothing.** A rule written
  `operations: ["*_delete"]` parsed fine, read correctly, and never fired — only a trailing
  `*` was honoured. Now full glob matching, for operations and environments alike.

### Notes
- Requires `vmware-policy>=1.8.0`; publish that package first.
- `vmware-audit policy` reports which rules are in force and where they came from —
  including the case where your rules file exists but failed to parse, which previously
  looked identical to "policy is working".

### Fixed — pre-release review (2026-07-19)

- **`get_skill_catalog` now lists `avi`.** The docs advertise cross-skill AVI workflows
  ("drain server, patch, restore traffic") but the design catalog carried no avi entry,
  so an agent calling `get_skill_catalog` to design exactly that workflow got zero avi
  tools. 13 curated tools added; the catalog is now 69 across 8 skills.
- **Tool and template tables were short.** `capabilities.md` listed 11 of 13 MCP tools
  (missing `review_workflow`, `cancel_workflow`) and 14 of 15 built-in templates
  (missing `investigate_alert`).
- **Removed `config.example.yaml`.** All four keys were inert — `database` and
  `templates_dir` are hardcoded constants with no config or env override, and
  `policy_rules` / `audit_db` belong to vmware-policy. It told operators to copy a file
  to a path nothing reads.

## v1.6.2 (2026-06-24) — MCP Registry registration

Adds the `mcp-name` marker to the README so the package can register on the MCP Registry (踩坑 #31). No functional code changes.

## v1.6.1 (2026-06-24) — version alignment

No functional changes — version bumped to stay aligned with the VMware skill family release.

## v1.5.38 (2026-06-12) — backlog finish: cancel_workflow, templates split

### Added
- **`cancel_workflow` MCP tool** + a terminal `CANCELLED` state. An approval-rejected or cancelled
  workflow can no longer be run — `run_workflow`/`resume_after_approval` refuse it with a teaching error,
  and cancel is itself audited. Closes a gap where a rejected PENDING workflow could still execute. (#7)

### Changed
- Split `templates.py` (1156 lines) into a `templates/` package and the MCP server into `tools/*`, all
  under the 800-line cap; builtin template names/IDs unchanged. (#8)

## v1.5.37 (2026-06-12) — backlog: remove dead states

### Changed
- Removed never-assigned workflow states (`MONITORING`, `COMMITTING`, `BLOCKED_BY_POLICY`) and the
  unreachable `mark_blocked`; verified no persisted state could carry them. (#9)

## v1.5.36 (2026-06-12) — never report work that wasn't performed

### Fixed
- **No-op "completed" workflows eliminated** — with no real dispatcher wired, the executor recorded
  every step as success and marked the workflow COMPLETED. Steps are now `not_executed` and the run
  finishes `dispatch_required` with a pending-step list; `COMPLETED` requires genuine execution.
- **Redacted secrets can't be silently dispatched** — a workflow resumed in a fresh process loaded
  `***` placeholders; dispatch now halts with a teaching error telling the operator to re-source them.
- **Crash-resume no longer skips an in-flight step as done** — a `running` step on resume is marked
  `interrupted` and the run halts with a verify-then-retry/rollback message.
- **Approval gating is now enforced** (not advisory) — ungated destructive workflows are refused
  unless `force=True` (audited).
- Step-ref resolution raises on invalid/forward refs instead of passing a literal placeholder;
  rollback outcomes are persisted; illegal state transitions rejected.

## v1.5.35 (2026-06-10) — security hardening: don't persist secrets; lock down state

### Fixed
- **Workflow params are redacted before SQLite persistence** — passwords/tokens stay
  in memory for the running workflow but are never written to `~/.vmware/workflows.db`.
- **State storage** dir is 0700 and the DB (incl. WAL/SHM) is 0600.
- **Custom-workflow `name`** is validated against path traversal before becoming a
  filename; the workflows dir is created 0700.

This release aligns the whole family back to a single version (1.5.35); vmware-policy and vmware-pilot return to the shared number after sitting at 1.5.22.

## v1.5.22 (2026-05-08)

**Smithery onboarding** — `vmware-pilot` is now installable via Smithery.

- **feat:** Added `Dockerfile` (Python 3.12-slim + uv) for containerized stdio MCP server.
- **feat:** Added `smithery.yaml` declaring stdio transport + config schema for the Smithery registry.
- **feat:** Added `mcp_server/__main__.py` so `python -m mcp_server` works inside the container.
- **align:** Tracks v1.5.22 family bump.

## v1.5.21 (2026-05-08)

**Family alignment** — no source changes in this skill. Skipped v1.5.20 family bump; this is the catch-up release.

- **deps:** Bumped `python-multipart` 0.0.26 → 0.0.27 (transitive, fixes GHSA HIGH DoS via unbounded multipart headers).
- **align:** Tracks family v1.5.20 + v1.5.21 alignment.

## v1.5.19 (2026-05-06)

**Family alignment** — no source changes in this orchestration skill.

- **align:** Catching up to family v1.5.19 (was at v1.5.17). Tracks fixes in vmware-nsx (CRITICAL CLI imports), vmware-vks (ApiClient leak), vmware-harden (Twin indexes + LEFT JOIN report), vmware-policy (approval gate AND→OR + singleton lock). yjs review 2026-05-06.
- **smoke:** Family `scripts/family_smoke.sh` adds Check 3b — recursive `--help` on every subcommand to surface broken lazy imports (CLAUDE.md 踩坑 #27).

## v1.5.17 (2026-05-01)

**v2 architecture follow-ups** — implements 4 of 5 additive gaps identified in the v1.5.16 architecture audit (`docs/architecture-audit-2026-04-30.md`).

- **feat:** New `investigate_alert` template — causal-chain root-cause workflow that codifies the four-criteria completeness check (falsifiability/sufficiency/necessity/mechanism) from the EHE investigation protocol. Round 1 parallel-gathers alarms + events + Aria alerts, then pauses for synthesis. Optional `deep_dive=True` adds a second round of broader gathering.
- **feat:** New `review_workflow` MCP tool — structural sanity check before execution. Detects delete-then-use cycles, ungated destructive operations, placeholder parameters, non-contiguous parallel groups, destructive ops inside parallel groups. Returns `verdict: approved | needs_revision` plus per-finding severity, kind, and message.
- **feat:** `WorkflowStep.group_id` field + `parallel_group(group_id, steps)` helper — siblings sharing a non-empty group_id may be dispatched concurrently by the calling agent. Backward-compatible deserialization for pre-existing workflows.
- **docs:** SKILL.md and `references/integration-patterns.md` now document the dispatch contract explicitly: pilot is the dispatcher (plans, state, approval gates), the calling AI agent is the executor (invokes per-step MCP tools).
- **align:** Family version bump to v1.5.17.

Tests: 75 → 96 passing (4 parallel_group + 7 investigate_alert + 10 review). MCP tools: 11 → 12. Templates: 14 → 15.

## v1.5.16 (2026-04-30)

**Enterprise Harness Engineering alignment** — adapted from the Linkloud × addxai framework articles ([part 1](https://mp.weixin.qq.com/s/hz4W7ILHJ1yz_pG0Z1xP-A), [part 2](https://mp.weixin.qq.com/s/F3qYbyB3S8oIqx-Y4BrWNQ)).

- **docs:** New `docs/architecture-audit-2026-04-30.md` — verifies pilot already follows the v2 Dispatcher + one-shot subagent pattern from EHE; identifies 5 additive gaps tracked as separate work items (G1-G5: investigate template, review gate, parallel step, L5 hooks, dispatch contract docs).
- **align:** Family version bump 1.5.14 → 1.5.16 (skipping 1.5.15 to align with the rest of the family).

## v1.5.14 (2026-04-21)

- Align with VMware skill family v1.5.14 (code review follow-up fixes by @yjs-2026)

## v1.5.13 (2026-04-21)

**Bug fixes from code review 2026-04-20**

- **fix(P0):** `executor.py` — added `_resolve_step_refs()` for step-to-step variable substitution; `plan_and_approve` workflow no longer passes literal `__from_step_0__` as plan_id
- **fix:** `templates.py` — `clone_and_test` now accepts both `memory_mb` and `memory_gb` in change_spec for reconfigure routing (was only checking `memory_mb` while docstring documented `memory_gb`)
- **fix:** `server.py` — `approve()` now rejects empty `approver` parameter with clear error message for audit trail integrity

# VMware Pilot — Release Notes

## v1.5.12 (2026-04-17)

**Bug fix from code review by @yjs-2026**

- **fix:** `rollback()` — persist workflow state after entering ROLLING_BACK and after each rollback step, preventing state inconsistency if process crashes mid-rollback

## v1.5.11 (2026-04-17)

- Align with VMware skill family v1.5.11 (AVI 22.x fixes from @timwangbc)

## v1.5.10 (2026-04-16)

- Security: bump python-multipart 0.0.22→0.0.26 (DoS via large multipart preamble/epilogue)
- Align with VMware skill family v1.5.10

## v1.5.8 (2026-04-15)

- Align with VMware skill family v1.5.8 (NSX/AVI/Aria/AIops bug fixes)

## v1.5.7 (2026-04-15)

- Fix: `plan_and_approve` workflow template used `__from_step_0__` as a literal placeholder string, causing `vm_apply_plan` to receive an invalid plan_id at execution. Executor now resolves `__from_step_N__` (full result) and `__from_step_N.field__` (nested field) references against completed steps' `result` at dispatch time. Also applied to rollback_params.
- Align with VMware skill family v1.5.7

## v1.5.6 (2026-04-15)

- Align with VMware skill family v1.5.6 (AVI bugfixes + packaging hotfix)

## v1.5.5 (2026-04-15)

- Align with VMware skill family v1.5.5

## v1.5.4 (2026-04-14)

- Security: bump pytest 9.0.2→9.0.3 (CVE-2025-71176, insecure tmpdir handling)

## v1.5.0 (2026-04-12)

### Anthropic Best Practices Integration

- **[READ]/[WRITE] tool prefixes**: All MCP tool descriptions now start with [READ] or [WRITE] to clearly indicate operation type
- **Read/write split counts**: SKILL.md MCP Tools section header shows exact read vs write tool counts
- **Negative routing**: Description frontmatter includes "Do NOT use when..." clause to prevent misrouting
- **Broadcom author attestation**: README.md, README-CN.md, and pyproject.toml include VMware by Broadcom author identity (wei-wz.zhou@broadcom.com) to resolve Snyk E005 brand warnings

## v1.4.9 (2026-04-11)

- Security: bump cryptography 46.0.6→46.0.7 (CVE-2026-39892, buffer overflow)
- Fix: require explicit "VMware" context in pilot workflow routing triggers
- Fix: clarify vmware-policy compatibility field (Python transitive dep, not required standalone binary)
- Remove stale dist/ build artifacts from git tracking
- Version aligned with VMware skill family (1.4.5 → 1.4.9)

## v1.4.5 — 2026-04-03

- **Security**: bump pygments 2.19.2 → 2.20.0 (fix ReDoS CVE in GUID matching regex)
- **Infrastructure**: add uv.lock for reproducible builds and Dependabot security tracking

## v1.4.0 — 2026-03-29

Initial release. Multi-step workflow orchestration with approval gates for VMware MCP skills.

- 11 MCP tools: design/plan/create/run/approve/rollback/status + discovery
- 4 built-in workflow templates: clone_and_test, incident_response, plan_and_approve, compliance_scan
- Interactive design mode: natural language → draft → edit → confirm → execute
- Custom YAML templates: drop in ~/.vmware/workflows/, hot-reload without restart
- State persistence: SQLite at ~/.vmware/workflows.db, survives restarts
- Approval gates: pause execution for human review before destructive operations
- Rollback: reverse completed steps in order on failure
- Skill catalog: 7 skills, 162 tools available for workflow composition
- blocked_by_policy state: mid-workflow policy denial without auto-rollback
- 32 unit tests