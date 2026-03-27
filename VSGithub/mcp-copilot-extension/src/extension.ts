import * as vscode from 'vscode';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { SSEClientTransport } from '@modelcontextprotocol/sdk/client/sse.js';
import { CreateMessageRequestSchema, ListRootsRequestSchema } from '@modelcontextprotocol/sdk/types.js';

const MCP_URL = "http://127.0.0.1:8001/mcp";
const HEALTH_INTERVAL_MS = 10_000;  // Check connection every 10s
const RECONNECT_DELAY_MS = 5_000;
const MAX_INITIAL_ATTEMPTS = 3;

/**
 * MCP Copilot Bridge — connects to the Synthesis MCP server, dynamically
 * discovers all tools, registers them with VS Code's Language Model API,
 * and handles sampling requests by forwarding to VS Code's LM API.
 *
 * Auto-reconnects when the MCP server restarts (health check every 30s).
 */
export async function activate(context: vscode.ExtensionContext) {
    const output = vscode.window.createOutputChannel('MCP Copilot Bridge');
    const bridge = new McpBridge(context, output);
    context.subscriptions.push({ dispose: () => bridge.dispose() });
    await bridge.start();
}

class McpBridge {
    private context: vscode.ExtensionContext;
    private output: vscode.OutputChannel;
    private client: Client | null = null;
    private healthTimer: ReturnType<typeof setInterval> | null = null;
    private disposed = false;
    private toolHandles: vscode.Disposable[] = [];

    constructor(context: vscode.ExtensionContext, output: vscode.OutputChannel) {
        this.context = context;
        this.output = output;
    }

    async start(): Promise<void> {
        this.log('Activating MCP Copilot Bridge...');

        // Initial connection with retries
        let attempt = 0;
        while (attempt < MAX_INITIAL_ATTEMPTS && !this.disposed) {
            try {
                await this.connect();
                this.startHealthCheck();
                return;
            } catch (e: any) {
                attempt++;
                this.log(`Attempt ${attempt}/${MAX_INITIAL_ATTEMPTS} failed: ${e.message}`);
                if (attempt < MAX_INITIAL_ATTEMPTS) {
                    await this.sleep(RECONNECT_DELAY_MS);
                }
            }
        }

        this.log('Initial connection failed. Will keep trying via health check...');
        // Start health check anyway — it will keep trying to connect
        this.startHealthCheck();
    }

    private async connect(): Promise<void> {
        // Clean up previous connection
        this.disposeTools();
        if (this.client) {
            try { await this.client.close(); } catch { /* ignore */ }
            this.client = null;
        }

        const url = new URL(MCP_URL);
        let client: Client;

        // Try StreamableHTTP first, fall back to SSE
        try {
            this.log(`Connecting via StreamableHTTP → ${MCP_URL}`);
            const transport = new StreamableHTTPClientTransport(url);
            client = new Client(
                { name: "mcp-copilot-bridge", version: "2.0.0" },
                { capabilities: { sampling: {}, roots: { listChanged: false } } }
            );
            await client.connect(transport);
            this.log('Connected via StreamableHTTP');
        } catch (e: any) {
            this.log(`StreamableHTTP failed (${e.message}), trying SSE...`);
            const sseTransport = new SSEClientTransport(url);
            client = new Client(
                { name: "mcp-copilot-bridge", version: "2.0.0" },
                { capabilities: { sampling: {}, roots: { listChanged: false } } }
            );
            await client.connect(sseTransport);
            this.log('Connected via SSE');
        }

        this.client = client;

        // Register sampling handler
        this.registerSamplingHandler(client);

        // Register roots handler — returns VS Code workspace folders
        // so the MCP server can scan for CLAUDE.md, AGENTS.md, etc.
        client.setRequestHandler(ListRootsRequestSchema, async () => {
            const folders = vscode.workspace.workspaceFolders || [];
            const roots = folders.map(f => ({
                uri: f.uri.toString(),
                name: f.name,
            }));
            this.log(`Roots requested: ${roots.length} folders → ${roots.map(r => r.name).join(', ')}`);
            return { roots };
        });

        // Discover and register tools
        const { tools } = await client.listTools();
        this.log(`Discovered ${tools.length} tools: ${tools.map(t => t.name).join(', ')}`);

        let registered = 0;
        for (const tool of tools) {
            try {
                const handle = this.registerTool(client, tool.name);
                this.toolHandles.push(handle);
                this.context.subscriptions.push(handle);
                registered++;
            } catch (e: any) {
                this.log(`FAILED to register ${tool.name}: ${e.message}`);
            }
        }

        this.log(`Registered ${registered}/${tools.length} tools with sampling support`);
        vscode.window.showInformationMessage(
            `MCP Copilot Bridge: ${registered} tools + sampling`
        );
    }

