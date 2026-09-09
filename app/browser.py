from pathlib import Path

from playwright.sync_api import sync_playwright

from app.agent import ActionType, AgentAction, decide_action
from app.guardrails import Guardrails
from app.surface import PlaywrightSurface


TARGET_URL = "https://parabank.parasoft.com/parabank/index.htm"


def execute_action(
    surface: PlaywrightSurface,
    guardrails: Guardrails,
    action: AgentAction,
):
    """
    Check an agent action against policy before executing it.
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

    if action.action == ActionType.FILL:
        if action.target_id is None or action.value is None:
            raise ValueError("FILL requires target_id and value.")

        surface.fill(action.target_id, action.value)

    elif action.action == ActionType.CLICK:
        if action.target_id is None:
            raise ValueError("CLICK requires target_id.")

        surface.click(action.target_id)

    elif action.action == ActionType.SELECT:
        if action.target_id is None or action.value is None:
            raise ValueError("SELECT requires target_id and value.")

        surface.select_option(action.target_id, action.value)

    elif action.action == ActionType.EXTRACT:
        if action.target_id is None:
            raise ValueError("EXTRACT requires target_id.")

        return surface.extract_text(action.target_id)

    elif action.action == ActionType.WAIT:
        surface.page.wait_for_timeout(1000)

    elif action.action in {
        ActionType.COMPLETE,
        ActionType.ESCALATE,
    }:
        return None

    else:
        raise ValueError(
            f"Unsupported action: {action.action}"
        )


def main():
    evidence_dir = Path("evidence/screenshots")
    evidence_dir.mkdir(parents=True, exist_ok=True)

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

        # -----------------------------
        # Surface
        # -----------------------------
        surface = PlaywrightSurface(page)

        # -----------------------------
        # Guardrails
        # -----------------------------
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
        )

        # -----------------------------
        # OBSERVE
        # -----------------------------
        observation = surface.observe()

        formatted_observation = (
            surface.format_observation(observation)
        )

        print("\n" + "=" * 70)
        print("AGENT OBSERVATION")
        print("=" * 70)
        print(formatted_observation)

        # Save evidence of the initial state.
        page.screenshot(
            path=evidence_dir / "parabank_home.png"
        )

        # -----------------------------
        # GOAL
        # -----------------------------
        goal = "Click the Contact Us link."

        print("\n" + "=" * 70)
        print("GOAL")
        print("=" * 70)
        print(goal)

        # -----------------------------
        # DECIDE — LLM
        # -----------------------------
        action = decide_action(
            goal=goal,
            observation=formatted_observation,
        )

        print("\n" + "=" * 70)
        print("LLM DECISION")
        print("=" * 70)
        print(action)

        # -----------------------------
        # GUARDRAIL + ACT
        # -----------------------------
        execute_action(
            surface=surface,
            guardrails=guardrails,
            action=action,
        )

        print("\nAction executed successfully.")

        # -----------------------------
        # OBSERVE AGAIN
        # -----------------------------
        new_observation = surface.observe()

        print("\n" + "=" * 70)
        print("OBSERVATION AFTER ACTION")
        print("=" * 70)
        print(
            surface.format_observation(
                new_observation
            )
        )

        # Save evidence after the LLM action.
        page.screenshot(
            path=evidence_dir / "after_llm_action.png"
        )

        input("\nPress Enter to close browser...")

        browser.close()


if __name__ == "__main__":
    main()