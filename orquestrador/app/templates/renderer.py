from __future__ import annotations

import string
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.templates.yaml_min import _parse_yaml

_TEMPLATES_FILE = Path(__file__).with_name("templates.yaml")

# The Cloud API forbids line breaks inside template body parameters, so a
# list-shaped section is collapsed into a single line with this separator.
_LIST_SEPARATOR = " | "
_EMPTY_SECTION = "nada pendente"


class RenderMode(str, Enum):
    """Baileys renders multi-line free text; Cloud API renders newline-free parts."""

    TEXT = "text"
    PARTS = "parts"


class TemplateError(RuntimeError):
    """Base error for template rendering."""


class TemplateNotFoundError(TemplateError):
    def __init__(self, template_key: str) -> None:
        super().__init__(f"unknown template key: {template_key}")
        self.template_key = template_key


class MissingTemplateParameterError(TemplateError):
    def __init__(self, template_key: str, parameter: str) -> None:
        super().__init__(
            f"template '{template_key}' is missing required parameter '{parameter}'"
        )
        self.template_key = template_key
        self.parameter = parameter


@dataclass(frozen=True)
class RenderedNotification:
    """A notification rendered for both channels from one data set."""

    text: str
    parts: list[str]
    cloud_name: str
    cloud_lang: str


@dataclass(frozen=True)
class _Template:
    key: str
    cloud_name: str
    cloud_lang: str
    sections: tuple[str, ...]
    text: str
    parts: tuple[str, ...]


class TemplateRenderer:
    """Renders notification templates into text (Baileys) and parts (Cloud API)."""

    def __init__(self, templates: dict[str, _Template]) -> None:
        self._templates = templates

    def keys(self) -> list[str]:
        return list(self._templates)

    def template_name(self, template_key: str) -> str:
        return self._template(template_key).cloud_name

    def template_lang(self, template_key: str) -> str:
        return self._template(template_key).cloud_lang

    def render(
        self,
        template_key: str,
        parametros: dict[str, object],
        strict: bool = False,
    ) -> RenderedNotification:
        template = self._template(template_key)
        if strict:
            self._require_all(template, parametros)
        values = self._resolve(template, parametros)
        text = self._format(template.text, values)
        parts = [self._format(part, values) for part in template.parts]
        return RenderedNotification(
            text=text,
            parts=parts,
            cloud_name=template.cloud_name,
            cloud_lang=template.cloud_lang,
        )

    def render_text(self, template_key: str, parametros: dict[str, object]) -> str:
        return self.render(template_key, parametros).text

    def render_parts(self, template_key: str, parametros: dict[str, object]) -> list[str]:
        return self.render(template_key, parametros).parts

    def required_parameters(self, template_key: str) -> set[str]:
        return self._placeholders(self._template(template_key))

    def _template(self, template_key: str) -> _Template:
        try:
            return self._templates[template_key]
        except KeyError as exc:
            raise TemplateNotFoundError(template_key) from exc

    def _require_all(self, template: _Template, parametros: dict[str, object]) -> None:
        # Sections may be absent (they render "nada pendente"); every other
        # placeholder must be supplied, so a malformed notification fails loudly.
        for name in self._placeholders(template):
            if name in template.sections:
                continue
            if name not in parametros:
                raise MissingTemplateParameterError(template.key, name)

    def _resolve(self, template: _Template, parametros: dict[str, object]) -> dict[str, str]:
        values: dict[str, str] = {}
        for name, raw in parametros.items():
            if name in template.sections:
                values[name] = self._join_section(raw)
            else:
                values[name] = self._stringify(raw)
        # Every placeholder is always present so formatting never raises; empty
        # list-shaped sections render "nada pendente", other gaps render empty.
        for name in self._placeholders(template):
            if name in template.sections:
                values.setdefault(name, _EMPTY_SECTION)
            else:
                values.setdefault(name, "")
        return values

    @staticmethod
    def _placeholders(template: _Template) -> set[str]:
        names: set[str] = set()
        formatter = string.Formatter()
        for pattern in (template.text, *template.parts):
            for _, field_name, _, _ in formatter.parse(pattern):
                if field_name:
                    names.add(field_name)
        return names

    def _join_section(self, raw: object) -> str:
        if raw is None:
            return _EMPTY_SECTION
        if isinstance(raw, (list, tuple)):
            items = [self._stringify(item) for item in raw if self._stringify(item)]
            return _LIST_SEPARATOR.join(items) if items else _EMPTY_SECTION
        text = self._stringify(raw)
        return text or _EMPTY_SECTION

    @staticmethod
    def _stringify(raw: object) -> str:
        if raw is None:
            return ""
        return str(raw)

    @staticmethod
    def _format(pattern: str, values: dict[str, str]) -> str:
        return pattern.format(**values)


def load_default_renderer() -> TemplateRenderer:
    return TemplateRenderer(_load_templates(_TEMPLATES_FILE.read_text(encoding="utf-8")))


def _load_templates(source: str) -> dict[str, _Template]:
    parsed = _parse_yaml(source)
    templates: dict[str, _Template] = {}
    for key, body in parsed.items():
        if not isinstance(body, dict):
            continue
        templates[key] = _Template(
            key=key,
            cloud_name=str(body.get("cloud_name", key)),
            cloud_lang=str(body.get("cloud_lang", "pt_BR")),
            sections=tuple(body.get("sections", []) or ()),
            text=str(body["text"]),
            parts=tuple(str(part) for part in body.get("parts", []) or ()),
        )
    return templates