    private registerTool(client: Client, toolName: string): vscode.Disposable {
        return vscode.lm.registerTool(toolName, {
            invoke: async (
                options: vscode.LanguageModelToolInvocationOptions<any>,
                _token: vscode.CancellationToken
            ) => {
                try {
                    const result = await client.callTool({
                        name: toolName,
                        arguments: options.input || {}
                    });
                    const textParts = (result as any).content
                        .filter((c: any) => c.type === 'text')
                        .map((c: any) => new vscode.LanguageModelTextPart(c.text));
                    return new vscode.LanguageModelToolResult(textParts);
                } catch (e: any) {
                    this.log(`Tool call failed: ${toolName}: ${e.message}`);
                    return new vscode.LanguageModelToolResult([
                        new vscode.LanguageModelTextPart(`Error calling ${toolName}: ${e.message}`)
                    ]);
                }
            },
            prepareInvocation: async () => {
                return { invocationMessage: `Invoking ${toolName} via Synthesis MCP...` };
            }
        });
    }

    /**
     * Schemas that produce free-form markdown (NOT JSON).
     * For these, we skip the JSON schema instruction to avoid degrading
     * output quality — the LLM should write rich markdown directly.
     */
    private static readonly FREE_TEXT_SCHEMAS = new Set([
        'OptimizationResult',    // optimize phase — produces the actual prompt
        'SuggestionsOutput',     // suggest phase — produces improvement suggestions
    ]);

    /**
     * Detect which pipeline phase this sampling request is for,
     * based on the tool schema title.
     */
    private static detectPhase(params: any): { name: string; needsJson: boolean } {
        if (!params.tools?.length) return { name: 'unknown', needsJson: false };
        const title: string = params.tools[0].inputSchema?.title || '';
        const schemaName = title.replace(/Arguments$/, '');
        const needsJson = !McpBridge.FREE_TEXT_SCHEMAS.has(schemaName);
        return { name: schemaName, needsJson };
    }

    private registerSamplingHandler(client: Client): void {
        client.setRequestHandler(CreateMessageRequestSchema, async (request) => {
            const params = request.params;
            const hasTools = params.tools && params.tools.length > 0;
            const hasSystem = !!params.systemPrompt;
            const phase = McpBridge.detectPhase(params);

            this.log(
                `Sampling [${phase.name}]: system=${hasSystem} tools=${hasTools} ` +
                `needsJson=${phase.needsJson} maxTokens=${params.maxTokens ?? 'default'} ` +
                `model=${params.modelPreferences?.hints?.[0]?.name ?? 'default'}`
            );

            const startTime = Date.now();
            try {
                const models = await vscode.lm.selectChatModels();
                if (models.length === 0) {
                    this.log(`ERROR [${phase.name}]: No language models available`);
                    throw new Error('No language models available in VS Code');
                }
                const model = models[0];
                this.log(`  Using model: ${model.id}`);

                const messages: vscode.LanguageModelChatMessage[] = [];

                // Prepend system prompt as first message (VS Code has no system role)
                if (params.systemPrompt) {
                    messages.push(vscode.LanguageModelChatMessage.User(
                        `<system-instructions>\n${params.systemPrompt}\n</system-instructions>`
                    ));
                    messages.push(vscode.LanguageModelChatMessage.Assistant(
                        'Understood. I will follow these instructions precisely.'
                    ));
                    this.log(`  System prompt: ${params.systemPrompt.length} chars`);
                }

                // Convert MCP messages
                for (const msg of params.messages) {
                    const textContent = typeof msg.content === 'string'
                        ? msg.content
                        : (msg.content as any)?.text ?? JSON.stringify(msg.content);
                    this.log(
                        `  msg[${msg.role}]: ${textContent.length} chars` +
                        (textContent.includes('Project Synthesis') ? ' [has workspace context]' : '')
                    );
                    if (msg.role === 'user') {
                        messages.push(vscode.LanguageModelChatMessage.User(textContent));
                    } else {
                        messages.push(vscode.LanguageModelChatMessage.Assistant(textContent));
                    }
                }

                // Append JSON schema instruction ONLY for phases that need structured output
                // (analyze, score). The optimize and suggest phases produce free-form markdown
                // — forcing JSON degrades quality and causes code-fence wrapping artifacts.
                if (hasTools && phase.needsJson) {
                    const schema = params.tools![0].inputSchema;
                    const schemaInstruction =
                        '\n\n---\nIMPORTANT: You MUST respond with ONLY a valid JSON object ' +
                        'matching this exact schema. No markdown fences, no commentary, ' +
                        'no explanation — just the raw JSON:\n' +
                        JSON.stringify(schema, null, 2);

                    const lastUserIdx = findLastIndex(messages, m =>
                        m.role === vscode.LanguageModelChatMessageRole.User
                    );
                    if (lastUserIdx >= 0) {
                        const orig = messages[lastUserIdx];
                        const origText = (orig as any).content?.map?.((p: any) => p.value ?? p.text ?? '')?.join('')
                            ?? (orig as any).content ?? '';
                        messages[lastUserIdx] = vscode.LanguageModelChatMessage.User(
                            origText + schemaInstruction
                        );
                    }
                    this.log(`  JSON schema injected for ${phase.name}`);
                } else if (hasTools) {
                    this.log(`  Skipping JSON schema for ${phase.name} (free-text phase)`);
                }

                const options: any = {
                    justification: 'MCP sampling request from Synthesis server',
                };
                if (params.maxTokens) {
                    options.maxOutputTokens = params.maxTokens;
                }

                // 90s timeout (under MCP server's 120s)
                const cts = new vscode.CancellationTokenSource();
                const timeoutHandle = setTimeout(() => {
                    this.log(`TIMEOUT [${phase.name}]: 90s exceeded, canceling request`);
                    cts.cancel();
                }, 90_000);

                let response;
                try {
                    response = await model.sendRequest(messages, options, cts.token);
                } catch (sendErr: any) {
                    clearTimeout(timeoutHandle);
                    this.log(`ERROR [${phase.name}]: sendRequest failed: ${sendErr.message}`);
                    throw sendErr;
                } finally {
                    clearTimeout(timeoutHandle);
                }

                // Collect response (streaming)
                let responseText = '';
                let chunks = 0;
                try {
                    for await (const chunk of response.text) {
                        responseText += chunk;
                        chunks++;
                    }
                } catch (streamErr: any) {
                    this.log(`ERROR [${phase.name}]: Stream interrupted after ${chunks} chunks (${responseText.length} chars): ${streamErr.message}`);
                    if (responseText.length > 0) {
                        this.log(`  Returning partial response (${responseText.length} chars)`);
                    } else {
                        throw streamErr;
                    }
                }

                const elapsed = Date.now() - startTime;
                this.log(
                    `OK [${phase.name}]: ${responseText.length} chars, ` +
                    `${chunks} chunks, ${elapsed}ms via ${model.id}`
                );

                return {
                    model: model.id,
                    role: 'assistant' as const,
                    content: { type: 'text' as const, text: responseText },
                };
            } catch (e: any) {
                const elapsed = Date.now() - startTime;
                this.log(`FAIL [${phase.name}]: ${e.message} (${elapsed}ms)`);
                throw e;
            }
        });
    }

