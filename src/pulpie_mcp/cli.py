"""Command line entry point.

  pulpie-mcp            run the MCP server over stdio (what MCP clients launch)
  pulpie-mcp serve      run the model backend in the foreground
  pulpie-mcp status     show whether the backend is running
  pulpie-mcp stop       ask a running backend to exit
"""

from __future__ import annotations

import argparse
import asyncio
import json

from pulpie_mcp import config


def main() -> None:
    parser = argparse.ArgumentParser(prog="pulpie-mcp", description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=config.app_version())
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the model backend in the foreground")
    host, port = config.backend_host_port()
    serve.add_argument("--host", default=host)
    serve.add_argument("--port", type=int, default=port)
    sub.add_parser("status", help="show backend status")
    sub.add_parser("stop", help="stop the running backend")
    args = parser.parse_args()

    if args.command == "serve":
        from pulpie_mcp.backend.app import serve as run_backend

        run_backend(args.host, args.port)
    elif args.command == "status":
        from pulpie_mcp.launcher import health

        info = asyncio.run(health())
        if info is None:
            print(f"Backend not running at {config.backend_url()}")
            raise SystemExit(1)
        print(json.dumps(info, indent=2))
    elif args.command == "stop":
        from pulpie_mcp.launcher import stop_backend

        stopped = asyncio.run(stop_backend())
        print("Backend stopping." if stopped else f"No backend running at {config.backend_url()}")
    else:
        from pulpie_mcp.server import mcp

        mcp.run()
