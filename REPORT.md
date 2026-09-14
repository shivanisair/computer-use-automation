# Computer-Use Automation System — Implementation Report

## Executive summary

This project implements a complete computer-use automation vertical slice: an LLM drives a real browser during discovery, the successful interaction is converted into a typed and versioned reusable capability artifact, and a separate deterministic replay path executes that artifact with caller-supplied inputs without using an LLM for decisions.

The demonstration targets the ParaBank Customer Care page. Discovery navigates to Contact Us, fills four form fields, extracts the Message field, and records the successful workflow. Replay loads that artifact, resolves stable semantic targets, performs the actions, returns the declared output, verifies observable success conditions, classifies failures, and supports transfer of the same browser session to a human for recoverable situations.

## Requirement mapping

| Requirement | Implementation |
| --- | --- |
| LLM drives a real computer/browser surface | `app/agent.py`, `app/browser.py`, and `app/surface.py` use structured model decisions with a live Playwright browser. |
| Successful run becomes a reusable artifact | `app/browser.py` records the successful steps and saves `evidence/artifacts/fill_customer_care_form.json`. |
| Typed/versioned artifact | `app/artifact.py` defines the Pydantic capability schema and artifact version `1.3`. |
| Ordered actions and stable target identification | Artifact steps contain actions plus semantic `role`/`name` targets. |
| Typed inputs | Artifact input schema declares `name`, `email`, `phone`, and `message` as required strings. |
| Typed outputs/data to extract | Artifact output schema declares required string output `message_value`; an `extract` step produces it. |
| Explicit success/checkpoint conditions | Artifact contains a URL-path checkpoint plus field-value checkpoints tied to invocation parameters. |
| Deterministic replay without LLM | `app/replay.py` loads and executes the artifact directly; it contains no OpenAI client/API decision loop. |
| Stable replay targeting | `app/surface.py` resolves normalized semantic role/name targets and rejects missing/ambiguous matches. |
| Structured result | Replay returns `ReplayResult` with status, outputs, failure category, failed step, reason, expected, and observed fields. |
| Error/outcome taxonomy | `app/escalation.py` models business outcomes, recoverable automation errors, and hard failures. |
| Human escalation/control transfer | Discovery and replay support same-session human handoff; replay includes `--demo-handoff-step`. |
| Safety/guardrails | `app/guardrails.py` enforces domain, route, action, and blocked-target policies. |
| Evidence | `evidence/` contains the saved artifact, structured logs, and screenshots. |

## 1. System lifecycle

The implementation deliberately separates **discovery** from **replay**.

During discovery, the system opens the real ParaBank site and observes the interactive surface. The model receives a compact representation of the page and chooses a structured action. The runner validates the action with guardrails, executes it, converts the temporary observation target into a semantic target, and records successful actions. Runtime output state is also supplied back to the discovery loop so the model can recognize when the requested extraction has already occurred and terminate instead of repeatedly extracting the same value.

After successful completion, the run is serialized as a capability artifact. The artifact is the boundary between the probabilistic discovery phase and deterministic execution.

During replay, the model is not asked what to do. The runner validates the artifact and invocation inputs, opens the target application, checks each step's URL precondition, resolves the recorded semantic target, applies guardrails, executes the recorded action, collects declared outputs, verifies explicit success checkpoints, and emits a structured result.

## 2. Discovery design

### Observation

`PlaywrightSurface` converts the current page into an observation containing the title, URL, and interactive elements. Elements include a temporary observation ID together with semantic information such as role, accessible name, tag, and current value.

The temporary ID is useful for one model decision because it provides a concise way to identify an element in the current observation. It is not treated as a durable replay locator.

### Structured model actions

The model selects from typed actions such as `click`, `fill`, `select`, `extract`, `wait`, `complete`, and `escalate`. The discovery prompt defines the exact capability goal, including the requirement to fill the four fields, extract the Message value, avoid submitting the form, and complete only when the requested work is finished.

### Bounded autonomy

Discovery is bounded by explicit limits on steps, attempts, and handoffs. This prevents an unsuccessful model/browser interaction from looping indefinitely.

### Runtime output tracking

Extracted values are kept in discovery runtime state. The reusable artifact records that an extraction should occur and which declared output it produces; the reusable action should not depend on a particular discovery-run value.

## 3. Capability artifact

The artifact is defined using typed Pydantic models and carries an explicit version. The saved example capability is `fill_customer_care_form` at version `1.3`.

Its contract contains four required string inputs:

- `name`
- `email`
- `phone`
- `message`

It declares one required string output:

- `message_value`

The recorded workflow contains six ordered actions: navigate through the Contact Us link, fill the four Customer Care fields, then extract the Message field.

Parameterized fill steps store an `input_ref` such as `name` or `email`. The invocation value is therefore provided by the replay caller rather than being hard-coded into the reusable capability action.

The artifact also declares five success checkpoints: the final URL path must be `/parabank/contact.htm`, and each of the four form fields must contain its corresponding invocation value.

## 4. Deterministic replay

`app/replay.py` is intentionally separate from the LLM discovery path. Replay behavior is derived from the artifact and supplied inputs rather than fresh model reasoning.

Replay performs the following sequence:

1. Load and Pydantic-validate the artifact.
2. Require the supported artifact version.
3. Validate supplied inputs against the typed input schema.
4. Open the known target application.
5. Before every step, compare the current normalized route with the recorded precondition.
6. Run the action through guardrails.
7. Resolve the recorded semantic target.
8. Execute the action using the invocation value referenced by `input_ref` where applicable.
9. Capture extraction results by declared output name.
10. Verify explicit success checkpoints against the live UI.
11. Return only outputs declared by the artifact output schema.
12. Print/log a structured replay result.

