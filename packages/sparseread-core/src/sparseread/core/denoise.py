"""Deterministic observation hygiene before SparseRead evidence selection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import ClassVar

import yaml

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_PROGRESS_RE = re.compile(r"(?:\b\d{1,3}%|\b\d+\s*/\s*\d+\b|\[[#=>.\s-]{4,}\])")
_VOLATILE_RE = re.compile(
    r"(?:\b\d{1,4}[-/:]\d{1,4}(?:[-/:]\d{1,4})?(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?)?\b|"
    r"\b0x[0-9a-f]+\b|\b\d+(?:\.\d+)?\b)",
    re.IGNORECASE,
)
_VOLATILE_PREFIX_RE = re.compile(
    r"^(?:\[)?(?:\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?(?:\d{2})?)?"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(?:\])?(?:\s+|$)"
)
_MAX_BLOCK_LINES = 32
_MIN_BLOCK_LINES = 2
_MAX_STRUCTURED_CHARS = 2_000_000
_MAX_YAML_NODES = 20_000
_MAX_YAML_DEPTH = 128


@dataclass(frozen=True, slots=True)
class DenoiseResult:
    text: str
    input_chars: int
    output_chars: int
    rules: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.input_chars != self.output_chars or bool(self.rules)


class _HTMLTextExtractor(HTMLParser):
    """Small conservative HTML-to-text extractor with an explicit main-content fast path."""

    _DROP: ClassVar[set[str]] = {"script", "style", "svg", "template", "noscript"}
    _BLOCK: ClassVar[set[str]] = {
        "address", "article", "aside", "blockquote", "br", "div", "dl", "dt", "dd",
        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "td",
        "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._drop_depth = 0
        self._primary_depth = 0
        self._title_depth = 0
        self.all_parts: list[str] = []
        self.primary_parts: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in self._DROP:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return
        if tag in {"main", "article"}:
            self._primary_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag in self._BLOCK:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._DROP:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if self._drop_depth:
            return
        if tag in self._BLOCK:
            self._append("\n")
        if tag in {"main", "article"}:
            self._primary_depth = max(0, self._primary_depth - 1)
        if tag == "title":
            self._title_depth = max(0, self._title_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self._append(f" {value} ")

    def _append(self, value: str) -> None:
        self.all_parts.append(value)
        if self._primary_depth:
            self.primary_parts.append(value)
        if self._title_depth and value.strip():
            self.title_parts.append(value)

    @staticmethod
    def render(parts: list[str]) -> str:
        text = "".join(parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(lines)

    def best_text(self) -> tuple[str, bool]:
        all_text = self.render(self.all_parts)
        primary = self.render(self.primary_parts)
        primary_chars = len(primary.strip())
        all_chars = max(1, len(all_text.strip()))
        if primary_chars >= 500 and primary_chars / all_chars >= 0.45:
            title = " ".join(part.strip() for part in self.title_parts if part.strip())
            return (f"# {title}\n\n{primary}" if title else primary), True
        return all_text, False


def _clean_controls(text: str) -> tuple[str, bool]:
    cleaned = _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(
        char
        for char in cleaned
        if char in {"\n", "\t"} or (ord(char) >= 32 and char not in {"\u200b", "\u200c", "\u200d", "\ufeff"})
    )
    return cleaned, cleaned != text


def _protected_lines(lines: list[str]) -> list[bool]:
    protected: list[bool] = []
    fenced = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            protected.append(True)
            fenced = not fenced
            continue
        protected.append(fenced or line.startswith(("    ", "\t")))
    return protected


def _progress_signature(line: str) -> str:
    if not _PROGRESS_RE.search(line):
        return ""
    return re.sub(r"\s+", " ", _VOLATILE_RE.sub("#", line.strip().lower()))


def _collapse_volatile_prefix(text: str) -> tuple[str, bool]:
    """Fold consecutive lines whose body is identical after a leading timestamp prefix."""
    lines = text.splitlines()
    protected = _protected_lines(lines)
    output: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        line = lines[index]
        if protected[index] or not line.strip():
            output.append(line.rstrip())
            index += 1
            continue

        match = _VOLATILE_PREFIX_RE.match(line)
        if not match:
            output.append(line.rstrip())
            index += 1
            continue
        body = re.sub(r"\s+", " ", line[match.end() :].strip())
        if not body:
            output.append(line.rstrip())
            index += 1
            continue

        end = index + 1
        while end < len(lines) and not protected[end]:
            candidate = lines[end]
            candidate_match = _VOLATILE_PREFIX_RE.match(candidate)
            if not candidate_match:
                break
            candidate_body = re.sub(r"\s+", " ", candidate[candidate_match.end() :].strip())
            if candidate_body != body:
                break
            end += 1
        if end - index >= 3:
            output.extend(
                [
                    line.rstrip(),
                    f"[repeated line with volatile prefix ×{end - index}]",
                    lines[end - 1].rstrip(),
                ]
            )
            changed = True
            index = end
            continue

        output.append(line.rstrip())
        index += 1

    compact = re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()
    return compact, changed or compact != text.strip()


def _collapse_repeated_blocks(text: str) -> tuple[str, bool]:
    """Fold consecutive identical multi-line blocks into one occurrence plus a count."""
    lines = text.splitlines()
    protected = _protected_lines(lines)
    keys = [hash(line) for line in lines]
    output: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        if protected[index] or not lines[index].strip():
            output.append(lines[index].rstrip())
            index += 1
            continue

        best_k = 0
        best_count = 0
        max_k = min(_MAX_BLOCK_LINES, (len(lines) - index) // 2)
        for k in range(max_k, _MIN_BLOCK_LINES - 1, -1):
            if keys[index] != keys[index + k]:
                continue
            if any(protected[index + j] for j in range(k)):
                continue
            block = lines[index : index + k]
            block_keys = keys[index : index + k]
            count = 1
            pos = index + k
            while pos + k <= len(lines) and keys[pos : pos + k] == block_keys:
                if lines[pos : pos + k] != block:
                    break
                count += 1
                pos += k
            if count >= 2 and (k >= _MIN_BLOCK_LINES + 1 or count >= 3):
                best_k = k
                best_count = count
                break

        if best_k:
            output.extend(line.rstrip() for line in lines[index : index + best_k])
            output.append(f"[repeated block ×{best_count}]")
            changed = True
            index += best_k * best_count
            continue

        output.append(lines[index].rstrip())
        index += 1

    compact = re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()
    return compact, changed or compact != text.strip()


def _minify_json(text: str) -> tuple[str, bool]:
    """Strip whitespace outside JSON strings; byte-faithful for all other tokens."""
    output: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            output.append(char)
        elif char in " \t\r\n":
            continue
        else:
            output.append(char)
    compact = "".join(output)
    return compact, compact != text


def _yaml_tree_safe(root: yaml.Node) -> bool:
    """Reject YAML trees that could expand during construction (aliases, size, depth)."""
    nodes = 0
    seen: set[int] = set()

    def walk(node: yaml.Node, depth: int) -> bool:
        nonlocal nodes
        if depth > _MAX_YAML_DEPTH:
            return False
        node_id = id(node)
        if node_id in seen:
            return False
        seen.add(node_id)
        nodes += 1
        if nodes > _MAX_YAML_NODES:
            return False
        if isinstance(node, yaml.ScalarNode):
            return True
        if isinstance(node, yaml.MappingNode):
            return all(walk(key, depth + 1) and walk(value, depth + 1) for key, value in node.value)
        return all(walk(item, depth + 1) for item in node.value)

    return walk(root, 1)


def _flatten_yaml(text: str) -> tuple[str, bool]:
    """Flatten a single-document, comment-free YAML data file into compact flow style."""
    if any(line.lstrip().startswith(("#", "---", "...")) for line in text.splitlines()):
        return text, False
    if len(text) > _MAX_STRUCTURED_CHARS:
        return text, False
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        if root is None or not _yaml_tree_safe(root):
            return text, False
        data = yaml.safe_load(text)
        if data is None:
            return text, False
        compact = yaml.safe_dump(
            data,
            sort_keys=False,
            default_flow_style=True,
            allow_unicode=True,
        ).strip()
    except yaml.YAMLError:
        return text, False
    return compact, compact != text


def _flatten_structured(text: str, kind: str) -> tuple[str, bool]:
    if kind == "json":
        return _minify_json(text)
    return _flatten_yaml(text)


def _collapse_repetition(text: str) -> tuple[str, bool]:
    lines = text.splitlines()
    protected = _protected_lines(lines)
    output: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        line = lines[index]
        if protected[index] or not line.strip():
            output.append(line.rstrip())
            index += 1
            continue

        normalized = re.sub(r"\s+", " ", line.strip())
        end = index + 1
        while (
            end < len(lines)
            and not protected[end]
            and re.sub(r"\s+", " ", lines[end].strip()) == normalized
        ):
            end += 1
        if end - index >= 3:
            output.extend([line.rstrip(), f"[repeated identical line ×{end - index}]"])
            changed = True
            index = end
            continue

        signature = _progress_signature(line)
        end = index + 1
        while end < len(lines) and not protected[end] and signature and _progress_signature(lines[end]) == signature:
            end += 1
        if end - index >= 4:
            output.extend(
                [
                    line.rstrip(),
                    f"[collapsed {end - index - 2} intermediate progress updates]",
                    lines[end - 1].rstrip(),
                ]
            )
            changed = True
            index = end
            continue

        output.append(line.rstrip())
        index += 1

    compact = re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()
    return compact, changed or compact != text.strip()


def denoise_text(
    text: str,
    *,
    source_kind: str = "text",
    min_chars: int = 4_096,
) -> DenoiseResult:
    """Remove carrier noise without ranking or deleting unique semantic content."""
    original = text
    if len(text) < min_chars:
        return DenoiseResult(text=text, input_chars=len(text), output_chars=len(text), rules=())

    rules: list[str] = []
    text, controls_changed = _clean_controls(text)
    if controls_changed:
        rules.append("controls")

    kind = source_kind.strip().lower()
    if kind in {"html", "htm"}:
        parser = _HTMLTextExtractor()
        parser.feed(text)
        parser.close()
        html_text, used_primary = parser.best_text()
        if html_text.strip():
            text = html_text
            rules.append("html_main" if used_primary else "html_text")
    elif kind in {"json", "yaml", "yml"}:
        structured_kind = "yaml" if kind in {"yaml", "yml"} else "json"
        text, structured_changed = _flatten_structured(text, structured_kind)
        if structured_changed:
            rules.append(f"structured_{structured_kind}")

    text, repetition_changed = _collapse_repetition(text)
    if repetition_changed:
        rules.append("repetition")

    text, volatile_changed = _collapse_volatile_prefix(text)
    if volatile_changed:
        rules.append("volatile_prefix")

    text, block_changed = _collapse_repeated_blocks(text)
    if block_changed:
        rules.append("repeated_block")

    return DenoiseResult(
        text=text,
        input_chars=len(original),
        output_chars=len(text),
        rules=tuple(rules),
    )
