from pathlib import Path

from playwright.sync_api import sync_playwright

from app.agent import ActionType, AgentAction, decide_action
from app.artifact import CapabilityArtifact, SemanticTarget
from app.guardrails import Guardrails
from app.surface import Observation, PlaywrightSurface


TARGET_URL = "https://parabank.parasoft.com/parabank/index.htm"
MAX_STEPS = 8


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


def main():
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

    goal = (
    "Navigate to the Contact Us page and fill out the Customer Care form "
    "using Name 'Demo User', Email 'demo@example.com', Phone '555-0100', "
    "and Message 'Automated test message'. "
    "Do not submit the form. "
    "Complete when all four fields have been filled."
)

    artifact = CapabilityArtifact(
        capability_name="fill_customer_care_form",
        goal=goal,
        target_domain="parabank.parasoft.com",
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

        print(f"Opening: {TARGET_URL}")

        page.goto(
            TARGET_URL,
            wait_until="domcontentloaded",
            timeout=30_000,
        )

        surface = PlaywrightSurface(page)

        guardrails = Guardrails(
            allowed_domains={
                "parabank.parasoft.com"
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

        print("\n" + "=" * 70)
        print("DISCOVERY GOAL")
        print("=" * 70)
        print(goal)

        completed = False

        for step_number in range(
            1,
            MAX_STEPS + 1,
        ):
            print("\n" + "=" * 70)
            print(
                f"DISCOVERY STEP {step_number}"
            )
            print("=" * 70)

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

            print("\nLLM DECISION")
            print("-" * 70)
            print(action)

            if action.action == ActionType.COMPLETE:
                print(
                    "\nAgent reports that the goal "
                    "has been completed."
                )
                completed = True
                break

            if action.action == ActionType.ESCALATE:
                print(
                    "\nAgent requested human escalation."
                )
                break

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
                )

            except Exception as error:
                print(
                    f"\nACTION FAILED: {error}"
                )
                break

            artifact.add_step(
                step_number=step_number,
                url_before=url_before,
                action=action,
                target=semantic_target,
                extracted_value=extracted_value,
            )

            print(
                "\nAction executed and "
                "recorded successfully."
            )

            page.wait_for_timeout(500)

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