This separation is important: the artifact is not merely a transcript for another model prompt. It is executable data for a deterministic engine.

## 5. Locator strategy

The locator strategy distinguishes **discovery identity** from **replay identity**.

Observation IDs are ephemeral. Once an action succeeds during discovery, the system records a semantic target containing the element's role and accessible name. Replay resolves that pair against the current live surface using normalized text.

A target must resolve uniquely. Zero matches and multiple matches are explicit recoverable errors. This is safer than silently choosing the first approximate element and also makes UI drift visible in evidence.

ParaBank may introduce an ephemeral `jsessionid` into the URL. URL normalization strips that session-specific component before deterministic route comparisons so replay does not mistake a new session identifier for application drift.

## 6. Outputs and success conditions

Outputs are part of the capability contract rather than incidental print statements. The example artifact declares `message_value`, and the final recorded `extract` action maps the Message textbox to that output name.

Replay collects extraction results during execution and returns only outputs declared by the artifact. A required output that was not produced is a hard failure because the capability contract was not fulfilled.

Success is separately verified through observable checkpoints. This prevents the system from equating "the automation command did not throw" with "the requested state exists in the application." Field checkpoints read the current UI values and compare them with invocation inputs.

## 7. Error taxonomy

The project uses three explicit categories.

### Business outcome

A `business_outcome` represents a legitimate application result that is meaningful to the caller but is not an automation-system defect. The type is modeled and demonstrated even though the selected ParaBank happy path does not naturally produce a business rejection/outcome.

### Recoverable automation error

A `recoverable_error` means deterministic automation cannot safely continue on its own but the situation may be recoverable with human intervention. Examples include a missing/ambiguous semantic target, changed route precondition, or a checkpoint state that does not match expectations.

### Hard failure

A `hard_failure` covers unsafe or invalid conditions such as an unsupported artifact version, invalid/missing required input, missing artifact, blocked guardrail action, or infrastructure/configuration failure. Hard failures are not converted into handoff opportunities merely to continue execution, because doing so could bypass the safety contract.

`app/error_demo.py` provides a compact demonstration of the taxonomy.

## 8. Human control transfer

Human handoff preserves the existing browser session. The automation does not close the browser and ask the operator to reproduce the state elsewhere.

For evidence, deterministic replay supports `--demo-handoff-step N`. Before that recorded step, the runner creates a structured handoff request, captures a screenshot, transfers ownership to the human, prints a precise instruction, and waits. The operator performs the step in the same browser and presses Enter. Automation captures the post-handoff state, verifies relevant observable state for fill steps, logs the ownership transition, and resumes.

This demonstrates explicit control transfer rather than treating "human escalation" as only an error message.

## 9. Safety model

Guardrails are checked independently of model reasoning. The demo constrains automation to ParaBank, approved routes, and approved action types. It explicitly blocks the Customer Care submission target.

That blocked submission is intentional. The demonstrated capability is "fill and inspect" rather than "send a customer-care request." Keeping submission out of scope reduces external side effects and makes the safety boundary easy to inspect.

Guardrail violations are hard failures. A human handoff is not offered as a mechanism for bypassing the blocked action.

## 10. Evidence and observability

The project writes structured JSONL logs through `RunLogger` and saves browser screenshots for discovery, replay, and handoff evidence. The repository includes the saved capability artifact so the reviewer can inspect the exact serialized contract independently of the Python implementation.

Important evidence locations are:

- `evidence/artifacts/`
- `evidence/logs/`
- `evidence/screenshots/`
- `evidence/replay/`

The replay result itself is structured and includes success/failure status, declared outputs, and diagnostic fields for failures.

## 11. Tradeoffs and limitations

This implementation prioritizes a complete and inspectable vertical slice over broad product coverage.

The semantic locator uses role and accessible name rather than a multi-strategy locator graph. This is stable enough for the demonstration and much better than replaying temporary IDs, but a production system would record multiple candidate locator strategies and confidence/uniqueness metadata.

The artifact schema supports multiple primitive value types, while the current demonstration inputs and output are strings. Production replay should add comprehensive output type coercion and validation for every declared type.

The current handoff UI is terminal-coordinated. A production system would expose ownership state in a dedicated operator interface and support timeouts, cancellation, authentication, and richer resume policies.

The business-outcome category is explicit in the architecture but is not naturally exercised by the selected non-submitting ParaBank workflow. A broader test suite would include a workflow with a genuine business rejection or alternate successful outcome.

Finally, production use would require stronger secret management and configurable log redaction, automated unit/integration tests, artifact migration tooling, concurrency/session isolation, and broader browser/application support.

## 12. Future improvements

The next improvements would be:

1. record multiple semantic/DOM locator candidates with deterministic fallback rules;
2. add artifact migration and compatibility tests across versions;
3. validate/coerce all output types against the output schema;
4. add automated replay tests using controlled fixtures;
5. add richer business-outcome detectors;
6. add configurable sensitive-value redaction and secret storage;
7. add an operator UI for handoff/resume; and
8. add CI that compiles the project and runs non-browser contract tests.

## Conclusion

The project demonstrates the requested core transition from probabilistic computer-use discovery to deterministic reusable automation. The successful browser interaction becomes a typed/versioned artifact with explicit inputs, outputs, semantic targets, and checkpoints. Replay consumes that artifact without an LLM decision loop, reports structured outcomes, detects drift instead of guessing, applies safety guardrails, and can transfer the same live session to a human for recoverable situations.