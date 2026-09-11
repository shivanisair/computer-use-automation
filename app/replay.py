import argparse
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

from app.agent import ActionType
from app.artifact import (
    ARTIFACT_VERSION,
    CapabilityArtifact,
    CheckpointType,
    SemanticTarget,
    ValueType,
)
from app.escalation import (
    ErrorCategory,
    HardAutomationFailure,
    RecoverableAutomationError,
    create_handoff,
)
from app.guardrails import Guardrails
from app.logger import RunLogger
from app.surface import (
    PlaywrightSurface,
    normalize_semantic_text,
)


ARTIFACT_PATH = Path(
    "evidence/artifacts/fill_customer_care_form.json"
)

TARGET_URL = (
    "https://parabank.parasoft.com/parabank/index.htm"
)


class ReplayStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    FAILURE = "failure"


class ReplayResult(BaseModel):
    """
    Structured result returned by deterministic replay.
    """

    status: ReplayStatus
    capability_name: str
    artifact_version: str

    outputs: dict[str, Any] = Field(
        default_factory=dict
    )

    error_category: ErrorCategory | None = None
    failed_step: int | None = None
    reason: str | None = None
    expected: str | None = None
    observed: str | None = None


def parse_arguments() -> argparse.Namespace:
    """
    Parse per-invocation capability inputs.

    These values are supplied by the caller and are not
    permanently stored in the reusable artifact.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Replay a saved capability deterministically "
            "without using an LLM."
        )
    )

    parser.add_argument(
        "--name",
        required=True,
        help="Name to enter in the Customer Care form.",
    )

    parser.add_argument(
        "--email",
        required=True,
        help="Email to enter in the Customer Care form.",
    )

    parser.add_argument(
        "--phone",
        required=True,
        help="Phone number to enter in the Customer Care form.",
    )

    parser.add_argument(
        "--message",
        required=True,
        help="Message to enter in the Customer Care form.",
    )

    return parser.parse_args()


def load_artifact() -> CapabilityArtifact:
    """
    Load and validate the saved reusable capability.
    """

    if not ARTIFACT_PATH.exists():
        raise HardAutomationFailure(
            f"Artifact not found: {ARTIFACT_PATH}"
        )

    artifact_json = ARTIFACT_PATH.read_text(
        encoding="utf-8"
    )

    try:
        artifact = (
            CapabilityArtifact.model_validate_json(
                artifact_json
            )
        )

    except Exception as error:
        raise HardAutomationFailure(
            f"Artifact validation failed: {error}"
        ) from error

    if artifact.version != ARTIFACT_VERSION:
        raise HardAutomationFailure(
            "Unsupported artifact version: "
            f"{artifact.version}. "
            f"Expected {ARTIFACT_VERSION}."
        )

    return artifact


def normalize_url(
    url: str,
) -> tuple[str, str]:
    """
    Normalize ParaBank session URLs so ephemeral jsessionid
    values do not make deterministic replay fail.
    """

    parsed = urlparse(url)

    path = parsed.path

    if ";jsessionid=" in path:
        path = path.split(
            ";jsessionid=",
            1,
        )[0]

    return parsed.hostname or "", path


def validate_inputs(
    artifact: CapabilityArtifact,
    inputs: dict[str, Any],
) -> None:
    """
    Validate invocation inputs against the artifact's typed
    input contract.
    """

    declared_names = {
        parameter.name
        for parameter in artifact.input_schema
    }

    supplied_names = set(inputs)

    unexpected = supplied_names - declared_names

    if unexpected:
        raise HardAutomationFailure(
            "Unexpected input parameter(s): "
            + ", ".join(sorted(unexpected))
        )

    for parameter in artifact.input_schema:
        if parameter.required:
            if parameter.name not in inputs:
                raise HardAutomationFailure(
                    "Missing required input parameter: "
                    f"{parameter.name}"
                )

            if inputs[parameter.name] is None:
                raise HardAutomationFailure(
                    "Required input parameter is null: "
                    f"{parameter.name}"
                )

        if parameter.name not in inputs:
            continue

        value = inputs[parameter.name]

        if parameter.type == ValueType.STRING:
            if not isinstance(value, str):
                raise HardAutomationFailure(
                    f"Input '{parameter.name}' must "
                    "be a string."
                )

        elif parameter.type == ValueType.INTEGER:
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
            ):
                raise HardAutomationFailure(
                    f"Input '{parameter.name}' must "
                    "be an integer."
                )

        elif parameter.type == ValueType.NUMBER:
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
            ):
                raise HardAutomationFailure(
                    f"Input '{parameter.name}' must "
                    "be a number."
                )

        elif parameter.type == ValueType.BOOLEAN:
            if not isinstance(value, bool):
                raise HardAutomationFailure(
                    f"Input '{parameter.name}' must "
                    "be a boolean."
                )


def resolve_parameter(
    value: str | None,
    inputs: dict[str, Any],
) -> Any:
    """
    Resolve artifact references such as ${name} using
    per-invocation inputs.

    Literal values are returned unchanged for values that
    are not parameterized.
    """

    if value is None:
        return None

    if (
        isinstance(value, str)
        and value.startswith("${")
        and value.endswith("}")
    ):
        parameter_name = value[2:-1]

        if parameter_name not in inputs:
            raise HardAutomationFailure(
                "Artifact references missing input "
                f"parameter '{parameter_name}'."
            )

        return inputs[parameter_name]

    return value


def replay_value_for_action(
    action,
    input_ref: str | None,
    inputs: dict[str, Any],
) -> Any:
    """
    Determine the value used by a deterministic replay action.

    Parameterized actions consume the invocation input explicitly
    referenced by the saved artifact step. Literal action values
    remain supported for non-parameterized actions.
    """

    if input_ref is not None:
        if action.action not in {
            ActionType.FILL,
            ActionType.SELECT,
        }:
            raise HardAutomationFailure(
                "Artifact input_ref is attached to an "
                f"unsupported action type: {action.action.value}."
            )

        if input_ref not in inputs:
            raise HardAutomationFailure(
                "Artifact references missing invocation "
                f"input '{input_ref}'."
            )

        return inputs[input_ref]

    return resolve_parameter(
        action.value,
        inputs,
    )


def execute_replay_action(
    surface: PlaywrightSurface,
    guardrails: Guardrails,
    action,
    target: SemanticTarget | None,
    input_ref: str | None,
    inputs: dict[str, Any],
):
    """
    Replay one recorded action deterministically.
    """

    decision = guardrails.check(
        action,
        surface.page.url,
        target,
    )

    print(
        f"GUARDRAIL: allowed={decision.allowed} "
        f"reason={decision.reason}"
    )

    if not decision.allowed:
        raise HardAutomationFailure(
            decision.reason
        )

    if action.action == ActionType.WAIT:
        surface.page.wait_for_timeout(1000)
        return None

    if target is None:
        raise HardAutomationFailure(
            "Recorded action requires a semantic target."
        )

    print(
        f"Resolving semantic target: "
        f"role={target.role!r}, "
        f"name={target.name!r}"
    )

    try:
        if action.action == ActionType.CLICK:
            surface.click_semantic(
                target.role,
                target.name,
            )

        elif action.action == ActionType.FILL:
            value = replay_value_for_action(
                action,
                input_ref,
                inputs,
            )

            if value is None:
                raise HardAutomationFailure(
                    "Recorded FILL action has no value."
                )

            surface.fill_semantic(
                target.role,
                target.name,
                str(value),
            )

        elif action.action == ActionType.SELECT:
            value = replay_value_for_action(
                action,
                input_ref,
                inputs,
            )

            if value is None:
                raise HardAutomationFailure(
                    "Recorded SELECT action has no value."
                )

            surface.select_semantic(
                target.role,
                target.name,
                str(value),
            )

        elif action.action == ActionType.EXTRACT:
            return surface.extract_semantic(
                target.role,
                target.name,
            )

        else:
            raise HardAutomationFailure(
                f"Unsupported replay action: "
                f"{action.action}"
            )

    except HardAutomationFailure:
        raise

    except ValueError as error:
        raise RecoverableAutomationError(
            str(error)
        ) from error

    return None


def get_field_value(
    surface: PlaywrightSurface,
    target: SemanticTarget,
) -> str:
    """
    Read the current value of a semantic form control.

    Checkpoint verification deliberately observes the live
    UI instead of assuming that a successful fill call means
    the desired state was reached.
    """

    observation = surface.observe()

    matches = [
        element
        for element in observation.elements
        if (
            normalize_semantic_text(element.role)
            == normalize_semantic_text(target.role)
            and normalize_semantic_text(element.name)
            == normalize_semantic_text(target.name)
        )
    ]

    if len(matches) == 0:
        raise RecoverableAutomationError(
            "Checkpoint target was not found: "
            f"role={target.role!r}, "
            f"name={target.name!r}."
        )

    if len(matches) > 1:
        raise RecoverableAutomationError(
            "Checkpoint target is ambiguous: "
            f"role={target.role!r}, "
            f"name={target.name!r}."
        )

    value = matches[0].value

    if value is None:
        return ""

    return str(value)


def verify_checkpoints(
    artifact: CapabilityArtifact,
    surface: PlaywrightSurface,
    inputs: dict[str, Any],
) -> None:
    """
    Verify the artifact's explicit success conditions against
    the live application state.
    """

    if not artifact.success_checkpoints:
        raise HardAutomationFailure(
            "Artifact declares no success checkpoints."
        )

    print("\n" + "=" * 70)
    print("VERIFYING SUCCESS CHECKPOINTS")
    print("=" * 70)

    for index, checkpoint in enumerate(
        artifact.success_checkpoints,
        start=1,
    ):
        print(
            f"\nCheckpoint {index}: "
            f"{checkpoint.description}"
        )

        if checkpoint.type == CheckpointType.URL_PATH:
            if checkpoint.expected_path is None:
                raise HardAutomationFailure(
                    "URL checkpoint has no expected path."
                )

            _, observed_path = normalize_url(
                surface.page.url
            )

            if observed_path != checkpoint.expected_path:
                raise RecoverableAutomationError(
                    "Success checkpoint failed. "
                    f"Expected URL path "
                    f"{checkpoint.expected_path!r}, "
                    f"observed {observed_path!r}."
                )

            print(
                "PASS: URL path = "
                f"{observed_path!r}"
            )

        elif (
            checkpoint.type
            == CheckpointType.FIELD_VALUE
        ):
            if checkpoint.target is None:
                raise HardAutomationFailure(
                    "Field checkpoint has no target."
                )

            expected_value = resolve_parameter(
                checkpoint.expected_value,
                inputs,
            )

            if expected_value is None:
                raise HardAutomationFailure(
                    "Field checkpoint has no "
                    "expected value."
                )

            observed_value = get_field_value(
                surface,
                checkpoint.target,
            )

            if observed_value != str(expected_value):
                raise RecoverableAutomationError(
                    "Success checkpoint failed for "
                    f"role={checkpoint.target.role!r}, "
                    f"name={checkpoint.target.name!r}. "
                    "Observed value did not match "
                    "the expected invocation value."
                )

            print(
                "PASS: "
                f"{checkpoint.target.name!r} "
                "contains the expected value."
            )

        else:
            raise HardAutomationFailure(
                "Unsupported checkpoint type: "
                f"{checkpoint.type}"
            )


def collect_outputs(
    artifact: CapabilityArtifact,
    extracted_outputs: dict[str, Any],
) -> dict[str, Any]:
    """
    Return only outputs declared by the capability contract.
    """

    result: dict[str, Any] = {}

    for output in artifact.output_schema:
        if output.name not in extracted_outputs:
            if output.required:
                raise HardAutomationFailure(
                    "Required declared output was not "
                    f"produced: {output.name}"
                )

            continue

        result[output.name] = (
            extracted_outputs[output.name]
        )

    return result


def print_result(
    result: ReplayResult,
) -> None:
    print("\n" + "=" * 70)
    print("STRUCTURED REPLAY RESULT")
    print("=" * 70)
    print(result.model_dump_json(indent=2))


def main():
    args = parse_arguments()

    logger = RunLogger("replay")

    print(f"Replay log: {logger.path}")

    inputs = {
        "name": args.name,
        "email": args.email,
        "phone": args.phone,
        "message": args.message,
    }

    print("=" * 70)
    print("DETERMINISTIC SEMANTIC REPLAY")
    print("=" * 70)

    try:
        artifact = load_artifact()

        validate_inputs(
            artifact,
            inputs,
        )

        logger.log(
            "run_started",
            data={
                "capability_name": artifact.capability_name,
                "artifact_version": artifact.version,
                "artifact_path": str(ARTIFACT_PATH),
                "target_url": TARGET_URL,
                "input_names": sorted(inputs.keys()),
            },
        )

    except HardAutomationFailure as error:
        logger.log(
            "run_failed",
            data={
                "error_category": error.category.value,
                "reason": error.reason,
                "stage": "artifact_or_input_validation",
            },
        )
        result = ReplayResult(
            status=ReplayStatus.FAILURE,
            capability_name="unknown",
            artifact_version="unknown",
            error_category=error.category,
            reason=error.reason,
        )

        print_result(result)
        return

    print(
        f"Capability: {artifact.capability_name}"
    )
    print(f"Version: {artifact.version}")
    print(f"Goal: {artifact.goal}")
    print(
        f"Recorded steps: {len(artifact.steps)}"
    )

    replay_screenshot_dir = Path(
        "evidence/replay"
    )

    replay_screenshot_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    guardrails = Guardrails(
        allowed_domains={
            "parabank.parasoft.com"
        },

        allowed_routes={
            "/parabank/index.htm",
            "/parabank/contact.htm",
        },
        allowed_actions={
            ActionType.CLICK,
            ActionType.FILL,
            ActionType.SELECT,
            ActionType.EXTRACT,
            ActionType.WAIT,
        },
        blocked_targets={
            ("button", "Send to Customer Care"),
        },
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False
        )

        page = browser.new_page(
            viewport={
                "width": 1280,
                "height": 800,
            }
        )

        print(f"\nOpening: {TARGET_URL}")

        try:
            page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=30_000,
            )

        except Exception as error:
            failure = HardAutomationFailure(
                f"Failed to load target application: "
                f"{error}"
            )

            logger.log(
                "run_failed",
                data={
                    "error_category": failure.category.value,
                    "reason": failure.reason,
                    "stage": "target_application_load",
                },
            )

            result = ReplayResult(
                status=ReplayStatus.FAILURE,
                capability_name=(
                    artifact.capability_name
                ),
                artifact_version=artifact.version,
                error_category=failure.category,
                reason=failure.reason,
            )

            print_result(result)
            browser.close()
            return

        surface = PlaywrightSurface(page)

        replay_completed = True
        failed_result: ReplayResult | None = None

        extracted_outputs: dict[str, Any] = {}

        for step in artifact.steps:
            print("\n" + "=" * 70)
            print(
                f"REPLAY STEP {step.step_number}"
            )
            print("=" * 70)

            current_url = page.url

            logger.log(
                "step_started",
                step_number=step.step_number,
                data={
                    "action": step.action.action.value,
                    "target_role": (
                        step.target.role
                        if step.target is not None
                        else None
                    ),
                    "target_name": (
                        step.target.name
                        if step.target is not None
                        else None
                    ),
                    "input_ref": step.input_ref,
                    "current_url": current_url,
                },
            )

            if normalize_url(
                current_url
            ) != normalize_url(
                step.url_before
            ):
                error = RecoverableAutomationError(
                    "Replay URL precondition changed. "
                    f"Expected route "
                    f"{normalize_url(step.url_before)}, "
                    f"but found "
                    f"{normalize_url(current_url)}."
                )

                handoff = create_handoff(
                    category=error.category,
                    reason=error.reason,
                    step_number=step.step_number,
                    current_url=current_url,
                    last_action=str(step.action),
                )

                print("\nHUMAN HANDOFF")
                print("-" * 70)
                print(
                    handoff.model_dump_json(
                        indent=2
                    )
                )

                failed_result = ReplayResult(
                    status=ReplayStatus.FAILURE,
                    capability_name=(
                        artifact.capability_name
                    ),
                    artifact_version=artifact.version,
                    error_category=error.category,
                    failed_step=step.step_number,
                    reason=error.reason,
                    expected=str(
                        normalize_url(
                            step.url_before
                        )
                    ),
                    observed=str(
                        normalize_url(
                            current_url
                        )
                    ),
                )

                logger.log(
                    "run_failed",
                    step_number=step.step_number,
                    data={
                        "error_category": error.category.value,
                        "reason": error.reason,
                        "stage": "url_precondition",
                        "expected": str(
                            normalize_url(step.url_before)
                        ),
                        "observed": str(
                            normalize_url(current_url)
                        ),
                    },
                )

                logger.log(
                    "run_failed",
                    step_number=step.step_number,
                    data={
                        "error_category": error.category.value,
                        "reason": error.reason,
                        "stage": "action_execution",
                    },
                )

                replay_completed = False
                break

            print("\nRECORDED ACTION")
            print("-" * 70)
            print(
                f"action={step.action.action.value!r}"
            )

            print("\nRECORDED SEMANTIC TARGET")
            print("-" * 70)
            print(step.target)

            try:
                extracted_value = (
                    execute_replay_action(
                        surface=surface,
                        guardrails=guardrails,
                        action=step.action,
                        target=step.target,
                        input_ref=step.input_ref,
                        inputs=inputs,
                    )
                )

                if (
                    step.action.action
                    == ActionType.EXTRACT
                    and step.action.output_name
                    is not None
                ):
                    extracted_outputs[
                        step.action.output_name
                    ] = extracted_value

            except (
                RecoverableAutomationError,
                HardAutomationFailure,
            ) as error:
                handoff = create_handoff(
                    category=error.category,
                    reason=error.reason,
                    step_number=step.step_number,
                    current_url=page.url,
                    last_action=str(step.action),
                )

                print("\nHUMAN HANDOFF")
                print("-" * 70)
                print(
                    handoff.model_dump_json(
                        indent=2
                    )
                )

                failed_result = ReplayResult(
                    status=ReplayStatus.FAILURE,
                    capability_name=(
                        artifact.capability_name
                    ),
                    artifact_version=artifact.version,
                    error_category=error.category,
                    failed_step=step.step_number,
                    reason=error.reason,
                )

                replay_completed = False
                break

            page.wait_for_timeout(500)

            page.screenshot(
                path=(
                    replay_screenshot_dir
                    / (
                        "semantic_replay_step_"
                        f"{step.step_number}.png"
                    )
                )
            )

            print(
                "\nRecorded semantic action "
                "replayed successfully."
            )

            logger.log(
                "step_completed",
                step_number=step.step_number,
                data={
                    "action": step.action.action.value,
                    "current_url": page.url,
                },
            )

        if replay_completed:
            try:
                verify_checkpoints(
                    artifact,
                    surface,
                    inputs,
                )

                outputs = collect_outputs(
                    artifact,
                    extracted_outputs,
                )

                result = ReplayResult(
                    status=ReplayStatus.SUCCESS,
                    capability_name=(
                        artifact.capability_name
                    ),
                    artifact_version=artifact.version,
                    outputs=outputs,
                    reason=(
                        "All recorded actions and "
                        "success checkpoints completed."
                    ),
                )

                print("\n" + "=" * 70)
                print("REPLAY COMPLETE")
                print("=" * 70)

                print(
                    f"Final title: {page.title()}"
                )

                print(
                    f"Final URL:   {page.url}"
                )

                page.screenshot(
                    path=(
                        replay_screenshot_dir
                        / (
                            "semantic_replay_"
                            "complete.png"
                        )
                    )
                )

                print_result(result)

                logger.log(
                    "run_completed",
                    data={
                        "status": result.status.value,
                        "capability_name": artifact.capability_name,
                        "artifact_version": artifact.version,
                        "checkpoint_count": len(
                            artifact.success_checkpoints
                        ),
                        "output_names": sorted(
                            result.outputs.keys()
                        ),
                        "final_url": page.url,
                    },
                )

            except (
                RecoverableAutomationError,
                HardAutomationFailure,
            ) as error:
                page.screenshot(
                    path=(
                        replay_screenshot_dir
                        / (
                            "checkpoint_failure.png"
                        )
                    )
                )

                handoff = create_handoff(
                    category=error.category,
                    reason=error.reason,
                    current_url=page.url,
                    last_action=(
                        "verify_success_checkpoints"
                    ),
                )

                print("\nHUMAN HANDOFF")
                print("-" * 70)
                print(
                    handoff.model_dump_json(
                        indent=2
                    )
                )

                result = ReplayResult(
                    status=ReplayStatus.FAILURE,
                    capability_name=(
                        artifact.capability_name
                    ),
                    artifact_version=artifact.version,
                    error_category=error.category,
                    reason=error.reason,
                )

                print_result(result)

                logger.log(
                    "run_failed",
                    data={
                        "error_category": error.category.value,
                        "reason": error.reason,
                        "stage": "success_checkpoints",
                        "current_url": page.url,
                    },
                )

        elif failed_result is not None:
            print_result(failed_result)

        input(
            "\nPress Enter to close browser..."
        )

        browser.close()


if __name__ == "__main__":
    main()