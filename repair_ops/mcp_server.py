"""Read-only stdio MCP server configured by its local operator."""

import argparse
from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from .fixtures import IDENTITIES, Identity, lookup_order


def create_server(identity: Identity) -> FastMCP:
    server = FastMCP("synthetic-repair-orders")

    @server.tool()
    def get_repair_order(order_id: str) -> dict:
        """Look up a fictional order within this process's configured shop scope."""
        order = lookup_order(identity, order_id)
        return asdict(order) if order else {"error": "Order unavailable"}

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-user", choices=sorted(IDENTITIES), required=True)
    options = parser.parse_args()
    create_server(IDENTITIES[options.demo_user]).run(transport="stdio")


if __name__ == "__main__":
    main()
