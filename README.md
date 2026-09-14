# Computer-Use Automation System

A small end-to-end computer-use automation system that demonstrates how an LLM-driven browser run can be converted into a reusable, typed capability and replayed deterministically without an LLM.

## Demo workflow

The included vertical slice uses the ParaBank demo application. The capability:

1. opens ParaBank,
2. navigates to **Contact Us**,
3. fills the Customer Care form with caller-supplied `name`, `email`, `phone`, and `message` values,
4. does **not** submit the form,
5. extracts the Message field as the declared output `message_value`, and
6. verifies explicit success checkpoints.

The purpose of the project is the automation architecture, not the ParaBank workflow itself.

## Architecture

- `app/agent.py` — LLM decision layer used only during discovery.
- `app/browser.py` — discovery runner. Observes the real browser surface, asks the model for structured actions, applies guardrails, records semantic targets, tracks outputs, and saves a capability artifact after success.
- `app/surface.py` — Playwright surface abstraction and semantic role/name target resolution.
- `app/artifact.py` — typed, versioned capability artifact schema.
- `app/replay.py` — deterministic replay engine. Loads the saved artifact, validates typed inputs, resolves semantic targets, executes recorded actions without an LLM, extracts declared outputs, verifies checkpoints, and returns a structured result.
- `app/guardrails.py` — domain, route, action, and blocked-target safety checks.
- `app/escalation.py` — error taxonomy and human-handoff structures.
- `app/logger.py` — structured JSONL evidence logging.
- `app/error_demo.py` — small demonstration of the three error categories.
- `evidence/` — saved capability artifact, discovery/replay logs, screenshots, and handoff evidence.

## Key design choices

### LLM discovery, deterministic replay

The LLM is used to discover the workflow against the real browser surface. A successful run is converted into a reusable artifact. Replay consumes only that artifact plus invocation inputs; `app/replay.py` does not call the OpenAI API.

### Stable semantic targets

Temporary observation IDs are useful while the model is deciding what to do, but they are not the replay contract. Recorded steps store semantic targets such as:

```json
{
  "role": "textbox",
  "name": "email"
}
```

Replay resolves the target by normalized role and accessible name. Missing or ambiguous targets become explicit recoverable errors rather than silently clicking a different element.

### Typed capability contract

Artifact version `1.3` declares:

- typed input parameters,
- typed output definitions,
- ordered recorded actions,
- semantic targets,
- `input_ref` references for parameterized actions,
- success checkpoints, and
- capability metadata.

Invocation values are supplied at replay time instead of being permanently embedded in reusable fill steps.

### Success verification

Replay does not treat a successful Playwright call as sufficient proof of completion. It verifies observable UI state using an expected URL path and expected field values.

### Error taxonomy and handoff

The system distinguishes:

- `business_outcome` — the application produced a legitimate outcome that is not an automation defect;
- `recoverable_error` — automation cannot safely continue by itself, for example a missing/ambiguous semantic target or changed route;
- `hard_failure` — configuration, contract, safety, or infrastructure failure that should not be bypassed.

Recoverable situations can transfer the same live browser session to a human operator. The operator completes the requested step and returns control to automation. Guardrail failures remain hard failures so handoff cannot be used to bypass safety policy.

## Setup

Requires Python and Chromium through Playwright.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
export OPENAI_API_KEY="your-key"
```

`OPENAI_API_KEY` is required for discovery. Deterministic replay itself does not require an LLM call.

## Run discovery from a natural-language goal

The discovery runner accepts both the target application/entry point and a natural-language goal. For the included ParaBank vertical slice, run:

```bash
python -m app.browser \
  --target-url "https://parabank.parasoft.com/parabank/index.htm" \
  --goal "Navigate to the Contact Us page and fill out the Customer Care form using Name 'Demo User', Email 'demo@example.com', Phone '555-0100', and Message 'Automated test message'. Do not submit the form. After filling all four fields, extract the Message field value as output 'message_value'. Complete only after the value has been extracted."
```

The LLM observes the live UI and decides the discovery actions needed to achieve that goal. A successful run saves/updates the reusable artifact at:

```text
evidence/artifacts/fill_customer_care_form.json
```

Discovery is intentionally bounded by maximum step, attempt, and handoff counts. Running `python -m app.browser` without arguments uses the same ParaBank target and demo goal as defaults.

## Run deterministic replay

Replay the resulting artifact with fresh invocation values:

```bash
python -m app.replay \
  --name "Replay User" \
  --email "replay@example.com" \
  --phone "555-0200" \
  --message "Replay test message"
```

Expected behavior: replay opens ParaBank, follows the recorded semantic steps without asking an LLM what to do, verifies the checkpoints, and prints a structured result whose declared output includes `message_value`.

This demonstrates the intended through-line: **natural-language goal + target → LLM discovery → typed/versioned artifact → deterministic replay with caller-supplied inputs and declared outputs**.

## Human-handoff evidence

To deliberately transfer one recorded replay step to a human in the same browser session:

```bash
python -m app.replay \
  --name "Replay User" \
  --email "replay@example.com" \
  --phone "555-0200" \
  --message "Replay test message" \
  --demo-handoff-step 3
```

Perform the requested step in the already-open browser and press Enter in the terminal. Replay verifies the human-performed state and resumes automation.

## Error taxonomy demo

```bash
python -m app.error_demo
```

## Verification

The core implementation can be checked with:

```bash
python -m py_compile app/*.py
python -m app.error_demo
```

The repository already includes saved discovery/replay evidence. Running discovery or replay again will generate new evidence files. To inspect the committed evidence without changing it:

```bash
find evidence -maxdepth 2 -type f -print | sort
```

## Evidence

The repository includes evidence under:

- `evidence/artifacts/` — reusable capability artifact;
- `evidence/logs/` — structured discovery/replay logs;
- `evidence/screenshots/` — discovery screenshots;
- `evidence/replay/` — replay and human-handoff screenshots.

## Safety

The demo uses explicit domain/route/action allowlists. The Customer Care **Send** action is blocked because the demonstrated capability is intentionally limited to filling and inspecting the form, not submitting it.

## Limitations

This is a focused take-home vertical slice rather than a production browser-agent platform. It demonstrates one application and capability. Semantic role/name targeting depends on usable accessibility metadata, human handoff is terminal-coordinated, and the business-outcome category is modeled/demoed rather than naturally exercised by the happy-path ParaBank workflow. Production work would add richer locator fallbacks, artifact migrations, automated integration tests, secret/redaction controls, and broader business-outcome detection.

See `REPORT.md` for the implementation rationale and requirement mapping.