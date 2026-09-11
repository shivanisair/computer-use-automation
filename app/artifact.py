from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.agent import AgentAction


ARTIFACT_VERSION = "1.3"


class ValueType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"


class SemanticTarget(BaseModel):
    role: str
    name: str


class InputParameter(BaseModel):
    name: str
    type: ValueType
    description: str
    required: bool = True


class OutputDefinition(BaseModel):
    name: str
    type: ValueType
    description: str
    required: bool = True


class CheckpointType(str, Enum):
    URL_PATH = "url_path"
    FIELD_VALUE = "field_value"


class SuccessCheckpoint(BaseModel):
    type: CheckpointType
    target: SemanticTarget | None = None
    expected_value: str | None = None
    expected_path: str | None = None
    description: str = ""


class ArtifactStep(BaseModel):
    step_number: int = Field(ge=1)
    url_before: str
    action: AgentAction
    target: SemanticTarget | None = None

    # If present, replay obtains the action value from the
    # invocation inputs instead of from a persisted literal.
    input_ref: str | None = None

    extracted_value: str | None = None


class CapabilityArtifact(BaseModel):
    version: str = ARTIFACT_VERSION
    capability_name: str
    goal: str
    target_domain: str

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(
            timezone.utc
        )
    )

    input_schema: list[InputParameter] = Field(
        default_factory=list
    )

    output_schema: list[OutputDefinition] = Field(
        default_factory=list
    )

    steps: list[ArtifactStep] = Field(
        default_factory=list
    )

    success_checkpoints: list[
        SuccessCheckpoint
    ] = Field(
        default_factory=list
    )

    outputs: dict[str, Any] = Field(
        default_factory=dict
    )

    def add_step(
        self,
        step_number,
        url_before,
        action,
        target=None,
        input_ref=None,
        extracted_value=None,
    ):
        self.steps.append(
            ArtifactStep(
                step_number=step_number,
                url_before=url_before,
                action=action,
                target=target,
                input_ref=input_ref,
                extracted_value=extracted_value,
            )
        )

    def add_input(
        self,
        name,
        value_type,
        description,
        required=True,
    ):
        self.input_schema.append(
            InputParameter(
                name=name,
                type=value_type,
                description=description,
                required=required,
            )
        )

    def add_output(
        self,
        name,
        value_type,
        description,
        required=True,
    ):
        self.output_schema.append(
            OutputDefinition(
                name=name,
                type=value_type,
                description=description,
                required=required,
            )
        )

    def add_checkpoint(
        self,
        checkpoint_type,
        *,
        target=None,
        expected_value=None,
        expected_path=None,
        description="",
    ):
        self.success_checkpoints.append(
            SuccessCheckpoint(
                type=checkpoint_type,
                target=target,
                expected_value=expected_value,
                expected_path=expected_path,
                description=description,
            )
        )

    def save(self, path):
        with open(
            path,
            "w",
            encoding="utf-8",
        ) as file:
            file.write(
                self.model_dump_json(indent=2)
            )