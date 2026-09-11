from dataclasses import dataclass
from urllib.parse import urlparse

from app.agent import ActionType, AgentAction
from app.artifact import SemanticTarget


@dataclass
class GuardrailDecision:
    allowed: bool
    reason: str


class Guardrails:
    def __init__(
        self,
        allowed_domains: set[str],
        allowed_actions: set[ActionType],
        allowed_routes: set[str] | None = None,
        blocked_targets: set[tuple[str, str]] | None = None,
    ):
        self.allowed_domains = allowed_domains
        self.allowed_actions = allowed_actions
        self.allowed_routes = allowed_routes or set()
        self.blocked_targets = blocked_targets or set()

    def check(
        self,
        action: AgentAction,
        current_url: str,
        target: SemanticTarget | None = None,
    ) -> GuardrailDecision:
        if action.action in {
            ActionType.COMPLETE,
            ActionType.ESCALATE,
        }:
            return GuardrailDecision(
                allowed=True,
                reason=(
                    "Control action does not modify "
                    "the computer surface."
                ),
            )

        parsed = urlparse(current_url)
        domain = parsed.hostname

        if domain not in self.allowed_domains:
            return GuardrailDecision(
                allowed=False,
                reason=(
                    f"Domain '{domain}' "
                    "is not allowlisted."
                ),
            )

        route = parsed.path

        if ";jsessionid=" in route:
            route = route.split(
                ";jsessionid=",
                1,
            )[0]

        if (
            self.allowed_routes
            and route not in self.allowed_routes
        ):
            return GuardrailDecision(
                allowed=False,
                reason=(
                    f"Route '{route}' "
                    "is not allowlisted."
                ),
            )

        if action.action not in self.allowed_actions:
            return GuardrailDecision(
                allowed=False,
                reason=(
                    f"Action '{action.action.value}' "
                    "is not allowed."
                ),
            )

        if target is not None:
            target_key = (
                target.role,
                target.name,
            )

            if target_key in self.blocked_targets:
                return GuardrailDecision(
                    allowed=False,
                    reason=(
                        "Target is explicitly blocked: "
                        f'role="{target.role}", '
                        f'name="{target.name}".'
                    ),
                )

        return GuardrailDecision(
            allowed=True,
            reason="Action passed guardrail checks.",
        )