    // ── Health check + auto-reconnect ────────────────────────────────

    private startHealthCheck(): void {
        if (this.healthTimer) return;
        this.healthTimer = setInterval(() => this.checkHealth(), HEALTH_INTERVAL_MS);
        this.log(`Health check started (every ${HEALTH_INTERVAL_MS / 1000}s)`);
    }

    private async checkHealth(): Promise<void> {
        if (this.disposed) return;

        if (!this.client) {
            // No connection — try to establish one
            this.log('No active connection, attempting reconnect...');
            try {
                await this.connect();
                this.log('Reconnected successfully');
            } catch (e: any) {
                this.log(`Reconnect failed: ${e.message}`);
            }
            return;
        }

        // Probe the server with a lightweight tool call
        try {
            await this.client.listTools();
            // Connection alive — nothing to do
        } catch (e: any) {
            this.log(`Health check failed (${e.message}) — server may have restarted. Reconnecting...`);
            try {
                await this.connect();
                this.log('Reconnected after health check failure');
            } catch (reconnectErr: any) {
                this.log(`Reconnect failed: ${reconnectErr.message}`);
                this.client = null; // Mark as disconnected; next health check will retry
            }
        }
    }

    // ── Cleanup ──────────────────────────────────────────────────────

    private disposeTools(): void {
        for (const handle of this.toolHandles) {
            try { handle.dispose(); } catch { /* ignore */ }
        }
        this.toolHandles = [];
    }

    dispose(): void {
        this.disposed = true;
        if (this.healthTimer) {
            clearInterval(this.healthTimer);
            this.healthTimer = null;
        }
        this.disposeTools();
        if (this.client) {
            try { this.client.close(); } catch { /* ignore */ }
            this.client = null;
        }
    }

    private log(msg: string): void {
        this.output.appendLine(`[${ts()}] ${msg}`);
    }

    private sleep(ms: number): Promise<void> {
        return new Promise(r => setTimeout(r, ms));
    }
}

function ts(): string {
    return new Date().toISOString().slice(11, 23);
}

function findLastIndex<T>(arr: T[], predicate: (item: T) => boolean): number {
    for (let i = arr.length - 1; i >= 0; i--) {
        if (predicate(arr[i])) return i;
    }
    return -1;
}

export function deactivate() {}
