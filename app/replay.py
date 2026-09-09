from pathlib import Path

from playwright.sync_api import sync_playwright

from app.agent import ActionType
from app.artifact import CapabilityArtifact, SemanticTarget
from app.guardrails import Guardrails
from app.surface import PlaywrightSurface


ARTIFACT_PATH = Path(
    "evidence/artifacts/navigate_to_contact_us.json"
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
    Replay a recorded action deterministically.

    Numeric discovery IDs are intentionally not used for
    element-targeted replay.
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
        raise PermissionError(decision.reason)

    if action.action == ActionType.WAIT:
        surface.page.wait_for_timeout(1000)
        return None

    if target is None:
        raise ValueError(
            "Recorded action requires a semantic target."
        )

    print(
        f"Resolving semantic target: "
        f"role={target.role!r}, "
        f"name={target.name!r}"
    )

    if action.action == ActionType.CLICK:
        surface.click_semantic(
            target.role,
            target.name,
        )

    elif action.action == ActionType.FILL:
        if action.value is None:
            raise ValueError(
                "Recorded FILL action has no value."
            )

        surface.fill_semantic(
            target.role,
            target.name,
            action.value,
        )

    elif action.action == ActionType.SELECT:
        if action.value is None:
            raise ValueError(
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
        raise ValueError(
            f"Unsupported replay action: "
            f"{action.action}"
        )

    return None


def load_artifact() -> CapabilityArtifact:
    if not ARTIFACT_PATH.exists():
        raise FileNotFoundError(
            f"Artifact not found: {ARTIFACT_PATH}"
        )

    artifact_json = ARTIFACT_PATH.read_text(
        encoding="utf-8"
    )

    artifact = (
        CapabilityArtifact.model_validate_json(
            artifact_json
        )
    )

    if artifact.version != "1.1":
        raise ValueError(
            "Unsupported artifact version: "
            f"{artifact.version}"
        )

    return artifact


def main():
    print("=" * 70)
    print("DETERMINISTIC SEMANTIC REPLAY")
    print("=" * 70)

    artifact = load_artifact()

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

        for step in artifact.steps:
            print("\n" + "=" * 70)
            print(
                f"REPLAY STEP {step.step_number}"
            )
            print("=" * 70)

            current_url = page.url

            if current_url != step.url_before:
                raise RuntimeError(
                    "Replay precondition failed.\n"
                    f"Expected URL: {step.url_before}\n"
                    f"Current URL:  {current_url}"
                )

            print("\nRECORDED ACTION")
            print("-" * 70)
            print(step.action)

            print("\nRECORDED SEMANTIC TARGET")
            print("-" * 70)
            print(step.target)

            execute_replay_action(
                surface=surface,
                guardrails=guardrails,
                action=step.action,
                target=step.target,
            )

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

        print("\n" + "=" * 70)
        print("REPLAY COMPLETE")
        print("=" * 70)

        print(f"Final title: {page.title()}")
        print(f"Final URL:   {page.url}")

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