import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.agent import ActionType, AgentAction, decide_action
from app.artifact import (
    CapabilityArtifact,
    CheckpointType,
    SemanticTarget,
    ValueType,
)
from app.escalation import (
    ErrorCategory,
    create_handoff,
)
from app.guardrails import Guardrails
from app.logger import RunLogger
from app.surface import Observation, PlaywrightSurface


TARGET_URL = "https://parabank.parasoft.com/parabank/index.htm"
MAX_STEPS = 8
MAX_ATTEMPTS = 16
MAX_HANDOFFS = 2


def get_semantic_target(
    observation: Observation,
    action: AgentAction,
) -> SemanticTarget | None:
    """
    Convert the temporary element ID selected by the discovery
    agent into stable semantic target information.
    """

    if action.target_id is None:
        return None

    for element in observation.elements:
        if element.id == action.target_id:
            return SemanticTarget(
                role=element.role,
                name=element.name,
            )

    raise ValueError(
        f"Agent selected element {action.target_id}, "
        "but it does not exist in the current observation."
    )


def execute_action(
    surface: PlaywrightSurface,
    guardrails: Guardrails,
    action: AgentAction,
    logger: RunLogger | None = None,
    step_number: int | None = None,
):
    target = None

    if action.target_id is not None:
        element = surface.get_element(action.target_id)

        target = SemanticTarget(
            role=element.role,
            name=element.name,
        )

    decision = guardrails.check(
        action,
        surface.page.url,
        target,
    )

    if logger is not None:
        logger.log(
            "guardrail_decision",
            step_number=step_number,
            data={
                "action": action.action.value,
                "target_role": (
                    target.role if target else None
                ),
                "target_name": (
                    target.name if target else None
                ),
                "allowed": decision.allowed,
                "reason": decision.reason,
            },
        )

    print(
        f"GUARDRAIL: allowed={decision.allowed} "
        f"reason={decision.reason}"
    )

    if not decision.allowed:
        raise PermissionError(decision.reason)

    if action.action == ActionType.FILL:
        if action.target_id is None or action.value is None:
            raise ValueError(
                "FILL requires target_id and value."
            )

        surface.fill(
            action.target_id,
            action.value,
        )

    elif action.action == ActionType.CLICK:
        if action.target_id is None:
            raise ValueError(
                "CLICK requires target_id."
            )

        surface.click(action.target_id)

    elif action.action == ActionType.SELECT:
        if action.target_id is None or action.value is None:
            raise ValueError(
                "SELECT requires target_id and value."
            )

        surface.select_option(
            action.target_id,
            action.value,
        )

    elif action.action == ActionType.EXTRACT:
        if action.target_id is None:
            raise ValueError(
                "EXTRACT requires target_id."
            )

        return surface.extract_text(
            action.target_id
        )

    elif action.action == ActionType.WAIT:
        surface.page.wait_for_timeout(1000)

    else:
        raise ValueError(
            f"Unsupported discovery action: "
            f"{action.action}"
        )

    return None


def get_input_ref(
    action: AgentAction,
    target: SemanticTarget | None,
) -> str | None:
    """
    Connect a discovered form action to the capability's
    typed invocation input.

    The live discovery action may contain an example value,
    but the reusable artifact stores only the input reference.
    """

    if (
        action.action != ActionType.FILL
        or target is None
    ):
        return None

    if target.role.casefold() != "textbox":
        return None

    input_mapping = {
        "name": "name",
        "email": "email",
        "phone": "phone",
        "message": "message",
    }

    normalized_name = target.name.strip().casefold()

    return input_mapping.get(normalized_name)


def sanitize_action_for_artifact(
    action: AgentAction,
    input_ref: str | None,
) -> AgentAction:
    """
    Create the reusable recorded action.

    When an action consumes an invocation input, do not
    persist the concrete discovery value.
    """

    if input_ref is None:
        return action.model_copy(deep=True)

    return action.model_copy(
        update={
            "value": None,
        },
        deep=True,
    )


