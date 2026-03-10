import { user } from '$lib/stores/user.svelte';
import { toast } from '$lib/stores/toast.svelte';
import { trackOnboardingEvent } from '$lib/api/client';

export interface Milestone {
  id: string;
  title: string;
  description: string;
  color: string;
  celebrationText: string;
  condition: (ctx: MilestoneContext) => boolean;
}

export interface MilestoneContext {
  forgeCount: number;
  score: number | null;
  usedContext: boolean;
  strategy: string;
  repoLinked: boolean;
  allStrategiesUsed?: Set<string>;
}

export const milestones: Milestone[] = [
  {
    id: 'first-forge',
    title: 'FIRST FORGE',
    description: 'Completed your first prompt optimization',
    color: '#00e5ff',
    celebrationText: 'Your prompt engineering journey begins!',
    condition: (ctx) => ctx.forgeCount >= 1,
  },
  {
    id: 'repo-linked',
    title: 'CODEBASE CONNECTED',
    description: 'Linked a GitHub repository for context-aware optimization',
    color: '#22ff88',
    celebrationText: 'Codebase context unlocked for richer optimizations.',
    condition: (ctx) => ctx.repoLinked,
  },
  {
    id: 'context-used',
    title: 'CONTEXT MASTER',
    description: 'Used file, URL, or instruction context in a forge',
    color: '#a855f7',
    celebrationText: 'Context-enriched prompts produce better results.',
    condition: (ctx) => ctx.usedContext,
  },
  {
    id: 'score-above-8',
    title: 'HIGH SCORER',
    description: 'Achieved an optimization score of 8.0 or higher',
    color: '#fbbf24',
    celebrationText: 'Excellent prompt quality achieved!',
    condition: (ctx) => (ctx.score ?? 0) >= 8.0,
  },
  {
    id: 'five-forges',
    title: 'FORGE VETERAN',
    description: 'Completed 5 prompt optimizations',
    color: '#00d4aa',
    celebrationText: 'Experience builds expertise.',
    condition: (ctx) => ctx.forgeCount >= 5,
  },
  {
    id: 'ten-forges',
    title: 'FORGE EXPERT',
    description: 'Completed 10 prompt optimizations',
    color: '#ff8c00',
    celebrationText: 'You are a prompt engineering expert!',
    condition: (ctx) => ctx.forgeCount >= 10,
  },
  {
    id: 'all-strategies',
    title: 'STRATEGIST',
    description: 'Tried all available optimization strategies',
    color: '#7b61ff',
    celebrationText: 'Master of all frameworks!',
    condition: (ctx) => (ctx.allStrategiesUsed?.size ?? 0) >= 10,
  },
  {
    id: 'perfect-score',
    title: 'PERFECT 10',
    description: 'Achieved a perfect 10/10 optimization score',
    color: '#ff6eb4',
    celebrationText: 'Perfection achieved!',
    condition: (ctx) => (ctx.score ?? 0) >= 10,
  },
];

/**
 * Check milestone conditions and fire celebrations for newly achieved ones.
 * Returns IDs of newly achieved milestones.
 */
export function checkAndCelebrateMilestones(ctx: MilestoneContext): string[] {
  const dismissed = user.preferences.dismissedMilestones;
  const achieved: string[] = [];

  for (const milestone of milestones) {
    if (dismissed.includes(milestone.id)) continue;
    if (!milestone.condition(ctx)) continue;

    achieved.push(milestone.id);
    toast.milestone(milestone);
    user.dismissMilestone(milestone.id);
    trackOnboardingEvent('milestone_achieved', { milestone: milestone.id }).catch(() => {});
  }

  return achieved;
}
