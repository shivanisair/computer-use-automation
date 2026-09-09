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
    Browser-backed surface adapter used by discovery and replay.

    Discovery may use temporary numeric element IDs.
    Replay can resolve controls deterministically using semantic
    role/name information.
    """

    def __init__(self, page: Page):
        self.page = page
        self._elements: dict[int, Any] = {}

    def observe(self) -> Observation:
        """
        Capture the current page state and visible interactive controls.
        """
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

                element_type = locator.get_attribute(
                    "type"
                )

                element = UIElement(
                    id=next_id,
                    tag=tag,
                    role=self._infer_role(
                        tag,
                        element_type,
                    ),
                    name=self._get_accessible_name(
                        locator
                    ),
                    element_type=element_type,
                )

                elements.append(element)

                self._elements[next_id] = locator

                next_id += 1

            except Exception:
                # Dynamic pages may detach controls while
                # an observation is being collected.
                continue

        body_text = self.page.locator(
            "body"
        ).inner_text()

        body_text = body_text[:5000]

        return Observation(
            url=self.page.url,
            title=self.page.title(),
            text=body_text,
            elements=elements,
        )

    def get_element(
        self,
        element_id: int,
    ) -> UIElement:
        """
        Return semantic metadata for an element from
        the current observation.
        """
        observation = self.observe()

        for element in observation.elements:
            if element.id == element_id:
                return element

        raise ValueError(
            f"Element {element_id} does not exist "
            "in the current observation."
        )

    def resolve_semantic_target(
        self,
        role: str,
        name: str,
    ):
        """
        Deterministically resolve a visible control using
        semantic role and accessible name.

        Replay must get exactly one match. Ambiguous or missing
        targets fail rather than guessing.
        """
        observation = self.observe()

        matches = [
            element
            for element in observation.elements
            if element.role == role
            and element.name == name
        ]

        if not matches:
            raise ValueError(
                "Semantic target not found: "
                f'role="{role}", name="{name}"'
            )

        if len(matches) > 1:
            raise ValueError(
                "Semantic target is ambiguous: "
                f'role="{role}", name="{name}". '
                f"Found {len(matches)} matches."
            )

        return self._resolve_element(
            matches[0].id
        )

    def fill(
        self,
        element_id: int,
        value: str,
    ) -> None:
        locator = self._resolve_element(
            element_id
        )
        locator.fill(value)

    def click(
        self,
        element_id: int,
    ) -> None:
        locator = self._resolve_element(
            element_id
        )
        locator.click()

    def select_option(
        self,
        element_id: int,
        value: str,
    ) -> None:
        locator = self._resolve_element(
            element_id
        )
        locator.select_option(value)

    def extract_text(
        self,
        element_id: int,
    ) -> str:
        locator = self._resolve_element(
            element_id
        )

        try:
            return locator.inner_text().strip()
        except Exception:
            return locator.input_value().strip()

    def click_semantic(
        self,
        role: str,
        name: str,
    ) -> None:
        locator = self.resolve_semantic_target(
            role,
            name,
        )
        locator.click()

    def fill_semantic(
        self,
        role: str,
        name: str,
        value: str,
    ) -> None:
        locator = self.resolve_semantic_target(
            role,
            name,
        )
        locator.fill(value)

    def select_semantic(
        self,
        role: str,
        name: str,
        value: str,
    ) -> None:
        locator = self.resolve_semantic_target(
            role,
            name,
        )
        locator.select_option(value)

    def extract_semantic(
        self,
        role: str,
        name: str,
    ) -> str:
        locator = self.resolve_semantic_target(
            role,
            name,
        )

        try:
            return locator.inner_text().strip()
        except Exception:
            return locator.input_value().strip()

    def _get_accessible_name(
        self,
        locator,
    ) -> str:
        """
        Resolve a compact human-readable name for
        an interactive control.
        """
        aria_label = locator.get_attribute(
            "aria-label"
        )

        if aria_label:
            return aria_label.strip()

        placeholder = locator.get_attribute(
            "placeholder"
        )

        if placeholder:
            return placeholder.strip()

        try:
            text = locator.inner_text().strip()
        except Exception:
            text = ""

        if text:
            return text

        value = locator.get_attribute(
            "value"
        )

        element_type = locator.get_attribute(
            "type"
        )

        if (
            value
            and element_type
            in {"submit", "button"}
        ):
            return value.strip()

        name = locator.get_attribute(
            "name"
        )

        if name:
            return name.strip()

        return "(unnamed)"

    @staticmethod
    def _infer_role(
        tag: str,
        element_type: str | None,
    ) -> str:
        """
        Map HTML controls to a small surface-neutral
        role vocabulary.
        """
        if tag == "a":
            return "link"

        if tag == "button":
            return "button"

        if tag == "select":
            return "combobox"

        if tag == "textarea":
            return "textbox"

        if tag == "input":
            if element_type in {
                "submit",
                "button",
            }:
                return "button"

            if element_type == "checkbox":
                return "checkbox"

            if element_type == "radio":
                return "radio"

            return "textbox"

        return tag

    def _resolve_element(
        self,
        element_id: int,
    ):
        locator = self._elements.get(
            element_id
        )

        if locator is None:
            raise ValueError(
                f"Element {element_id} does not "
                "exist in the current observation."
            )

        return locator

    @staticmethod
    def format_observation(
        observation: Observation,
    ) -> str:
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