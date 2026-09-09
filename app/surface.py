from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page


@dataclass
class UIElement:
    id: int
    tag: str
    role: str
    name: str
    element_type: str | None = None


@dataclass
class Observation:
    url: str
    title: str
    text: str
    elements: list[UIElement]


class PlaywrightSurface:
    """
    Browser-backed surface adapter used by the discovery agent.

    Exposes a compact semantic representation of the current UI while
    keeping browser-specific interaction details behind a single boundary.
    """

    def __init__(self, page: Page):
        self.page = page
        self._elements: dict[int, Any] = {}

    def observe(self) -> Observation:
        """Capture the current page state and visible interactive controls."""
        self._elements.clear()

        raw_elements = self.page.locator(
            "input, button, a, select, textarea"
        )

        elements: list[UIElement] = []
        next_id = 1

        for index in range(raw_elements.count()):
            locator = raw_elements.nth(index)

            try:
                if not locator.is_visible():
                    continue

                tag = locator.evaluate(
                    "(el) => el.tagName.toLowerCase()"
                )
                element_type = locator.get_attribute("type")

                elements.append(
                    UIElement(
                        id=next_id,
                        tag=tag,
                        role=self._infer_role(tag, element_type),
                        name=self._get_accessible_name(locator),
                        element_type=element_type,
                    )
                )

                # Element IDs are scoped to the current observation and provide
                # a compact target reference for discovery actions.
                self._elements[next_id] = locator
                next_id += 1

            except Exception:
                # Dynamic pages may detach elements while the observation is
                # being collected. A stale control should not invalidate the
                # complete page observation.
                continue

        body_text = self.page.locator("body").inner_text()

        # Bound observation size while retaining enough page context for
        # goal-directed reasoning.
        body_text = body_text[:5000]

        return Observation(
            url=self.page.url,
            title=self.page.title(),
            text=body_text,
            elements=elements,
        )

    def _get_accessible_name(self, locator) -> str:
        """Resolve a compact human-readable name for an interactive control."""
        aria_label = locator.get_attribute("aria-label")
        if aria_label:
            return aria_label.strip()

        placeholder = locator.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()

        try:
            text = locator.inner_text().strip()
        except Exception:
            text = ""

        if text:
            return text

        value = locator.get_attribute("value")
        element_type = locator.get_attribute("type")

        if value and element_type in {"submit", "button"}:
            return value.strip()

        name = locator.get_attribute("name")
        if name:
            return name.strip()

        return "(unnamed)"

    @staticmethod
    def _infer_role(tag: str, element_type: str | None) -> str:
        """Map HTML controls to a small surface-neutral role vocabulary."""
        if tag == "a":
            return "link"

        if tag == "button":
            return "button"

        if tag == "select":
            return "combobox"

        if tag == "textarea":
            return "textbox"

        if tag == "input":
            if element_type in {"submit", "button"}:
                return "button"

            if element_type == "checkbox":
                return "checkbox"

            if element_type == "radio":
                return "radio"

            return "textbox"

        return tag
    

    def fill(self, element_id: int, value: str) -> None:
        """Fill a textbox identified in the most recent observation."""
        locator = self._resolve_element(element_id)
        locator.fill(value)

    def click(self, element_id: int) -> None:
        """Click a control identified in the most recent observation."""
        locator = self._resolve_element(element_id)
        locator.click()

    def select_option(self, element_id: int, value: str) -> None:
        """Select an option from a combobox."""
        locator = self._resolve_element(element_id)
        locator.select_option(value)

    def extract_text(self, element_id: int) -> str:
        """Read visible text from a control in the current observation."""
        locator = self._resolve_element(element_id)

        try:
            return locator.inner_text().strip()
        except Exception:
            return locator.input_value().strip()

    def _resolve_element(self, element_id: int):
        """Resolve an observation-scoped element ID to its live locator."""
        locator = self._elements.get(element_id)

        if locator is None:
            raise ValueError(
                f"Element {element_id} does not exist in the current observation."
            )

        return locator

    @staticmethod
    def format_observation(observation: Observation) -> str:
        """Serialize an observation into a compact representation for an LLM."""
        lines = [
            f"TITLE: {observation.title}",
            f"URL: {observation.url}",
            "",
            "INTERACTIVE ELEMENTS:",
        ]

        for element in observation.elements:
            lines.append(
                f'[{element.id}] {element.role} '
                f'name="{element.name}" '
                f'tag="{element.tag}"'
            )

        lines.extend(
            [
                "",
                "VISIBLE PAGE TEXT:",
                observation.text,
            ]
        )

        return "\n".join(lines)