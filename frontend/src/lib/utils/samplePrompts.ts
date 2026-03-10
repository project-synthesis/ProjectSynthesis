export interface SamplePrompt {
  id: string;
  title: string;
  category: 'system' | 'instruction' | 'analysis' | 'creative' | 'code';
  description: string;
  text: string;
  suggestedStrategy: string;
  difficulty: 'beginner' | 'intermediate' | 'advanced';
}

export const samplePrompts: SamplePrompt[] = [
  // ── System Prompts ─────────────────────────────────────────────────
  {
    id: 'sys-assistant',
    title: 'AI Assistant Persona',
    category: 'system',
    description: 'Define a helpful AI assistant with clear boundaries and behavior guidelines.',
    text: `You are a helpful AI assistant. Answer user questions accurately and concisely. If you don't know the answer, say so rather than guessing. Provide sources when making factual claims. Keep responses under 300 words unless the user asks for detail.`,
    suggestedStrategy: 'CO-STAR',
    difficulty: 'beginner',
  },
  {
    id: 'sys-code-review',
    title: 'Code Review Bot',
    category: 'system',
    description: 'System prompt for an automated code review agent that checks for bugs and style.',
    text: `You are a senior code reviewer. For each code snippet submitted, analyze it for: bugs and logic errors, security vulnerabilities, performance issues, code style and readability. Provide specific line-level feedback with suggested fixes. Rate the overall code quality from 1-10.`,
    suggestedStrategy: 'role-task-format',
    difficulty: 'intermediate',
  },
  {
    id: 'sys-support',
    title: 'Customer Support Agent',
    category: 'system',
    description: 'Support agent that follows escalation protocols and maintains brand tone.',
    text: `You are a customer support agent for a SaaS platform. Follow these rules: greet the customer warmly, identify their issue category (billing, technical, account), attempt resolution using the knowledge base, escalate to a human agent if unresolved after 3 exchanges. Always maintain a professional, empathetic tone.`,
    suggestedStrategy: 'persona-assignment',
    difficulty: 'intermediate',
  },

  // ── Instructions ───────────────────────────────────────────────────
  {
    id: 'inst-api-docs',
    title: 'API Documentation Generator',
    category: 'instruction',
    description: 'Generate OpenAPI-style documentation from code or endpoint descriptions.',
    text: `Given a list of API endpoints, generate comprehensive documentation for each one. Include: HTTP method and path, description, request parameters (query, path, body) with types, response schema with example JSON, error codes, and authentication requirements. Format as markdown.`,
    suggestedStrategy: 'structured-output',
    difficulty: 'intermediate',
  },
  {
    id: 'inst-extract',
    title: 'Data Extraction Pipeline',
    category: 'instruction',
    description: 'Extract structured data from unstructured text documents.',
    text: `Extract the following fields from the provided document: company name, contact person, email, phone number, date, total amount, line items with quantities and prices. Return the result as a JSON object. If a field is not found, set it to null.`,
    suggestedStrategy: 'constraint-injection',
    difficulty: 'beginner',
  },
  {
    id: 'inst-report',
    title: 'Weekly Report Writer',
    category: 'instruction',
    description: 'Transform bullet points and metrics into a polished executive summary.',
    text: `You will receive a set of bullet points describing this week's accomplishments, blockers, and metrics. Transform them into a professional weekly report with these sections: Executive Summary (2-3 sentences), Key Accomplishments, Challenges & Mitigations, Metrics Dashboard, Next Week Priorities. Keep the tone professional but accessible.`,
    suggestedStrategy: 'RISEN',
    difficulty: 'beginner',
  },

  // ── Analysis ───────────────────────────────────────────────────────
  {
    id: 'analysis-research',
    title: 'User Research Synthesizer',
    category: 'analysis',
    description: 'Synthesize interview transcripts into actionable product insights.',
    text: `Analyze the following user interview transcripts. Identify: common pain points (ranked by frequency), feature requests with priority scores, user personas that emerge from the data, quotes that best illustrate each finding. Present findings in a structured report with an executive summary.`,
    suggestedStrategy: 'chain-of-thought',
    difficulty: 'advanced',
  },
  {
    id: 'analysis-competitive',
    title: 'Competitive Analysis',
    category: 'analysis',
    description: 'Compare products across features, pricing, and market positioning.',
    text: `Compare the following products across these dimensions: core features, pricing tiers, target audience, unique selling points, weaknesses. Create a comparison matrix and provide a strategic recommendation for positioning against each competitor.`,
    suggestedStrategy: 'step-by-step',
    difficulty: 'intermediate',
  },
  {
    id: 'analysis-sentiment',
    title: 'Sentiment Classifier',
    category: 'analysis',
    description: 'Classify text sentiment with confidence scores and reasoning.',
    text: `Classify the sentiment of each customer review as positive, negative, or neutral. For each classification, provide: sentiment label, confidence score (0-1), key phrases that influenced the classification, any mixed signals detected. Return results as a JSON array.`,
    suggestedStrategy: 'few-shot-scaffolding',
    difficulty: 'beginner',
  },

  // ── Code ───────────────────────────────────────────────────────────
  {
    id: 'code-refactor',
    title: 'Refactoring Advisor',
    category: 'code',
    description: 'Analyze code for refactoring opportunities with specific improvement suggestions.',
    text: `Review the following code and identify refactoring opportunities. For each suggestion: describe the code smell or issue, explain why it should be refactored, provide the refactored code, estimate the effort level (low/medium/high). Prioritize suggestions by impact on maintainability.`,
    suggestedStrategy: 'chain-of-thought',
    difficulty: 'advanced',
  },
  {
    id: 'code-tests',
    title: 'Test Generator',
    category: 'code',
    description: 'Generate unit tests with edge cases, mocks, and assertions.',
    text: `Generate comprehensive unit tests for the following function. Include: happy path tests, edge cases (empty input, null values, boundary conditions), error handling tests, mock setup for external dependencies. Use descriptive test names that explain the expected behavior. Target 90%+ code coverage.`,
    suggestedStrategy: 'structured-output',
    difficulty: 'intermediate',
  },

  // ── Creative ───────────────────────────────────────────────────────
  {
    id: 'creative-blog',
    title: 'Technical Blog Writer',
    category: 'creative',
    description: 'Write engaging technical blog posts from outlines or concepts.',
    text: `Write a technical blog post about the given topic. Structure it with: an attention-grabbing introduction, clear problem statement, solution walkthrough with code examples, practical tips and gotchas, conclusion with next steps. Target 1500 words, use a conversational but authoritative tone, and include relevant analogies for complex concepts.`,
    suggestedStrategy: 'context-enrichment',
    difficulty: 'intermediate',
  },
  {
    id: 'creative-product',
    title: 'Product Description Optimizer',
    category: 'creative',
    description: 'Optimize product descriptions for conversion and SEO.',
    text: `Rewrite the following product description to maximize conversion. Include: a compelling headline, key benefits (not just features), social proof elements, urgency or scarcity cues, clear call-to-action, SEO-optimized keywords naturally integrated. Keep under 200 words while maintaining all essential information.`,
    suggestedStrategy: 'CO-STAR',
    difficulty: 'beginner',
  },
];

export const promptCategories = ['system', 'instruction', 'analysis', 'code', 'creative'] as const;

export const categoryLabels: Record<string, string> = {
  system: 'System Prompts',
  instruction: 'Instructions',
  analysis: 'Analysis',
  code: 'Code',
  creative: 'Creative',
};

export const categoryColors: Record<string, string> = {
  system: '#00e5ff',
  instruction: '#22ff88',
  analysis: '#a855f7',
  code: '#fbbf24',
  creative: '#ff6eb4',
};

export const difficultyColors: Record<string, string> = {
  beginner: '#22ff88',
  intermediate: '#fbbf24',
  advanced: '#ff3366',
};
