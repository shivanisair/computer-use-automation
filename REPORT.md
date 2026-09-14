# Computer-Use Automation System — Implementation Report

## 1. Architecture

This project implements a complete computer-use automation vertical slice against the public ParaBank demo site. The central architectural decision is to separate probabilistic **discovery** from deterministic **production replay**.

During discovery, `app/browser.py` opens a live Playwright browser and `app/agent.py` runs an LLM-driven observe → decide → act loop. `app/surface.py` converts the current browser state into a compact observation containing the URL, title, and interactive controls. The model selects a typed action such as `click`, `fill`, `select`, `extract`, `wait`, `complete`, or `escalate`. Before execution, the action is checked by independent guardrails. Successful actions are recorded as reusable semantic steps rather than as a raw model transcript.

The demonstrated capability navigates to ParaBank Customer Care, fills Name, Email, Phone, and Message, extracts the Message value, and deliberately does not submit the form. Discovery is bounded by explicit step, attempt, and handoff limits so model-driven execution cannot loop indefinitely.

After discovery succeeds, `app/artifact.py` serializes the interaction as a typed capability artifact. `app/replay.py` is a separate deterministic executor: it loads the artifact, validates invocation inputs, resolves recorded semantic targets, executes the fixed action sequence, collects declared outputs, verifies checkpoints, and returns a structured result. It does not use an LLM to decide replay actions.

The main trade-off is deliberate simplicity. Playwright and accessibility-style semantic targeting give a strong, inspectable implementation for one real surface without prematurely building distributed scheduling, multi-tenant infrastructure, or a full operator console. The abstraction boundary is the surface adapter: discovery/replay operate on semantic observations and actions, while browser-specific perception and control live in `PlaywrightSurface`.

## 2. Artifact schema

The reusable capability is defined with typed Pydantic models and an explicit artifact version (`1.3`). The saved example is `evidence/artifacts/fill_customer_care_form.json`.

The artifact is a contract, not merely a list of clicks. It records the capability name and goal, target domain, version and creation metadata, typed input schema, typed output schema, ordered action steps, semantic targets, input/output references, URL preconditions, and explicit success checkpoints.

The example capability requires four string inputs: `name`, `email`, `phone`, and `message`. Parameterized fill steps store an `input_ref` rather than the discovery-run value, so replay receives fresh caller-provided values instead of persisting reusable PII in the artifact. It declares one required string output, `message_value`, produced by an `extract` step.

Recorded targets use semantic `role` + accessible `name` pairs. Temporary discovery observation IDs are intentionally not persisted as replay identity. The six recorded actions are: navigate through Contact Us, fill the four Customer Care fields, and extract Message.

The artifact declares five success checkpoints: the final URL path must be `/parabank/contact.htm`, and each of the four fields must contain its corresponding invocation value. Keeping outputs and checkpoints separate is intentional: an output is data returned to the caller, while a checkpoint proves that the requested application state was actually reached.

The schema is versioned so replay can reject unsupported contracts rather than silently misinterpreting older artifacts. A production implementation would add formal migration tooling, richer locator candidates and confidence metadata, and comprehensive type coercion/validation for every declared primitive type.

## 3. Determinism & error handling

Replay is intentionally isolated from LLM discovery. Given an artifact and input parameters, `app/replay.py` validates the artifact/version and typed inputs, opens the known target, checks each recorded URL precondition, applies guardrails, resolves the semantic target, executes the recorded action, captures declared outputs, verifies live checkpoints, and returns a structured `ReplayResult`.

Determinism comes from replaying executable artifact data rather than asking a model to reinterpret a transcript. Semantic role/name targets are normalized and must resolve uniquely. Zero matches and ambiguous matches are errors rather than opportunities to guess. ParaBank may add an ephemeral `jsessionid` to URLs, so URL normalization removes that session-specific component before route comparisons.

The result/error contract separates three categories. A `business_outcome` is a legitimate application result the caller needs to know about, not an automation crash. A `recoverable_error` means deterministic automation cannot safely continue but the situation may be recoverable through intervention, such as a missing/ambiguous semantic target or unexpected checkpoint state. A `hard_failure` represents invalid or unsafe conditions such as unsupported artifact versions, missing/invalid required inputs, blocked guardrail actions, missing artifacts, or infrastructure/configuration failures.

Structured failures include diagnostic context such as category, reason, failed step, expected state, and observed state. `app/error_demo.py` demonstrates all three categories. The selected non-submitting ParaBank happy path does not naturally produce a business rejection, so the business-outcome category is explicitly modeled and demonstrated rather than artificially changing the main workflow solely to manufacture one.

Success is not inferred from the absence of exceptions. Replay separately verifies URL and field-value checkpoints against the live surface and requires declared outputs to be produced. This makes failures debuggable and prevents blind progression through an unexpected state.

## 4. Heterogeneity & multi-tenant

The implementation targets one web application, but the core contract separates **what the flow means** from **how a particular surface is perceived and controlled**. Artifact steps contain abstract action types, semantic targets, parameter references, outputs, and checkpoints. Browser-specific observation and execution are concentrated in the surface adapter.