def perform_discovery_handoff(
    *,
    page,
    logger: RunLogger,
    screenshot_dir: Path,
    step_number: int,
    reason: str,
    last_action: str | None,
) -> None:
    """
    Pause discovery and transfer the same live browser session
    to a human operator.

    The human can modify the current UI directly. After the
    operator signals completion, control returns to automation.
    The caller then re-observes the live page before asking the
    LLM for another decision.
    """

    handoff = create_handoff(
        category=ErrorCategory.RECOVERABLE_ERROR,
        reason=reason,
        step_number=step_number,
        current_url=page.url,
        last_action=last_action,
    )

    before_path = (
        screenshot_dir
        / f"discovery_handoff_step_{step_number}_before.png"
    )

    after_path = (
        screenshot_dir
        / f"discovery_handoff_step_{step_number}_after.png"
    )

    page.screenshot(path=before_path)

    logger.log(
        "handoff_requested",
        step_number=step_number,
        data={
            "category": ErrorCategory.RECOVERABLE_ERROR.value,
            "reason": reason,
            "current_url": page.url,
            "before_screenshot": str(before_path),
        },
    )

    logger.log(
        "control_transferred_to_human",
        step_number=step_number,
        data={
            "control_owner": "human",
            "current_url": page.url,
        },
    )

    print("\n" + "=" * 70)
    print("HUMAN CONTROL — SAME LIVE BROWSER SESSION")
    print("=" * 70)

    print(
        handoff.model_dump_json(
            indent=2
        )
    )

    print(
        "\nAutomation is paused."
        "\nUse the currently open browser window to perform "
        "the required recovery."
    )

    input(
        "\nWhen the browser is ready for automation to continue, "
        "press Enter here..."
    )

    page.screenshot(path=after_path)

    logger.log(
        "human_action_recorded",
        step_number=step_number,
        data={
            "after_screenshot": str(after_path),
            "current_url": page.url,
        },
    )

    logger.log(
        "control_returned_to_automation",
        step_number=step_number,
        data={
            "control_owner": "automation",
            "current_url": page.url,
        },
    )

    print("\nControl returned to automation.")
    print(
        "Discovery will re-observe the current live UI "
        "before making another decision."
    )


capability_goal = (
    "Navigate to the Contact Us page and fill out the "
    "Customer Care form using the supplied name, email, "
    "phone, and message inputs. Do not submit the form. "
    "Complete when all four fields have been filled."
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run LLM-driven capability discovery."
    )

    parser.add_argument(
        "--target-url",
        default=TARGET_URL,
        help=(
            "Target application URL for discovery. "
            "Defaults to the ParaBank demo application."
        ),
    )

    parser.add_argument(
        "--goal",
        default=None,
        help=(
            "Natural-language goal for the discovery agent. "
            "If omitted, the ParaBank Customer Care demo goal is used."
        ),
    )

    parser.add_argument(
        "--demo-handoff-step",
        type=int,
        default=None,
        help=(
            "Optional evidence/demo mode. Before this discovery step, "
            "transfer control of the same live browser session to a "
            "human operator, then re-observe and continue."
        ),
    )

    return parser.parse_args()


