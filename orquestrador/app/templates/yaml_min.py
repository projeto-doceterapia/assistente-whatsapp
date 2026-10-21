from __future__ import annotations

# A deliberately small YAML subset parser for templates.yaml only. It avoids a
# runtime dependency on PyYAML (not in this service's approved libraries) while
# still keeping the templates in a real `.yaml` file. It supports exactly what
# templates.yaml uses: a top-level mapping of named templates, each a mapping of
# scalar fields, block sequences of scalars (`- item`) and block scalars
# introduced by `|-` (literal, strip final newline). Anything beyond that is out
# of scope and would need PyYAML.


def _parse_yaml(source: str) -> dict[str, object]:
    lines = source.splitlines()
    index = 0
    result: dict[str, object] = {}
    total = len(lines)
    while index < total:
        raw = lines[index]
        if _is_skippable(raw):
            index += 1
            continue
        if _indent(raw) != 0:
            raise _error(index, "expected a top-level key")
        key, inline = _split_key(raw.strip(), index)
        if inline:
            raise _error(index, "top-level scalars are not supported")
        value, index = _parse_mapping(lines, index + 1, base_indent=2)
        result[key] = value
    return result


def _parse_mapping(lines: list[str], index: int, base_indent: int) -> tuple[dict[str, object], int]:
    mapping: dict[str, object] = {}
    total = len(lines)
    while index < total:
        raw = lines[index]
        if _is_skippable(raw):
            index += 1
            continue
        indent = _indent(raw)
        if indent < base_indent:
            break
        if indent != base_indent:
            raise _error(index, "unexpected indentation")
        key, inline = _split_key(raw.strip(), index)
        if inline == "|-" or inline == "|":
            block, index = _parse_block_scalar(lines, index + 1, base_indent + 2, strip=inline == "|-")
            mapping[key] = block
        elif inline == "":
            nested, index = _parse_block(lines, index + 1, base_indent + 2)
            mapping[key] = nested
        else:
            mapping[key] = _scalar(inline)
            index += 1
    return mapping, index


def _parse_block(lines: list[str], index: int, base_indent: int) -> tuple[object, int]:
    total = len(lines)
    scan = index
    while scan < total and _is_skippable(lines[scan]):
        scan += 1
    if scan < total and lines[scan].strip().startswith("- "):
        return _parse_sequence(lines, scan, base_indent)
    return _parse_mapping(lines, index, base_indent)


def _parse_sequence(lines: list[str], index: int, base_indent: int) -> tuple[list[object], int]:
    items: list[object] = []
    total = len(lines)
    while index < total:
        raw = lines[index]
        if _is_skippable(raw):
            index += 1
            continue
        indent = _indent(raw)
        if indent < base_indent or not raw.strip().startswith("- "):
            break
        if indent != base_indent:
            raise _error(index, "unexpected indentation in sequence")
        items.append(_scalar(raw.strip()[2:]))
        index += 1
    return items, index


def _parse_block_scalar(lines: list[str], index: int, base_indent: int, strip: bool) -> tuple[str, int]:
    collected: list[str] = []
    total = len(lines)
    while index < total:
        raw = lines[index]
        if raw.strip() == "":
            collected.append("")
            index += 1
            continue
        if _indent(raw) < base_indent:
            break
        collected.append(raw[base_indent:])
        index += 1
    while collected and collected[-1] == "":
        collected.pop()
    text = "\n".join(collected)
    return (text if strip else text + "\n"), index


def _split_key(stripped: str, line_number: int) -> tuple[str, str]:
    if ":" not in stripped:
        raise _error(line_number, "expected 'key:'")
    key, _, rest = stripped.partition(":")
    return key.strip(), rest.strip()


def _scalar(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        return token[1:-1]
    return token


def _indent(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" "))


def _is_skippable(raw: str) -> bool:
    stripped = raw.strip()
    return stripped == "" or stripped.startswith("#")


def _error(line_number: int, message: str) -> ValueError:
    return ValueError(f"templates.yaml line {line_number + 1}: {message}")
