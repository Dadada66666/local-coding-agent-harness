from __future__ import annotations

import argparse
import os

from mcp import types
from mcp.server import MCPServer


server = MCPServer("local-coding-agent-harness-test")
invocations = 0


@server.tool(structured_output=False)
def echo(text: str) -> str:
    """Return text unchanged."""
    global invocations
    invocations += 1
    return text


@server.tool(structured_output=False)
def process_id() -> str:
    """Return the fixture server process id."""
    return str(os.getpid())


@server.tool()
def structured(value: int) -> dict[str, int]:
    """Return deterministic structured data."""
    global invocations
    invocations += 1
    return {"value": value, "double": value * 2}


@server.tool(structured_output=False)
def tool_error() -> str:
    """Raise a deterministic tool error."""
    global invocations
    invocations += 1
    raise ValueError("fixture tool error")


@server.tool(structured_output=False)
def oversized(size: int) -> str:
    """Return a deterministic large text result."""
    global invocations
    invocations += 1
    return "x" * size


@server.tool(structured_output=False)
def unsupported_content() -> types.ImageContent:
    """Return a deterministic unsupported result block."""
    global invocations
    invocations += 1
    return types.ImageContent(data="fixture-image", mimeType="image/png")


@server.tool(structured_output=False)
def invocation_count() -> str:
    """Return the number of recorded fixture calls."""
    return str(invocations)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "http"), required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.transport == "stdio":
        server.run("stdio")
        return
    server.run(
        "streamable-http",
        host="127.0.0.1",
        port=args.port,
        streamable_http_path="/mcp",
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
