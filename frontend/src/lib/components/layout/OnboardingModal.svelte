<script lang="ts">
  import OnboardingWizard from './OnboardingWizard.svelte';

  interface Props {
    onComplete: () => void;
  }
  const { onComplete }: Props = $props();
</script>

<!-- Full-screen onboarding overlay -->
<div class="fixed inset-0 z-50 flex items-center justify-center bg-bg-primary/90">
  <OnboardingWizard {onComplete} />
</div>
