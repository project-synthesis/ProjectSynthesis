import { describe, it, expect, beforeEach } from 'vitest';
import { routing } from './routing.svelte';
import { preferencesStore } from './preferences.svelte';
import { forgeStore } from './forge.svelte';

/**
 * Helper: reset both stores to a neutral state (internal provider, no force toggles).
 */
function resetStores() {
  preferencesStore.prefs.pipeline.force_passthrough = false;
  preferencesStore.prefs.pipeline.force_sampling = false;
  forgeStore.provider = 'claude_cli';
  forgeStore.samplingCapable = null;
  forgeStore.mcpDisconnected = false;
}

describe('routing store — 5-tier priority chain', () => {
  beforeEach(resetStores);

  // -----------------------------------------------------------------------
  // Priority 1: force_passthrough always wins
  // -----------------------------------------------------------------------

  it('force_passthrough=true → passthrough (regardless of provider)', () => {
    preferencesStore.prefs.pipeline.force_passthrough = true;
    forgeStore.provider = 'claude_cli';
    expect(routing.tier).toBe('passthrough');
  });

  it('force_passthrough=true → passthrough (even with sampling capable)', () => {
    preferencesStore.prefs.pipeline.force_passthrough = true;
    forgeStore.samplingCapable = true;
    expect(routing.tier).toBe('passthrough');
  });

  // -----------------------------------------------------------------------
  // Priority 2: force_sampling (if capable + connected)
  // -----------------------------------------------------------------------

  it('force_sampling + capable + connected → sampling', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    forgeStore.provider = null;
    expect(routing.tier).toBe('sampling');
  });

  it('force_sampling + not capable → falls through', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = false;
    forgeStore.provider = null;
    expect(routing.tier).not.toBe('sampling');
  });

  it('force_sampling + disconnected → falls through', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = true;
    forgeStore.provider = null;
    expect(routing.tier).not.toBe('sampling');
  });

  it('force_sampling + capable=null → falls through', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = null;
    forgeStore.provider = null;
    expect(routing.tier).not.toBe('sampling');
  });

  // -----------------------------------------------------------------------
  // Priority 3: internal provider available
  // -----------------------------------------------------------------------

  it('provider present (no force toggles) → internal', () => {
    forgeStore.provider = 'claude_cli';
    expect(routing.tier).toBe('internal');
  });

  it('provider="anthropic_api" → internal', () => {
    forgeStore.provider = 'anthropic_api';
    expect(routing.tier).toBe('internal');
  });

  // -----------------------------------------------------------------------
  // Priority 4: auto-sampling available
  // -----------------------------------------------------------------------

  it('no provider + samplingCapable + connected → sampling', () => {
    forgeStore.provider = null;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    expect(routing.tier).toBe('sampling');
  });

  it('no provider + samplingCapable + disconnected → passthrough', () => {
    forgeStore.provider = null;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = true;
    expect(routing.tier).toBe('passthrough');
  });

  // -----------------------------------------------------------------------
  // Priority 5: passthrough fallback
  // -----------------------------------------------------------------------

  it('no provider + no sampling → passthrough', () => {
    forgeStore.provider = null;
    forgeStore.samplingCapable = null;
    expect(routing.tier).toBe('passthrough');
  });

  it('no provider + samplingCapable=false → passthrough', () => {
    forgeStore.provider = null;
    forgeStore.samplingCapable = false;
    expect(routing.tier).toBe('passthrough');
  });

  // -----------------------------------------------------------------------
  // Convenience getters
  // -----------------------------------------------------------------------

  it('isPassthrough matches tier', () => {
    preferencesStore.prefs.pipeline.force_passthrough = true;
    expect(routing.isPassthrough).toBe(true);
    expect(routing.isSampling).toBe(false);
    expect(routing.isInternal).toBe(false);
  });

  it('isSampling matches tier', () => {
    forgeStore.provider = null;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    expect(routing.isSampling).toBe(true);
    expect(routing.isPassthrough).toBe(false);
    expect(routing.isInternal).toBe(false);
  });

  it('isInternal matches tier', () => {
    forgeStore.provider = 'claude_cli';
    expect(routing.isInternal).toBe(true);
    expect(routing.isPassthrough).toBe(false);
    expect(routing.isSampling).toBe(false);
  });
});

