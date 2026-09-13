from enum import Enum

from pydantic import BaseModel, Field


class ErrorCategory(str, Enum):
    """
    Explicit failure taxonomy used by discovery and replay.
    """

    BUSINESS_OUTCOME = "business_outcome"
    RECOVERABLE_ERROR = "recoverable_error"
    HARD_FAILURE = "hard_failure"


class HandoffRequest(BaseModel):
    """
    Structured context provided when automation cannot safely continue.

    The request describes why control is being transferred. The replay
    layer is responsible for pausing automation while preserving the
    same live browser session and for recording control transitions.
    """

    category: ErrorCategory
    reason: str = Field(min_length=1)

    step_number: int | None = None
    current_url: str | None = None
    last_action: str | None = None

    suggested_human_action: str = Field(min_length=1)


class AutomationError(Exception):
    """
    Base exception carrying a classified automation failure.
    """

    def __init__(
        self,
        category: ErrorCategory,
        reason: str,
    ):
        super().__init__(reason)

        self.category = category
        self.reason = reason


class RecoverableAutomationError(AutomationError):
    def __init__(self, reason: str):
        super().__init__(
            ErrorCategory.RECOVERABLE_ERROR,
            reason,
        )


class HardAutomationFailure(AutomationError):
    def __init__(self, reason: str):
        super().__init__(
            ErrorCategory.HARD_FAILURE,
            reason,
        )


class BusinessOutcome(AutomationError):
    """
    Expected business-level outcome rather than an infrastructure failure.

    Example:
        "No eligible accounts are available."
    """

    def __init__(self, reason: str):
        super().__init__(
            ErrorCategory.BUSINESS_OUTCOME,
            reason,
        )


def create_handoff(
    category: ErrorCategory,
    reason: str,
    step_number: int | None = None,
    current_url: str | None = None,
    last_action: str | None = None,
) -> HandoffRequest:
    """
    Produce structured context for a human operator.
    """

    if category == ErrorCategory.RECOVERABLE_ERROR:
        suggested_action = (
            "Review the current UI state, perform the requested "
            "manual recovery in the existing browser session, and "
            "return control when the UI is ready for automation."
        )

    elif category == ErrorCategory.BUSINESS_OUTCOME:
        suggested_action = (
            "Review the business outcome and decide whether "
            "a different workflow or user input is required."
        )

    else:
        suggested_action = (
            "Inspect the failure and current UI before "
            "continuing manually."
        )

    return HandoffRequest(
        category=category,
        reason=reason,
        step_number=step_number,
        current_url=current_url,
        last_action=last_action,
        suggested_human_action=suggested_action,
    )
