from __future__ import annotations

from typing import Any

import yaml


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split an Agent Skill document into (metadata, body).

    A skill document may begin with a YAML frontmatter block delimited by
    ``---`` lines. When the block is absent the metadata is empty and the
    original text is returned unchanged.
    """
    content = text.lstrip("\ufeff")
    if not content.startswith("---"):
        return {}, text
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raw = "\n".join(lines[1:index])
            try:
                parsed = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                return {}, text
            if not isinstance(parsed, dict):
                return {}, text
            body = "\n".join(lines[index + 1 :]).lstrip("\n")
            return parsed, body
    return {}, text


def strip_frontmatter(text: str) -> str:
    """Return the skill body without its YAML frontmatter block."""
    return split_frontmatter(text)[1]


def frontmatter_field(text: str, key: str) -> str | None:
    value = split_frontmatter(text)[0].get(key)
    return str(value) if value is not None else None
