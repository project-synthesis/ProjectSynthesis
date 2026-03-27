# MCP Copilot Bridge

This is a VS Code extension that bridges a standard HTTP MCP server (Synthesis MCP) into the VS Code Language Model Tools API (`vscode.lm.registerTool`).

## Requirements
- VS Code 1.96.0+
- The Synthesis MCP server running at `http://127.0.0.1:8001/mcp` (must support SSE transport).

## How it works
This extension uses the `@modelcontextprotocol/sdk` to connect to your MCP server. It listens to Copilot natively discovering its `synthesis_analyze` tool via `package.json`, and invokes it by passing the arguments to `client.callTool()`.

## Installation & Running
1. `npm install`
2. Open this folder in VS Code
3. Press **F5** to start debugging the extension.
4. In the Extension Development Host, you can now open GitHub Copilot Chat, click the `+` Tool icon, and see `synthesis_analyze` natively!