// =========================================================================
// Degradation detection
// =========================================================================

describe('routing store — degradation detection', () => {
  beforeEach(resetStores);

  // -----------------------------------------------------------------------
  // requestedTier
  // -----------------------------------------------------------------------

  it('requestedTier is null when no force toggles', () => {
    expect(routing.requestedTier).toBeNull();
  });

  it('requestedTier = sampling when force_sampling is on', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    expect(routing.requestedTier).toBe('sampling');
  });

  it('requestedTier = passthrough when force_passthrough is on', () => {
    preferencesStore.prefs.pipeline.force_passthrough = true;
    expect(routing.requestedTier).toBe('passthrough');
  });

  it('force_passthrough takes priority over force_sampling for requestedTier', () => {
    preferencesStore.prefs.pipeline.force_passthrough = true;
    preferencesStore.prefs.pipeline.force_sampling = true;
    expect(routing.requestedTier).toBe('passthrough');
  });

  // -----------------------------------------------------------------------
  // isDegraded — not degraded scenarios
  // -----------------------------------------------------------------------

  it('not degraded when no force toggles', () => {
    expect(routing.isDegraded).toBe(false);
  });

  it('not degraded when force_passthrough honored', () => {
    preferencesStore.prefs.pipeline.force_passthrough = true;
    expect(routing.tier).toBe('passthrough');
    expect(routing.isDegraded).toBe(false);
  });

  it('not degraded when force_sampling honored (capable + connected)', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    forgeStore.provider = null;
    expect(routing.tier).toBe('sampling');
    expect(routing.isDegraded).toBe(false);
  });

  // -----------------------------------------------------------------------
  // isAutoFallback — seamless fallback to internal provider
  // -----------------------------------------------------------------------

  it('auto-fallback when force_sampling + disconnected + provider available', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = true;
    forgeStore.provider = 'claude_cli';
    expect(routing.tier).toBe('internal');
    expect(routing.isAutoFallback).toBe(true);
    expect(routing.isDegraded).toBe(false);
    expect(routing.autoFallbackMessage).toContain('CLI active');
  });

  it('auto-fallback when force_sampling + not capable + provider available', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = false;
    forgeStore.mcpDisconnected = false;
    forgeStore.provider = 'claude_cli';
    expect(routing.tier).toBe('internal');
    expect(routing.isAutoFallback).toBe(true);
    expect(routing.isDegraded).toBe(false);
  });

  it('auto-fallback when force_sampling + samplingCapable=null + provider', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = null;
    forgeStore.provider = 'claude_cli';
    expect(routing.tier).toBe('internal');
    expect(routing.isAutoFallback).toBe(true);
    expect(routing.isDegraded).toBe(false);
  });

  it('not auto-fallback when no force toggles', () => {
    forgeStore.provider = 'claude_cli';
    expect(routing.isAutoFallback).toBe(false);
  });

  it('not auto-fallback when force_sampling honored', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    forgeStore.provider = null;
    expect(routing.tier).toBe('sampling');
    expect(routing.isAutoFallback).toBe(false);
  });

  it('autoFallbackMessage is null when not auto-fallback', () => {
    expect(routing.autoFallbackMessage).toBeNull();
  });

  // -----------------------------------------------------------------------
  // isDegraded — true degradation (passthrough fallback only)
  // -----------------------------------------------------------------------

  it('degraded to passthrough when force_sampling + no provider + no sampling', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = null;
    forgeStore.provider = null;
    expect(routing.tier).toBe('passthrough');
    expect(routing.isDegraded).toBe(true);
    expect(routing.isAutoFallback).toBe(false);
    expect(routing.degradationReason).toBe('no_sampling_detected');
  });

  // -----------------------------------------------------------------------
  // degradationMessage — only for true degradation (passthrough)
  // -----------------------------------------------------------------------

  it('message says "using passthrough" when truly degraded', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = null;
    forgeStore.provider = null;
    expect(routing.tier).toBe('passthrough');
    expect(routing.degradationMessage).toContain('using passthrough');
  });

  it('degradationMessage is null when not degraded', () => {
    expect(routing.degradationMessage).toBeNull();
  });

  it('degradationReason is null when not degraded', () => {
    expect(routing.degradationReason).toBeNull();
  });
});
