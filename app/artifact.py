from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.agent import AgentAction


ARTIFACT_VERSION = "1.1"


class SemanticTarget(BaseModel):
    """
    Stable semantic description of a UI control.

    Discovery may use temporary numeric IDs, but replay uses
    this semantic target instead.
    """

    role: str
    name: str


class ArtifactStep(BaseModel):
    """
    One successfully executed step captured during discovery.
    """

    step_number: int = Field(ge=1)
    url_before: str
    action: AgentAction
    target: SemanticTarget | None = None
    extracted_value: str | None = None


class CapabilityArtifact(BaseModel):
    """
    Versioned capability artifact produced by a successful discovery run.

    Replay consumes this artifact without calling the LLM.
    """

    version: str = ARTIFACT_VERSION
    capability_name: str
    goal: str
    target_domain: str

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    steps: list[ArtifactStep] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)

    def add_step(
        self,
        step_number: int,
        url_before: str,
        action: AgentAction,
        target: SemanticTarget | None = None,
        extracted_value: str | None = None,
    ) -> None:
        self.steps.append(
            ArtifactStep(
                step_number=step_number,
                url_before=url_before,
                action=action,
                target=target,
                extracted_value=extracted_value,
            )
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as file:
            file.write(
                self.model_dump_json(indent=2)
            )