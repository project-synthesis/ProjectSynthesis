import { STRATEGY_HEX } from './strategy';

export interface StrategyInfo {
  id: string;
  name: string;
  fullName: string;
  description: string;
  bestFor: string[];
  example: string;
  color: string;
}

export const strategyReference: StrategyInfo[] = [
  {
    id: 'auto',
    name: 'Auto',
    fullName: 'Automatic Strategy Selection',
    description: 'The AI analyzer evaluates your prompt and selects the most effective framework based on task type, complexity, and structure. Best for most use cases.',
    bestFor: ['General prompts', 'First-time optimization', 'When unsure which strategy fits'],
    example: 'Input: "Write a function to sort an array"\nAuto selects: step-by-step (algorithmic task)',
    color: STRATEGY_HEX['auto'],
  },
  {
    id: 'CO-STAR',
    name: 'CO-STAR',
    fullName: 'Context, Objective, Style, Tone, Audience, Response',
    description: 'Structures prompts with six clear dimensions: Context (background), Objective (task), Style (writing approach), Tone (emotional quality), Audience (reader), Response (format). Produces comprehensive, well-framed prompts.',
    bestFor: ['Content creation', 'System prompts', 'Marketing copy', 'User-facing text'],
    example: 'Before: "Write about our product"\nAfter: "[Context] SaaS analytics platform [Objective] Product page hero copy [Style] Professional [Tone] Confident [Audience] CTOs [Response] 3 headline variants"',
    color: STRATEGY_HEX['CO-STAR'],
  },
  {
    id: 'RISEN',
    name: 'RISEN',
    fullName: 'Role, Instructions, Steps, End goal, Narrowing',
    description: 'Defines a clear role, gives precise instructions, outlines steps, specifies the end goal, and narrows scope with constraints. Excellent for task-oriented prompts with clear deliverables.',
    bestFor: ['Process documentation', 'Report generation', 'Structured workflows', 'Multi-step tasks'],
    example: 'Before: "Summarize this report"\nAfter: "[Role] Business analyst [Instructions] Extract KPIs [Steps] 1. Scan metrics 2. Identify trends 3. Summarize [End goal] Executive brief [Narrowing] Max 200 words"',
    color: STRATEGY_HEX['RISEN'],
  },
  {
    id: 'chain-of-thought',
    name: 'Chain of Thought',
    fullName: 'Chain-of-Thought Reasoning',
    description: 'Instructs the model to reason step-by-step before answering. Forces explicit reasoning chains that improve accuracy on complex problems requiring logic, math, or multi-step analysis.',
    bestFor: ['Complex reasoning', 'Math problems', 'Code debugging', 'Research analysis', 'Decision-making'],
    example: 'Before: "Is this code correct?"\nAfter: "Analyze this code step by step: 1) Trace the logic flow 2) Check edge cases 3) Verify correctness 4) Provide your conclusion with reasoning"',
    color: STRATEGY_HEX['chain-of-thought'],
  },
  {
    id: 'few-shot-scaffolding',
    name: 'Few-Shot Scaffolding',
    fullName: 'Example-Based Learning',
    description: 'Provides 2-3 input/output examples that demonstrate the desired format and behavior. The model learns the pattern from examples and applies it to new inputs consistently.',
    bestFor: ['Classification tasks', 'Data transformation', 'Consistent formatting', 'Pattern recognition'],
    example: 'Before: "Classify this review"\nAfter: "Classify sentiment:\nExample 1: \'Great product!\' -> positive\nExample 2: \'Terrible service\' -> negative\nNow classify: \'Works okay but pricey\' ->"',
    color: STRATEGY_HEX['few-shot-scaffolding'],
  },
  {
    id: 'role-task-format',
    name: 'Role-Task-Format',
    fullName: 'Role Assignment with Task and Output Format',
    description: 'Assigns a specific expert role, defines the task clearly, and specifies the exact output format. Simple but effective for straightforward tasks that need expert-level quality.',
    bestFor: ['Expert consultations', 'Code reviews', 'Technical writing', 'Professional advice'],
    example: 'Before: "Review my API design"\nAfter: "[Role] Senior API architect [Task] Review this REST API for best practices [Format] Table with issue, severity, fix for each finding"',
    color: STRATEGY_HEX['role-task-format'],
  },
  {
    id: 'structured-output',
    name: 'Structured Output',
    fullName: 'Schema-Driven Response Formatting',
    description: 'Defines a precise output schema (JSON, table, markdown structure) that the model must follow. Ensures machine-parseable, consistent responses ideal for pipelines and automation.',
    bestFor: ['API responses', 'Data extraction', 'Test generation', 'Documentation', 'Automation pipelines'],
    example: 'Before: "List the errors"\nAfter: "Return errors as JSON: [{\"line\": number, \"type\": string, \"message\": string, \"fix\": string}]"',
    color: STRATEGY_HEX['structured-output'],
  },
  {
    id: 'step-by-step',
    name: 'Step-by-Step',
    fullName: 'Sequential Task Decomposition',
    description: 'Breaks complex tasks into numbered sequential steps with clear transitions. Each step builds on the previous one, creating a logical progression toward the final output.',
    bestFor: ['Tutorials', 'Algorithms', 'Setup guides', 'Debugging workflows', 'Onboarding docs'],
    example: 'Before: "How to deploy to AWS"\nAfter: "Step 1: Configure IAM role Step 2: Create ECR repo Step 3: Build Docker image Step 4: Push to ECR Step 5: Deploy via ECS"',
    color: STRATEGY_HEX['step-by-step'],
  },
  {
    id: 'constraint-injection',
    name: 'Constraint Injection',
    fullName: 'Boundary and Rule Enforcement',
    description: 'Adds explicit constraints, guardrails, and boundaries to the prompt. Controls output length, format, forbidden topics, required elements, and edge case handling.',
    bestFor: ['Safety-critical prompts', 'Compliance content', 'Data extraction', 'Controlled generation'],
    example: 'Before: "Describe the product"\nAfter: "Describe the product. Constraints: max 100 words, no superlatives, include price, mention 3 features, use present tense only"',
    color: STRATEGY_HEX['constraint-injection'],
  },
  {
    id: 'context-enrichment',
    name: 'Context Enrichment',
    fullName: 'Background Context Augmentation',
    description: 'Enriches the prompt with relevant background information, domain knowledge, and contextual details that improve response quality. Bridges the gap between what the user knows and what the model needs.',
    bestFor: ['Domain-specific tasks', 'Technical writing', 'Creative content', 'Research tasks'],
    example: 'Before: "Write about React hooks"\nAfter: "[Context] React 18+, audience: mid-level devs migrating from class components, focus on useState/useEffect patterns [Task] Write tutorial"',
    color: STRATEGY_HEX['context-enrichment'],
  },
  {
    id: 'persona-assignment',
    name: 'Persona Assignment',
    fullName: 'Character and Expertise Definition',
    description: 'Creates a detailed persona with expertise, communication style, knowledge boundaries, and behavioral rules. Goes beyond simple role assignment to define how the model should think and respond.',
    bestFor: ['Chatbots', 'Customer support', 'Educational tools', 'Roleplay scenarios', 'Specialized advisors'],
    example: 'Before: "Be a doctor"\nAfter: "You are Dr. Chen, a board-certified cardiologist with 15 years of experience. You explain conditions using simple analogies, always recommend consulting a specialist, and never diagnose from symptoms alone."',
    color: STRATEGY_HEX['persona-assignment'],
  },
];

export function getStrategyInfo(id: string): StrategyInfo | undefined {
  return strategyReference.find(s => s.id === id || s.id === id.toLowerCase());
}

// ── Pipeline stage reference (shared by OnboardingWizard + WelcomeTab) ──

export interface PipelineStage {
  name: string;
  desc: string;
  color: string;
}

export const pipelineStages: PipelineStage[] = [
  { name: 'Explore', desc: 'Reads your linked GitHub repo for context', color: '#a855f7' },
  { name: 'Analyze', desc: 'Classifies prompt type, complexity, and weaknesses', color: '#00e5ff' },
  { name: 'Strategy', desc: 'Selects the optimal framework for your prompt', color: '#22ff88' },
  { name: 'Optimize', desc: 'Rewrites your prompt using the selected strategy', color: '#fbbf24' },
  { name: 'Validate', desc: 'Scores clarity, specificity, structure, faithfulness', color: '#ff3366' },
];