def main():
    args = parse_args()

    evidence_dir = Path("evidence")
    screenshot_dir = evidence_dir / "screenshots"
    artifact_dir = evidence_dir / "artifacts"

    screenshot_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    artifact_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    default_goal = (
        "Navigate to the Contact Us page and fill out the Customer Care form "
        "using Name 'Demo User', Email 'demo@example.com', Phone '555-0100', "
        "and Message 'Automated test message'. "
        "Do not submit the form. "
        "Complete when all four fields have been filled."
    )

    goal = args.goal or default_goal
    target_url = args.target_url

    artifact = CapabilityArtifact(
        capability_name="fill_customer_care_form",
        goal=capability_goal,
        target_domain="parabank.parasoft.com",
    )

    # --------------------------------------------------------------
    # Capability contract: typed inputs
    # --------------------------------------------------------------

    artifact.add_input(
        name="name",
        value_type=ValueType.STRING,
        description="Name to enter in the Customer Care form.",
    )

    artifact.add_input(
        name="email",
        value_type=ValueType.STRING,
        description="Email address to enter in the Customer Care form.",
    )

    artifact.add_input(
        name="phone",
        value_type=ValueType.STRING,
        description="Phone number to enter in the Customer Care form.",
    )

    artifact.add_input(
        name="message",
        value_type=ValueType.STRING,
        description="Message to enter in the Customer Care form.",
    )

    # --------------------------------------------------------------
    # Capability contract: typed outputs
    # --------------------------------------------------------------
    #
    # This capability intentionally does not submit the form and does
    # not extract a business value. Therefore output_schema remains
    # empty. Other capabilities can declare typed outputs with
    # artifact.add_output(...).
    # --------------------------------------------------------------

    # --------------------------------------------------------------
    # Capability contract: deterministic success checkpoints
    # --------------------------------------------------------------

    artifact.add_checkpoint(
        CheckpointType.URL_PATH,
        expected_path="/parabank/contact.htm",
        description=(
            "Replay must finish on the Customer Care page."
        ),
    )

    artifact.add_checkpoint(
        CheckpointType.FIELD_VALUE,
        target=SemanticTarget(
            role="textbox",
            name="name",
        ),
        expected_value="${name}",
        description=(
            "Name field must contain the supplied name."
        ),
    )

    artifact.add_checkpoint(
        CheckpointType.FIELD_VALUE,
        target=SemanticTarget(
            role="textbox",
            name="email",
        ),
        expected_value="${email}",
        description=(
            "Email field must contain the supplied email."
        ),
    )

    artifact.add_checkpoint(
        CheckpointType.FIELD_VALUE,
        target=SemanticTarget(
            role="textbox",
            name="phone",
        ),
        expected_value="${phone}",
        description=(
            "Phone field must contain the supplied phone number."
        ),
    )

    artifact.add_checkpoint(
        CheckpointType.FIELD_VALUE,
        target=SemanticTarget(
            role="textbox",
            name="message",
        ),
        expected_value="${message}",
        description=(
            "Message field must contain the supplied message."
        ),
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

        print(f"Opening: {target_url}")

        page.goto(
            target_url,
            wait_until="domcontentloaded",
            timeout=30_000,
        )

        surface = PlaywrightSurface(page)

        guardrails = Guardrails(
            allowed_domains={
                "parabank.parasoft.com"
            },
            allowed_routes={
                "/parabank/index.htm",
                "/parabank/contact.htm",
            },
            allowed_actions={
                ActionType.FILL,
                ActionType.CLICK,
                ActionType.SELECT,
                ActionType.EXTRACT,
                ActionType.WAIT,
            },
            blocked_targets={
                ("button", "Send to Customer Care"),
            },
        )

        logger = RunLogger(
            phase="discovery",
        )

        logger.log(
            "run_started",
            data={
                "capability_name": artifact.capability_name,
                "target_url": target_url,
                "max_steps": MAX_STEPS,
                "max_attempts": MAX_ATTEMPTS,
                "max_handoffs": MAX_HANDOFFS,
            },
        )

        print(f"Run log: {logger.path}")

        print("\n" + "=" * 70)
        print("DISCOVERY GOAL")
        print("=" * 70)
        print(goal)

        completed = False
        hard_failure = False
        demo_handoff_used = False

        step_number = 1
        attempt_count = 0
        handoff_count = 0
        stop_reason = None

        while step_number <= MAX_STEPS:
            attempt_count += 1

            if attempt_count > MAX_ATTEMPTS:
                stop_reason = (
                    f"Discovery exceeded the maximum attempt limit "
                    f"of {MAX_ATTEMPTS} without completing the goal."
                )

                logger.log(
                    "attempt_limit_reached",
                    step_number=step_number,
                    data={
                        "attempt_count": attempt_count,
                        "max_attempts": MAX_ATTEMPTS,
                        "reason": stop_reason,
                    },
                )

                print(f"\nSTOPPING: {stop_reason}")
                break

            print("\n" + "=" * 70)
            print(
                f"DISCOVERY STEP {step_number}"
            )
            print("=" * 70)

            if (
                args.demo_handoff_step == step_number
                and not demo_handoff_used
            ):
                perform_discovery_handoff(
                    page=page,
                    logger=logger,
                    screenshot_dir=screenshot_dir,
                    step_number=step_number,
                    reason=(
                        "Controlled discovery handoff requested "
                        "for evidence/demo testing."
                    ),
                    last_action=None,
                )

                demo_handoff_used = True

                logger.log(
                    "automation_resumed",
                    step_number=step_number,
                    data={
                        "reobserve_required": True,
                        "demo_handoff": True,
                    },
                )

                print(
                    "\nAutomation is re-observing the UI "
                    "after human control."
                )

            observation = surface.observe()

            formatted_observation = (
                surface.format_observation(
                    observation
                )
            )

            print("\nOBSERVATION")
            print("-" * 70)
            print(formatted_observation)

            page.screenshot(
                path=(
                    screenshot_dir
                    / f"discovery_step_{step_number}.png"
                )
            )

            action = decide_action(
                goal=goal,
                observation=formatted_observation,
            )

            logger.log(
                "action_selected",
                step_number=step_number,
                data={
                    "action": action.action.value,
                    "target_id": action.target_id,
                    "output_name": action.output_name,
                    "reason": action.reason,
                },
            )

            print("\nLLM DECISION")
            print("-" * 70)
            print(action)

            if action.action == ActionType.COMPLETE:
                logger.log(
                    "run_completed",
                    step_number=step_number,
                    data={
                        "reason": action.reason,
                    },
                )

                print(
                    "\nAgent reports that the goal "
                    "has been completed."
                )

                completed = True
                break

            if action.action == ActionType.ESCALATE:
                handoff_count += 1

                if handoff_count > MAX_HANDOFFS:
                    stop_reason = (
                        f"Discovery exceeded the maximum human handoff "
                        f"limit of {MAX_HANDOFFS}."
                    )

                    logger.log(
                        "handoff_limit_reached",
                        step_number=step_number,
                        data={
                            "handoff_count": handoff_count,
                            "max_handoffs": MAX_HANDOFFS,
                            "reason": stop_reason,
                        },
                    )

                    print(f"\nSTOPPING: {stop_reason}")
                    break

                perform_discovery_handoff(
                    page=page,
                    logger=logger,
                    screenshot_dir=screenshot_dir,
                    step_number=step_number,
                    reason=action.reason,
                    last_action=str(action),
                )

                logger.log(
                    "automation_resumed",
                    step_number=step_number,
                    data={
                        "reobserve_required": True,
                    },
                )

                # Do not increment the capability step.
                # Re-observe the same live browser after human recovery.
                continue

            # Resolve the temporary numeric ID into semantic
            # information BEFORE changing the page.
            semantic_target = get_semantic_target(
                observation=observation,
                action=action,
            )

            if semantic_target is not None:
                print(
                    "\nSEMANTIC TARGET"
                )
                print("-" * 70)
                print(
                    f"role={semantic_target.role!r} "
                    f"name={semantic_target.name!r}"
                )

            url_before = page.url

            try:
                extracted_value = execute_action(
                    surface=surface,
                    guardrails=guardrails,
                    action=action,
                    logger=logger,
                    step_number=step_number,
                )

                logger.log(
                    "action_succeeded",
                    step_number=step_number,
                    data={
                        "action": action.action.value,
                    },
                )

            except PermissionError as error:
                logger.log(
                    "action_failed",
                    step_number=step_number,
                    data={
                        "action": action.action.value,
                        "error_type": type(error).__name__,
                        "category": ErrorCategory.HARD_FAILURE.value,
                        "error": str(error),
                    },
                )

                handoff = create_handoff(
                    category=ErrorCategory.HARD_FAILURE,
                    reason=str(error),
                    step_number=step_number,
                    current_url=page.url,
                    last_action=str(action),
                )

                logger.log(
                    "hard_failure",
                    step_number=step_number,
                    data={
                        "category": ErrorCategory.HARD_FAILURE.value,
                        "reason": str(error),
                    },
                )

                print("\nHARD FAILURE")
                print("-" * 70)
                print(
                    handoff.model_dump_json(
                        indent=2
                    )
                )

                print(
                    "\nAutomation will not transfer control "
                    "to bypass a guardrail."
                )

                hard_failure = True
                break

            except Exception as error:
                logger.log(
                    "action_failed",
                    step_number=step_number,
                    data={
                        "action": action.action.value,
                        "error_type": type(error).__name__,
                        "category": (
                            ErrorCategory
                            .RECOVERABLE_ERROR
                            .value
                        ),
                        "error": str(error),
                    },
                )

                handoff_count += 1

                if handoff_count > MAX_HANDOFFS:
                    stop_reason = (
                        f"Discovery exceeded the maximum human handoff "
                        f"limit of {MAX_HANDOFFS}."
                    )

                    logger.log(
                        "handoff_limit_reached",
                        step_number=step_number,
                        data={
                            "handoff_count": handoff_count,
                            "max_handoffs": MAX_HANDOFFS,
                            "reason": stop_reason,
                        },
                    )

                    print(f"\nSTOPPING: {stop_reason}")
                    break

                perform_discovery_handoff(
                    page=page,
                    logger=logger,
                    screenshot_dir=screenshot_dir,
                    step_number=step_number,
                    reason=str(error),
                    last_action=str(action),
                )

                logger.log(
                    "automation_resumed",
                    step_number=step_number,
                    data={
                        "reobserve_required": True,
                    },
                )

                # The failed action is not recorded in the artifact.
                # Re-observe after the human recovery.
                continue

            input_ref = get_input_ref(
                action=action,
                target=semantic_target,
            )

            recorded_action = sanitize_action_for_artifact(
                action=action,
                input_ref=input_ref,
            )

            artifact.add_step(
                step_number=step_number,
                url_before=url_before,
                action=recorded_action,
                target=semantic_target,
                input_ref=input_ref,
                extracted_value=extracted_value,
            )

            print(
                "\nAction executed and "
                "recorded successfully."
            )

            page.wait_for_timeout(500)

            step_number += 1

        if completed:
            artifact_path = (
                artifact_dir
                / "fill_customer_care_form.json"
            )

            artifact.save(
                str(artifact_path)
            )

            print("\n" + "=" * 70)
            print("DISCOVERY COMPLETE")
            print("=" * 70)

            print(
                f"Artifact saved to: "
                f"{artifact_path}"
            )

            page.screenshot(
                path=(
                    screenshot_dir
                    / "discovery_complete.png"
                )
            )

        else:
            if not hard_failure:
                reason = stop_reason or (
                    f"Discovery reached the maximum "
                    f"step limit of {MAX_STEPS} "
                    "without completing the goal."
                )

                handoff = create_handoff(
                    category=ErrorCategory.RECOVERABLE_ERROR,
                    reason=reason,
                    step_number=step_number,
                    current_url=page.url,
                    last_action=None,
                )

                logger.log(
                    "human_handoff",
                    step_number=step_number,
                    data={
                        "category": (
                            ErrorCategory
                            .RECOVERABLE_ERROR
                            .value
                        ),
                        "reason": reason,
                    },
                )

                print("\nHUMAN HANDOFF")
                print("-" * 70)
                print(
                    handoff.model_dump_json(
                        indent=2
                    )
                )

            print("\n" + "=" * 70)
            print("DISCOVERY DID NOT COMPLETE")
            print("=" * 70)

            print(
                "No reusable artifact was saved."
            )

        input(
            "\nPress Enter to close browser..."
        )

        browser.close()


if __name__ == "__main__":
    main()