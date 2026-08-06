from __future__ import annotations

import shlex
from dataclasses import dataclass
from urllib.parse import urlparse


SHELL_SEPARATORS = ("&&", "||", "|", ";", "\n", "\r")


@dataclass(frozen=True)
class ShellEffects:
    network_program: str | None = None
    network_hosts: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    metadata_write_paths: tuple[str, ...] = ()
    directory_write_paths: tuple[str, ...] = ()
    has_unscoped_file_mutation: bool = False

    @property
    def has_network(self) -> bool:
        return self.network_program is not None

    @property
    def has_file_mutation(self) -> bool:
        return bool(
            self.write_paths
            or self.metadata_write_paths
            or self.directory_write_paths
            or self.has_unscoped_file_mutation
        )

    @property
    def mutation_paths(self) -> tuple[str, ...]:
        return _unique(
            [
                *self.write_paths,
                *self.metadata_write_paths,
                *self.directory_write_paths,
            ]
        )


def analyze_shell_effects(command: str) -> ShellEffects:
    redirection_paths = list(redirection_targets(command))
    network_program: str | None = None
    hosts: list[str] = []
    network_paths: list[str] = []
    metadata_paths: list[str] = []
    directory_paths: list[str] = []
    has_unscoped_file_mutation = False

    for segment in split_shell_segments(command):
        tokens = _tokens(segment)
        if not tokens:
            continue
        lowered = [token.lower() for token in tokens]
        program = _program(lowered)

        detected_network = _network_program(lowered)
        if detected_network is not None:
            network_program = network_program or detected_network
            hosts.extend(_network_hosts(tokens))
            output_paths, writes_unknown_path = _network_output_effects(
                tokens,
                detected_network,
            )
            network_paths.extend(output_paths)
            has_unscoped_file_mutation = (
                has_unscoped_file_mutation or writes_unknown_path
            )

        if program == "chmod":
            metadata_paths.extend(_chmod_paths(tokens))
        elif program in {"mkdir", "md"}:
            directory_paths.extend(_positional_paths(tokens[1:]))

    return ShellEffects(
        network_program=network_program,
        network_hosts=_unique(hosts),
        write_paths=_unique([*redirection_paths, *network_paths]),
        metadata_write_paths=_unique(metadata_paths),
        directory_write_paths=_unique(directory_paths),
        has_unscoped_file_mutation=has_unscoped_file_mutation,
    )


def split_shell_segments(command: str) -> list[str]:
    segments: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    index = 0

    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue

        separator = next(
            (value for value in SHELL_SEPARATORS if command.startswith(value, index)),
            None,
        )
        if separator is None:
            index += 1
            continue
        segment = command[start:index].strip()
        if segment:
            segments.append(segment)
        index += len(separator)
        start = index

    tail = command[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def redirection_targets(command: str) -> tuple[str, ...]:
    targets: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0

    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char != ">" or (index > 0 and command[index - 1] in {">", "<"}):
            index += 1
            continue

        index += 2 if index + 1 < len(command) and command[index + 1] == ">" else 1
        while index < len(command) and command[index].isspace():
            index += 1
        if index >= len(command) or command[index] == "&":
            continue

        target, index = _read_shell_word(command, index)
        cleaned = _clean_path(target)
        if cleaned is not None:
            targets.append(cleaned)

    return _unique(targets)


def mask_quoted_text(command: str) -> str:
    chars = list(command)
    quote: str | None = None
    escaped = False
    for index, char in enumerate(command):
        if escaped:
            if quote is not None:
                chars[index] = " "
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            if quote is not None:
                chars[index] = " "
            continue
        if quote is not None:
            chars[index] = " "
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            chars[index] = " "
    return "".join(chars)


def _read_shell_word(command: str, start: int) -> tuple[str, int]:
    quote: str | None = None
    escaped = False
    value: list[str] = []
    index = start
    while index < len(command):
        char = command[index]
        if escaped:
            value.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            else:
                value.append(char)
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char.isspace() or char in "&|;<>\r\n":
            break
        value.append(char)
        index += 1
    return "".join(value), index


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _program(lowered_tokens: list[str]) -> str:
    if not lowered_tokens:
        return ""
    return lowered_tokens[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _network_program(lowered_tokens: list[str]) -> str | None:
    program = _program(lowered_tokens)
    if program in {"curl", "wget", "invoke-webrequest", "invoke-restmethod", "iwr", "irm"}:
        return program
    if program in {"pip", "pip3", "npm"} and "install" in lowered_tokens[1:3]:
        return f"{program}-install"
    if program == "git" and any(value in lowered_tokens[1:3] for value in {"clone", "pull", "fetch"}):
        return f"git-{lowered_tokens[1]}"
    return None


def _network_hosts(tokens: list[str]) -> list[str]:
    hosts = []
    for token in tokens:
        parsed = urlparse(token)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            hosts.append(parsed.hostname.lower())
    return hosts


def _network_output_effects(tokens: list[str], program: str) -> tuple[list[str], bool]:
    paths: list[str] = []
    writes_unknown_path = program in {
        "wget",
        "git-clone",
        "git-pull",
        "git-fetch",
        "npm-install",
        "pip-install",
        "pip3-install",
    }
    options = {"curl": {"-o", "--output"}, "wget": {"-O"}}.get(program, set())
    if program in {"invoke-webrequest", "iwr"}:
        options = {"-OutFile", "-outfile"}
    for index, token in enumerate(tokens):
        comparable = token.lower() if token.startswith("--") else token
        comparable_options = {
            value.lower() if value.startswith("--") else value for value in options
        }
        if comparable in comparable_options and index + 1 < len(tokens):
            cleaned = _clean_path(tokens[index + 1])
            if cleaned is not None:
                paths.append(cleaned)
            writes_unknown_path = False
        elif program == "curl" and token.lower().startswith("--output="):
            cleaned = _clean_path(token.split("=", 1)[1])
            if cleaned is not None:
                paths.append(cleaned)
            writes_unknown_path = False
        elif program == "curl" and token in {"-O", "--remote-name"}:
            writes_unknown_path = True
    return paths, writes_unknown_path


def _chmod_paths(tokens: list[str]) -> list[str]:
    positional = [token for token in tokens[1:] if not token.startswith("-")]
    if len(positional) <= 1:
        return []
    return [path for path in (_clean_path(value) for value in positional[1:]) if path]


def _positional_paths(tokens: list[str]) -> list[str]:
    return [path for path in (_clean_path(value) for value in tokens if not value.startswith("-")) if path]


def _clean_path(value: str) -> str | None:
    cleaned = value.strip().strip("'\"").rstrip(",)")
    if not cleaned or cleaned.lower() in {"nul", "/dev/null"}:
        return None
    if cleaned.startswith(("$", "%")):
        return None
    return cleaned


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
