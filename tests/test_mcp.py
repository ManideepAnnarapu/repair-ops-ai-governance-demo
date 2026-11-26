import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_mcp_stdio_lookup_respects_operator_scope():
    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "repair_ops.mcp_server", "--demo-user", "cedar-writer"],
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                assert [tool.name for tool in (await client.list_tools()).tools] == [
                    "get_repair_order"
                ]
                for order_id in ("RO-101", "RO-201", "RO-999"):
                    result = await client.call_tool("get_repair_order", {"order_id": order_id})
                    assert not result.isError
                    data = json.loads(result.content[0].text)
                    if order_id == "RO-101":
                        assert data["shop"] == "Cedar Arc"
                        assert data["id"] == order_id
                    else:
                        assert data == {"error": "Order unavailable"}

    asyncio.run(asyncio.wait_for(check(), timeout=15))
