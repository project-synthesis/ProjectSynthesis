import { commandPalette } from '$lib/stores/commandPalette.svelte';

export interface WalkthroughStep {
  target: string;
  title: string;
  description: string;
  position: 'top' | 'bottom' | 'left' | 'right';
  action?: () => void;
}

const defaultSteps: WalkthroughStep[] = [
  {
    target: 'nav[aria-label="Activity Bar"]',
    title: 'Activity Bar',
    description: 'Switch between Files, History, Templates, GitHub, and Settings.',
    position: 'right',
  },
  {
    target: 'nav[aria-label="Navigator"]',
    title: 'Navigator',
    description: 'Browse your prompts, history, templates, and connected repos.',
    position: 'right',
  },
  {
    target: '[data-tour="editor"]',
    title: 'Editor',
    description: 'Write your prompt here. Use @ to add context from files, repos, or URLs.',
    position: 'bottom',
  },
  {
    target: '[data-tour="strategy"]',
    title: 'Strategy Selector',
    description: 'Choose a prompt framework or let auto-select pick the best one.',
    position: 'top',
  },
  {
    target: '[data-testid="forge-button"]',
    title: 'Forge Button',
    description: 'Press Ctrl+Enter to run the 5-stage optimization pipeline.',
    position: 'top',
  },
  {
    target: 'aside[aria-label="Inspector"]',
    title: 'Inspector',
    description: 'Scores, strategy details, and pipeline trace appear here after synthesis.',
    position: 'left',
  },
  {
    target: 'footer[aria-label="Status Bar"]',
    title: 'Status Bar',
    description: 'Provider status, linked repo, and quick actions at a glance.',
    position: 'top',
  },
  {
    target: '[data-tour="command-palette"]',
    title: 'Command Palette',
    description: 'Press Ctrl+K anytime to search commands, navigate, and access help.',
    position: 'bottom',
    action: () => commandPalette.open(),
  },
];

class WalkthroughStore {
  active = $state(false);
  currentStep = $state(0);
  steps = $state<WalkthroughStep[]>([]);

  start(): void {
    this.steps = [...defaultSteps];
    this.currentStep = 0;
    this.active = true;
  }

  next(): void {
    if (this.currentStep < this.steps.length - 1) {
      this.currentStep++;
    } else {
      this.exit();
    }
  }

  back(): void {
    if (this.currentStep > 0) {
      // Close command palette if the current step had opened it
      if (commandPalette.isOpen) commandPalette.close();
      this.currentStep--;
    }
  }

  exit(): void {
    this.active = false;
    this.currentStep = 0;
    // Close command palette if it was opened by the walkthrough action
    if (commandPalette.isOpen) commandPalette.close();
  }

  get step(): WalkthroughStep | null {
    if (!this.active || this.steps.length === 0) return null;
    return this.steps[this.currentStep] ?? null;
  }

  get isLastStep(): boolean {
    return this.currentStep >= this.steps.length - 1;
  }

  get progress(): string {
    return `${this.currentStep + 1} / ${this.steps.length}`;
  }
}

export const walkthrough = new WalkthroughStore();
