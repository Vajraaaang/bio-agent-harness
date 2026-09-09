"""Official MCP SDK stdio entry point; importing never starts an event loop."""
import asyncio
import json
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from devharness_server.dispatcher import ToolDispatcher


def create_server():
    dispatcher = ToolDispatcher(allowed_root=Path.cwd().resolve())
    server = Server("devharness")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [Tool(**schema, outputSchema={"type": "object", "additionalProperties": True})
                for schema in dispatcher.get_schemas()]

    # The dispatcher validates raw JSON and records its own failures. SDK argument
    # preprocessing must not coerce values or discard unknown parameters first.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict):
        encoded = dispatcher.execute(name, arguments)
        # A tuple retains the SDK's output-schema validation for structured data.
        return [TextContent(type="text", text=encoded)], json.loads(encoded)

    return server


def main() -> None:
    async def serve():
        server = create_server()
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    asyncio.run(serve())


if __name__ == "__main__":
    main()
