from app.escalation import (
    BusinessOutcome,
    ErrorCategory,
    HardAutomationFailure,
    RecoverableAutomationError,
    create_handoff,
)


def demonstrate_error(error):
    print("\n" + "=" * 70)
    print(f"ERROR CATEGORY: {error.category.value}")
    print("=" * 70)
    print(f"Reason: {error.reason}")

    handoff = create_handoff(
        category=error.category,
        reason=error.reason,
        step_number=2,
        current_url=(
            "https://parabank.parasoft.com/"
            "parabank/contact.htm"
        ),
        last_action="demo_action",
    )

    print("\nHUMAN HANDOFF")
    print(handoff.model_dump_json(indent=2))


def main():
    examples = [
        BusinessOutcome(
            "The application completed normally, "
            "but the requested business result was not available."
        ),
        RecoverableAutomationError(
            "The expected semantic target could not be found "
            "after the UI changed."
        ),
        HardAutomationFailure(
            "Execution was blocked because the current domain "
            "is outside the configured allowlist."
        ),
    ]

    for error in examples:
        demonstrate_error(error)


if __name__ == "__main__":
    main()