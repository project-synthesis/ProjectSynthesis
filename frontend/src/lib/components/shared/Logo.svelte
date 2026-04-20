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

  let isAnimating = $state(false);

  function triggerAnimation() {
    if (isAnimating) return;
    isAnimating = true;
    setTimeout(() => {
      isAnimating = false;
    }, 1200);
  }
</script>

<div class="logo-container {className}" class:full={variant === 'full'} class:is-active={isAnimating} onclick={triggerAnimation} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') triggerAnimation() }} role="button" tabindex="0" aria-label="Trigger brand animation">
  <svg 
    width={size} 
    height={size} 
    viewBox="0 0 32 32" 
    aria-hidden="true" 
    fill="none" 
    stroke="var(--tier-accent, var(--color-neon-cyan))" 
    stroke-width="1.8" 
    stroke-linecap="round" 
    stroke-linejoin="round"
    class="brand-svg"
  >
        <!-- Background Track -->
    <path class="brand-track" d="M 24 8 L 12 8 A 4 4 0 0 0 12 16 L 20 16 A 4 4 0 0 1 20 24 L 8 24" opacity="0.3" />
    
    <!-- Animating Data Packet -->
    <path class="brand-packet" d="M 24 8 L 12 8 A 4 4 0 0 0 12 16 L 20 16 A 4 4 0 0 1 20 24 L 8 24" />
    
    <!-- Top Terminal -->
    <rect class="top-terminal" x="21.5" y="5.5" width="5" height="5" fill="var(--color-bg-primary)" stroke="var(--tier-accent, var(--color-neon-cyan))" stroke-width="1.8" rx="1.25" />
    
    <!-- Center Node -->
    <circle class="center-node" cx="16" cy="16" r="3" fill="var(--tier-accent, var(--color-neon-cyan))" stroke="none" />
    
    <!-- Bottom Terminal -->
    <rect class="bottom-terminal" x="5.5" y="21.5" width="5" height="5" fill="var(--color-bg-primary)" stroke="var(--tier-accent, var(--color-neon-cyan))" stroke-width="1.8" rx="1.25" />
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
    cursor: pointer;
    outline: none;
    border: none !important;
    background: transparent !important;
  }
  .logo-container:hover, .logo-container:active {
    border: none !important;
    background: transparent !important;
  }

  .brand-svg {
    flex-shrink: 0;
    transition: transform var(--duration-hover) var(--ease-spring);
  }

  .logo-container:hover .brand-svg {
    transform: scale(1.08);
  }
  .logo-container:active .brand-svg {
    transform: scale(0.95);
  }

  .logo-container:hover .center-node {
    fill: var(--color-text-primary);
    transform-origin: 16px 16px;
    transform: scale(1.2);
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .brand-track {
    transition: opacity 0.3s ease;
  }
  .logo-container:hover .brand-track {
    opacity: 0.6;
  }

  /* Nodes and Terminals base origin for transforms */
  .top-terminal { transform-origin: 24px 8px; transition: transform 0.2s, fill 0.2s; }
  .bottom-terminal { transform-origin: 8px 24px; transition: transform 0.2s, fill 0.2s; }
  .center-node { transform-origin: 16px 16px; transition: transform 0.2s, fill 0.2s; }

  /* Data Packet Defaults */
  .brand-packet {
    stroke-dasharray: 12 80;
    stroke-dashoffset: 80; /* Hidden initially */
    stroke: var(--color-text-primary);
    stroke-width: 2.2;
    stroke-linecap: round;
    opacity: 0;
  }

  /* Click / Active Animation Trigger */
  .logo-container.is-active .brand-packet {
    animation: packet-flow 0.8s cubic-bezier(0.4, 0, 0.2, 1) forwards;
  }
  .logo-container.is-active .top-terminal {
    animation: terminal-pulse 0.4s ease-out 0s forwards;
  }
  .logo-container.is-active .center-node {
    animation: center-pulse 0.5s ease-out 0.3s forwards;
  }
  .logo-container.is-active .bottom-terminal {
    animation: terminal-pulse 0.4s ease-out 0.6s forwards;
  }

  @keyframes packet-flow {
    0% {
      stroke-dashoffset: 80;
      opacity: 0;
    }
    15% {
      opacity: 1;
    }
    85% {
      opacity: 1;
    }
    100% {
      stroke-dashoffset: -20;
      opacity: 0;
    }
  }

  @keyframes terminal-pulse {
    0% { transform: scale(1); fill: var(--color-bg-primary); }
    40% { transform: scale(1.4); fill: var(--tier-accent, var(--color-neon-cyan)); stroke-width: 2.5px; }
    100% { transform: scale(1); fill: var(--color-bg-primary); }
  }

  @keyframes center-pulse {
    0% { transform: scale(1); fill: var(--tier-accent, var(--color-neon-cyan)); }
    40% { transform: scale(1.8); fill: var(--color-text-primary); }
    100% { transform: scale(1); fill: var(--tier-accent, var(--color-neon-cyan)); }
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
