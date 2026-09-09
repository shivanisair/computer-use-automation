from dataclasses import dataclass
from urllib.parse import urlparse

from app.agent import ActionType, AgentAction


@dataclass
class GuardrailDecision:
    allowed: bool
    reason: str


class Guardrails:
    """
    Policy layer between the discovery agent and the computer-use surface.

    Every browser-affecting AgentAction must pass this policy before
    execution.
    """

    def __init__(
        self,
        allowed_domains: set[str],
        allowed_actions: set[ActionType],
    ):
        self.allowed_domains = allowed_domains
        self.allowed_actions = allowed_actions

    def check(
        self,
        action: AgentAction,
        current_url: str,
    ) -> GuardrailDecision:

        # COMPLETE and ESCALATE do not interact with the browser.
        if action.action in {
            ActionType.COMPLETE,
            ActionType.ESCALATE,
        }:
            return GuardrailDecision(
                allowed=True,
                reason="Control action does not modify the computer surface.",
            )

        # Check current domain.
        parsed = urlparse(current_url)
        domain = parsed.hostname

        if domain not in self.allowed_domains:
            return GuardrailDecision(
                allowed=False,
                reason=f"Domain '{domain}' is not allowlisted.",
            )

        # Check action type.
        if action.action not in self.allowed_actions:
            return GuardrailDecision(
                allowed=False,
                reason=f"Action '{action.action.value}' is not allowed.",
            )

        return GuardrailDecision(
            allowed=True,
            reason="Action passed guardrail checks.",
        )


if __name__ == "__main__":
    guardrails = Guardrails(
        allowed_domains={"parabank.parasoft.com"},
        allowed_actions={
            ActionType.FILL,
            ActionType.CLICK,
            ActionType.SELECT,
            ActionType.EXTRACT,
            ActionType.WAIT,
        },
    )

    action = AgentAction(
        action=ActionType.CLICK,
        target_id=1,
        reason="Test an allowed click.",
    )

    result = guardrails.check(
        action,
        "https://parabank.parasoft.com/parabank/index.htm",
    )

    print(result)