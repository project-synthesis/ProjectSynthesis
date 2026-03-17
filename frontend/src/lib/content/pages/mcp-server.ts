import type { ContentPage } from '../types';

export const mcpServer: ContentPage = {
  slug: 'mcp-server',
  title: 'Optimize Without Leaving Your Editor.',
  description: 'Four MCP tools on port 8001. Add one line to .mcp.json and optimize prompts directly from Claude Code without switching context.',
  sections: [
    {
      type: 'hero',
      heading: 'OPTIMIZE WITHOUT LEAVING YOUR EDITOR.',
      subheading:
        'Four MCP tools on port 8001. Add one line to .mcp.json and the full Project Synthesis pipeline is available as a native Claude Code tool.',
    },
    {
      type: 'endpoint-list',
      groups: [
        {
          name: 'MCP Tools',
          endpoints: [
            {
              method: 'TOOL',
              path: 'synthesis_optimize',
              description: 'Run the full 3-phase pipeline (analyze → optimize → score) and return the optimized prompt with scores.',
              details: 'Params: prompt (required), strategy (optional), context (optional). Returns OptimizationResult with all phase outputs.',
            },
            {
              method: 'TOOL',
              path: 'synthesis_analyze',
              description: 'Run analysis and baseline scoring only — no optimization. Returns task type, detected weaknesses, strengths, recommended strategy, original scores, and actionable next steps.',
              details: 'Params: prompt (required). Returns AnalysisResult with task_type, weaknesses, strengths, strategy, scores.',
            },
            {
              method: 'TOOL',
              path: 'synthesis_prepare_optimization',
              description: 'Assemble the full optimize prompt + codebase context for processing by an external LLM. Supports workspace_path for roots scanning.',
              details: 'Params: prompt, strategy?, workspace_path?. Returns assembled_prompt string ready for external LLM.',
            },
            {
              method: 'TOOL',
              path: 'synthesis_save_result',
              description: 'Persist an externally optimized result. Applies heuristic bias correction to self-rated scores before storage.',
              details: 'Params: original_prompt, optimized_prompt, self_scores. Applies 0.85 passthrough discount. Returns saved OptimizationRecord.',
            },
          ],
        },
      ],
    },
    {
      type: 'code-block',
      language: 'json',
      filename: '.mcp.json',
      code: '{\n  "synthesis": {\n    "url": "http://127.0.0.1:8001/mcp"\n  }\n}',
    },
    {
      type: 'prose',
      blocks: [
        {
          heading: 'Passthrough Workflow',
          content:
            'The passthrough protocol decouples optimization from scoring. Call synthesis_prepare_optimization to assemble the full prompt with codebase context. Hand the result to any LLM — Claude, GPT-4, Gemini. Then call synthesis_save_result with the output and self-rated scores. Project Synthesis applies bias correction and persists the record, making passthrough results comparable to native pipeline results in history and analytics.',
        },
        {
          heading: 'Workspace Context',
          content:
            'synthesis_prepare_optimization accepts an optional workspace_path. When provided, the roots scanner discovers agent guidance files (CLAUDE.md, AGENTS.md, .cursorrules, etc.) and injects them as untrusted context into the assembled prompt. The codebase explorer runs semantic retrieval against the linked repository if one is configured.',
        },
      ],
    },
  ],
};
