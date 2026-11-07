import importlib.util
import json
import sys
import unittest


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install the test extra for official MCP SDK interoperability checks')
class OfficialMCPClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_discover_and_call_through_real_stdio(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=sys.executable, args=['-m', 'freeciv_agent', 'mcp'])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                result = await client.initialize()
                self.assertEqual(result.serverInfo.name, 'freeciv-agent-harness')
                tools = await client.list_tools()
                self.assertGreaterEqual(len(tools.tools), 24)
                status = await client.call_tool('freeciv_status', {})
                self.assertFalse(status.isError)
                self.assertFalse(json.loads(status.content[0].text)['joined'])
                invalid = await client.call_tool('freeciv_end_turn', {'expected_turn': 0})
                self.assertTrue(invalid.isError)


if __name__ == '__main__':
    unittest.main()