For a legacy web application, a new adapter could build observations from accessibility information, frames, DOM metadata, OCR/vision, or deterministic coordinate anchors while exposing the same semantic action interface to discovery and replay. For a desktop application, the adapter could use OS accessibility APIs or a computer-use/vision layer. The artifact and replay contract would remain largely unchanged; surface-specific locator representations could be added as typed target variants while preserving the same action/input/output/checkpoint model.

For multi-tenant reuse, the capability should be associated with an application/vendor family and a compatible version range rather than with one institution's concrete deployment. Shared steps would remain canonical, while tenant-specific configuration could provide approved route patterns, target aliases/overrides, and environment metadata. Invocation data such as member IDs or form values remains parameterized rather than baked into the artifact.

Drift should be detected rather than hidden. Replay already fails on route, target, or checkpoint mismatches. At scale, those signals could be aggregated by vendor/version/tenant to distinguish a single-tenant configuration difference from a product-wide UI change. A reviewed override could specialize a canonical artifact for a tenant; a vendor-wide change could produce a new artifact version. Production replay should never silently relearn a changed workflow with an unrestricted LLM because that would weaken deterministic execution and reviewability.

This design avoids requiring a new recording for every institution while still making tenant-specific differences explicit, reviewable, and bounded by policy.

## 5. Escalation & handoff

The system has a real same-session human-control seam rather than treating escalation as a TODO. Discovery can escalate when the model is stuck, and deterministic replay can escalate recoverable conditions. The handoff request records the error category/reason, current step, current URL/state context, and last action so an operator has enough information to intervene.

For deterministic evidence, replay supports `--demo-handoff-step N`. Before the selected recorded step, automation pauses, creates a structured handoff request, captures evidence, and leaves the existing browser session open. The human performs the required action in that same session and signals resume from the terminal. Automation then inspects the resulting live state, records the ownership transition/evidence, and continues from the preserved session.

The control model is therefore explicit: automation owns the session during normal execution, yields ownership during handoff, waits while the human acts, and reacquires ownership only after the human signals resume. Hard safety failures are not converted into handoff opportunities merely to bypass policy.

The operator surface is intentionally minimal and terminal-coordinated. A production system would add authenticated operator identity, visible ownership state, timeout/cancel controls, richer screenshots/state context, audit records of manual actions, and explicit resume policies, but the same-session pause/cede/resume mechanism is implemented here.

## 6. Safety

Safety checks are independent of LLM reasoning. `app/guardrails.py` enforces explicit allowed domains, routes, action types, and blocked targets. The agent cannot authorize itself to leave those boundaries.

The Customer Care submit action is deliberately blocked. The demonstrated capability is "fill and inspect," not "send a customer-care request." Submission would create an external side effect, so keeping it outside the allowed capability makes the safe/reversible versus risky/irreversible boundary concrete. Guardrail violations are hard failures; human handoff is not a mechanism for bypassing them.

Reusable artifacts parameterize runtime values with `input_ref` instead of persisting discovery-run form values. Logs/actions are designed around structured metadata rather than secrets. The demo uses synthetic values and no real banking credentials or PII.

The current implementation is a demonstration rather than a production financial-data system. Production use would require centralized secret management, systematic configurable redaction across logs/screenshots/traces, stricter PII classification, retention controls, authenticated operators, audit policy, and broader security testing. These limits are intentional and documented rather than implied to be solved by the demo.

## 7. Cuts

The project prioritizes a small, complete vertical slice over breadth. It deliberately leaves out a full operator web console, desktop/OS automation adapters, multi-tenant infrastructure, automatic artifact migrations, a multi-strategy locator graph, broad automated integration testing, and production-grade secret/redaction infrastructure.

The semantic locator currently uses role + accessible name rather than recording several fallback strategies. This is substantially more stable than replaying temporary observation IDs and exposes drift cleanly, but production artifacts should include deterministic fallback candidates plus uniqueness/confidence metadata.

The business-outcome type is implemented and demonstrated, but the selected ParaBank flow intentionally stops before submission and therefore does not naturally exercise a business rejection. A broader fixture/test suite would add a real alternate business result without weakening the safety boundary of this capability.

The handoff UI is terminal-based. This keeps the control-transfer mechanism real while avoiding time spent on an operator-console frontend that is outside the core evaluation. Production would add authentication, cancellation/timeouts, explicit operator ownership, and richer auditing.

With more time, the next work would be: artifact migration/compatibility tests; deterministic multi-locator fallback rules; comprehensive typed output validation; controlled replay fixtures and CI; richer business-outcome detectors; configurable sensitive-data redaction and secret storage; a dedicated operator UI; and vendor/tenant compatibility metadata with reviewed overrides.

The resulting system demonstrates the required through-line: a genuine LLM-driven interaction with a live surface becomes a typed/versioned capability, that capability replays deterministically with caller inputs and declared outputs, runtime failures are classified deliberately, safety policy remains independent of the model, and a human can take control of the same live session when recovery is appropriate.