<script lang="ts">
  let {
    size = 24,
    variant = 'mark',
    class: className = ''
  }: {
    size?: number;
    variant?: 'mark' | 'full';
    class?: string;
  } = $props();
</script>

<div class="logo-container {className}" class:full={variant === 'full'}>
  <svg 
    width={size} 
    height={size} 
    viewBox="0 0 32 32" 
    aria-hidden="true" 
    fill="none" 
    stroke="var(--color-neon-cyan)" 
    stroke-width="1.8" 
    stroke-linecap="round" 
    stroke-linejoin="round"
    class="brand-svg"
  >
    <!-- The S Pipeline Trace - Smooth Symmetrical Curve -->
    <path d="M 24 8 
             L 12 8 
             A 4 4 0 0 0 12 16 
             L 20 16 
             A 4 4 0 0 1 20 24 
             L 8 24" />
    
    <!-- Top Terminal -->
    <rect x="21.5" y="5.5" width="5" height="5" fill="var(--color-bg-primary)" stroke="var(--color-neon-cyan)" stroke-width="1.8" rx="1.25" />
    
    <!-- Center Node -->
    <circle cx="16" cy="16" r="3" fill="var(--color-neon-cyan)" stroke="none" />
    
    <!-- Bottom Terminal -->
    <rect x="5.5" y="21.5" width="5" height="5" fill="var(--color-bg-primary)" stroke="var(--color-neon-cyan)" stroke-width="1.8" rx="1.25" />
  </svg>

  {#if variant === 'full'}
    <div class="brand-text" style="font-size: {size * 0.55}px; margin-left: {size * 0.2}px;">
      PROJECT<span class="highlight">SYNTHESIS</span>
    </div>
  {/if}
</div>

<style>
  .logo-container {
    display: inline-flex;
    align-items: center;
    vertical-align: middle;
  }

  .brand-svg {
    flex-shrink: 0;
  }

  .brand-text {
    font-family: var(--font-display, 'Syne', sans-serif);
    font-weight: 700;
    letter-spacing: 0.15em;
    color: var(--color-text-primary);
    white-space: nowrap;
    text-transform: uppercase;
    line-height: 1;
  }

  </style>
