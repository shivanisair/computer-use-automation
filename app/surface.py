from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page


def normalize_semantic_text(value: str) -> str:
    """
    Normalize semantic identifiers for robust matching.

    Case differences and insignificant whitespace should not
    cause deterministic replay to fail.
    """
    return " ".join(
        value.strip().casefold().split()
    )


@dataclass
class UIElement:
    id: int
    tag: str
    role: str
    name: str
    element_type: str | None = None
    value: str | None = None


@dataclass
class Observation:
    url: str
    title: str
    text: str
    elements: list[UIElement]


class PlaywrightSurface:
    def __init__(self, page: Page):
        self.page = page
        self._elements: dict[int, Any] = {}

    def observe(self) -> Observation:
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

                role = self._infer_role(
                    tag,
                    element_type,
                )

                name = self._get_accessible_name(locator)

                current_value = None

                if role in {"textbox", "combobox"}:
                    try:
                        current_value = locator.input_value()
                    except Exception:
                        current_value = None

                element = UIElement(
                    id=next_id,
                    tag=tag,
                    role=role,
                    name=name,
                    element_type=element_type,
                    value=current_value,
                )

                elements.append(element)
                self._elements[next_id] = locator
                next_id += 1

            except Exception as error:
                print(
                    f"Skipping interactive element "
                    f"{index}: {error}"
                )

        body_text = self.page.locator(
            "body"
        ).inner_text()[:5000]

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
        observation = self.observe()

        for element in observation.elements:
            if element.id == element_id:
                return element

        raise ValueError(
            f"Element ID {element_id} "
            "was not found in the current observation."
        )

    def resolve_semantic_target(
        self,
        role: str,
        name: str,
    ):
        """
        Resolve a recorded semantic target against the live UI.

        Matching is case-insensitive and ignores insignificant
        whitespace, while still requiring both role and name.
        """

        observation = self.observe()

        normalized_role = normalize_semantic_text(role)
        normalized_name = normalize_semantic_text(name)

        matches = [
            element
            for element in observation.elements
            if (
                normalize_semantic_text(element.role)
                == normalized_role
                and normalize_semantic_text(element.name)
                == normalized_name
            )
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
    ):
        locator = self._resolve_element(
            element_id
        )
        locator.fill(value)

    def click(
        self,
        element_id: int,
    ):
        locator = self._resolve_element(
            element_id
        )
        locator.click()

    def select_option(
        self,
        element_id: int,
        value: str,
    ):
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
            return locator.input_value()
        except Exception:
            return locator.inner_text()

    def click_semantic(
        self,
        role: str,
        name: str,
    ):
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
    ):
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
    ):
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
            return locator.input_value()
        except Exception:
            return locator.inner_text()

    def _get_accessible_name(
        self,
        locator,
    ) -> str:
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
            inner_text = locator.inner_text().strip()

            if inner_text:
                return inner_text

        except Exception:
            pass

        element_type = locator.get_attribute(
            "type"
        )

        if element_type in {
            "submit",
            "button",
        }:
            value = locator.get_attribute(
                "value"
            )

            if value:
                return value.strip()

        name = locator.get_attribute("name")

        if name:
            return name.strip()

        return "(unnamed)"

    @staticmethod
    def _infer_role(
        tag: str,
        element_type: str | None,
    ) -> str:
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

        return "unknown"

    def _resolve_element(
        self,
        element_id: int,
    ):
        if element_id not in self._elements:
            raise ValueError(
                f"Element ID {element_id} "
                "is not available in the current observation."
            )

        return self._elements[element_id]

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
            line = (
                f"[{element.id}] "
                f"{element.role} "
                f'name="{element.name}" '
                f'tag="{element.tag}"'
            )

            if element.value is not None:
                line += (
                    f' value="{element.value}"'
                )

            lines.append(line)

        lines.extend(
            [
                "",
                "VISIBLE PAGE TEXT:",
                observation.text,
            ]
        )

        return "\n".join(lines)