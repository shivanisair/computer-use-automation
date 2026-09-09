from pathlib import Path

from playwright.sync_api import sync_playwright
from urllib.parse import urlparse

from app.agent import ActionType
from app.artifact import CapabilityArtifact, SemanticTarget
from app.escalation import (
    ErrorCategory,
    HardAutomationFailure,
    RecoverableAutomationError,
    create_handoff,
)
from app.guardrails import Guardrails
from app.surface import PlaywrightSurface


ARTIFACT_PATH = Path(
    "evidence/artifacts/fill_customer_care_form.json"
)

TARGET_URL = (
    "https://parabank.parasoft.com/parabank/index.htm"
)


def execute_replay_action(
    surface: PlaywrightSurface,
    guardrails: Guardrails,
    action,
    target: SemanticTarget | None,
):
    """
    Replay one recorded action deterministically.
    """

    decision = guardrails.check(
        action,
        surface.page.url,
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
            if action.value is None:
                raise HardAutomationFailure(
                    "Recorded FILL action has no value."
                )

            surface.fill_semantic(
                target.role,
                target.name,
                action.value,
            )

        elif action.action == ActionType.SELECT:
            if action.value is None:
                raise HardAutomationFailure(
                    "Recorded SELECT action has no value."
                )

            surface.select_semantic(
                target.role,
                target.name,
                action.value,
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
        # Missing or ambiguous semantic targets represent
        # UI drift that may be recoverable by a human.
        raise RecoverableAutomationError(
            str(error)
        ) from error

    return None


def load_artifact() -> CapabilityArtifact:
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

    if artifact.version != "1.1":
        raise HardAutomationFailure(
            "Unsupported artifact version: "
            f"{artifact.version}"
        )

    return artifact

def normalize_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)

    path = parsed.path

    if ";jsessionid=" in path:
        path = path.split(";jsessionid=", 1)[0]

    return parsed.hostname or "", path

def main():
    print("=" * 70)
    print("DETERMINISTIC SEMANTIC REPLAY")
    print("=" * 70)

    try:
        artifact = load_artifact()

    except HardAutomationFailure as error:
        handoff = create_handoff(
            category=error.category,
            reason=error.reason,
        )

        print("\nHARD FAILURE")
        print(handoff.model_dump_json(indent=2))
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
            artifact.target_domain
        },
        allowed_actions={
            ActionType.FILL,
            ActionType.CLICK,
            ActionType.SELECT,
            ActionType.EXTRACT,
            ActionType.WAIT,
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

        page.goto(
            TARGET_URL,
            wait_until="domcontentloaded",
            timeout=30_000,
        )

        surface = PlaywrightSurface(page)

        replay_completed = True

        for step in artifact.steps:
            print("\n" + "=" * 70)
            print(
                f"REPLAY STEP {step.step_number}"
            )
            print("=" * 70)

            current_url = page.url

            if normalize_url(current_url) != normalize_url(
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

                replay_completed = False
                break

            print("\nRECORDED ACTION")
            print("-" * 70)
            print(step.action)

            print("\nRECORDED SEMANTIC TARGET")
            print("-" * 70)
            print(step.target)

            try:
                execute_replay_action(
                    surface=surface,
                    guardrails=guardrails,
                    action=step.action,
                    target=step.target,
                )

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

                replay_completed = False
                break

            page.wait_for_timeout(500)

            page.screenshot(
                path=(
                    replay_screenshot_dir
                    / (
                        f"semantic_replay_step_"
                        f"{step.step_number}.png"
                    )
                )
            )

            print(
                "\nRecorded semantic action "
                "replayed successfully."
            )

        if replay_completed:
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
                    / "semantic_replay_complete.png"
                )
            )

        input(
            "\nPress Enter to close browser..."
        )

        browser.close()


if __name__ == "__main__":
    main()