from enum import Enum

from openai import OpenAI
from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """Actions available to the discovery agent."""

    FILL = "fill"
    CLICK = "click"
    SELECT = "select"
    EXTRACT = "extract"
    WAIT = "wait"
    COMPLETE = "complete"
    ESCALATE = "escalate"


class AgentAction(BaseModel):
    """Structured action selected during a discovery step."""

    action: ActionType
    target_id: int | None = None
    value: str | None = None
    output_name: str | None = None
    reason: str = Field(min_length=1)


class GoalResult(BaseModel):
    """Final result produced when the discovery goal terminates."""

    completed: bool
    reason: str
    outputs: dict[str, str] = Field(default_factory=dict)

def decide_action(
    goal: str,
    observation: str,
) -> AgentAction:
    """
    Ask the LLM to choose the next structured action for the discovery run.
    """
    client = OpenAI()

    response = client.responses.parse(
        model="gpt-5.4-mini",
        input=[
            {
                "role": "system",
                "content": (
                    "You are a computer-use discovery agent. "
                    "Your job is to accomplish the user's goal by interacting "
                    "with the current application.\n\n"
                    "Choose exactly one next action based only on the supplied "
                    "observation.\n\n"
                    "Rules:\n"
                    "- Use only element IDs present in the observation.\n"
                    "- Use FILL for textboxes.\n"
                    "- Use CLICK for links and buttons.\n"
                    "- Use SELECT for comboboxes.\n"
                    "- Use EXTRACT when the goal requires reading a value.\n"
                    "- Use WAIT only when the page appears to be loading.\n"
                    "- Use COMPLETE only when the goal has been achieved.\n"
                    "- Use ESCALATE if you cannot safely determine what to do.\n"
                    "- Never invent an element ID."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"GOAL:\n{goal}\n\n"
                    f"CURRENT OBSERVATION:\n{observation}"
                ),
            },
        ],
        text_format=AgentAction,
    )

    return response.output_parsed

if __name__ == "__main__":
    observation = """
TITLE: ParaBank | Welcome | Online Banking
URL: https://parabank.parasoft.com/parabank/index.htm

INTERACTIVE ELEMENTS:
[9] textbox name="username" tag="input"
[10] textbox name="password" tag="input"
[11] button name="Log In" tag="input"
"""

    action = decide_action(
        goal="Enter the username demo_user into the username field.",
        observation=observation,
    )

    print(action)