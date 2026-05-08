#!/usr/bin/env python3
"""
ONTAP CLI MCP Server
Executes ONTAP CLI commands on lab clusters via SSH (paramiko).
Credentials are passed per-call — no local storage required.
"""

import sys
import time
import json
import asyncio
import paramiko
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

server = Server("ontap-mcp")


def _exec_command(host: str, username: str, password: str,
                  command: str, timeout: int = 60) -> str:
    """SSH exec_command — for standard ONTAP CLI (non-interactive)."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=username, password=password,
                   timeout=15, banner_timeout=30)
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return (out + err).strip()
    finally:
        client.close()


def _interactive_session(host: str, username: str, password: str,
                         steps: list[dict]) -> str:
    """
    SSH invoke_shell with PTY — for SP/BMC/LOADER interactive sessions.

    Each step:
      { "send": "<text to send>", "wait_for": "<expected string>", "timeout": 60 }

    Returns full transcript of the session.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=username, password=password,
                   timeout=15, banner_timeout=30)

    channel = client.get_transport().open_session()
    channel.get_pty(width=220, height=50)
    channel.invoke_shell()
    time.sleep(1)

    transcript = []

    def _read_until(wait_for: str, timeout: int) -> str:
        buf = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if channel.recv_ready():
                chunk = channel.recv(65536).decode("utf-8", errors="replace")
                buf += chunk
                if wait_for and wait_for in buf:
                    break
            else:
                time.sleep(0.2)
        return buf

    try:
        for step in steps:
            send_text = step.get("send", "")
            wait_for = step.get("wait_for", "")
            timeout = int(step.get("timeout", 60))

            if send_text:
                channel.send(send_text + "\n")
                transcript.append(f">>> {send_text}")

            output = _read_until(wait_for, timeout)
            transcript.append(output)

    finally:
        channel.close()
        client.close()

    return "\n".join(transcript)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="ontap_cli",
            description=(
                "Execute one or more ONTAP CLI commands on a lab cluster via SSH. "
                "Use this for all standard ONTAP commands (volume show, network interface show, etc.). "
                "Multiple commands can be chained with ' ; ' or sent as separate calls."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "host":     {"type": "string", "description": "Cluster management IP or hostname"},
                    "username": {"type": "string", "description": "SSH username (e.g. admin)"},
                    "password": {"type": "string", "description": "SSH password"},
                    "command":  {"type": "string", "description": "ONTAP CLI command to run"},
                    "timeout":  {"type": "integer", "description": "Command timeout in seconds (default: 60)", "default": 60},
                },
                "required": ["host", "username", "password", "command"],
            },
        ),
        types.Tool(
            name="ontap_shell",
            description=(
                "Run an interactive SSH shell session with PTY — required for SP/BMC console, "
                "LOADER prompt, boot menu, or cluster setup wizard. "
                "Provide a list of steps: each step sends text and waits for an expected string. "
                "Example step: {\"send\": \"system console\", \"wait_for\": \"LOADER>\", \"timeout\": 120}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "host":     {"type": "string", "description": "SP/BMC IP or node management IP"},
                    "username": {"type": "string", "description": "SSH username"},
                    "password": {"type": "string", "description": "SSH password (use empty string '' for post-option4 SP)"},
                    "steps": {
                        "type": "array",
                        "description": "Ordered list of send/wait steps",
                        "items": {
                            "type": "object",
                            "properties": {
                                "send":     {"type": "string", "description": "Text to send (omit or empty to only read)"},
                                "wait_for": {"type": "string", "description": "String to wait for before proceeding"},
                                "timeout":  {"type": "integer", "description": "Seconds to wait (default: 60)"},
                            },
                        },
                    },
                },
                "required": ["host", "username", "steps"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "ontap_cli":
            result = _exec_command(
                host=arguments["host"],
                username=arguments["username"],
                password=arguments["password"],
                command=arguments["command"],
                timeout=arguments.get("timeout", 60),
            )
            return [types.TextContent(type="text", text=result)]

        elif name == "ontap_shell":
            result = _interactive_session(
                host=arguments["host"],
                username=arguments["username"],
                password=arguments.get("password", ""),
                steps=arguments["steps"],
            )
            return [types.TextContent(type="text", text=result)]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except paramiko.AuthenticationException:
        return [types.TextContent(type="text", text="Error: SSH authentication failed — check username/password")]
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]


async def main():
    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
