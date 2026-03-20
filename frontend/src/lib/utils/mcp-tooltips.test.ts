import { describe, it, expect, beforeEach } from 'vitest';
import { forgeStore } from '$lib/stores/forge.svelte';
import { preferencesStore } from '$lib/stores/preferences.svelte';
import { forceSamplingTooltip, forcePassthroughTooltip } from './mcp-tooltips';

describe('forceSamplingTooltip', () => {
  beforeEach(() => {
    forgeStore._reset();
    preferencesStore._reset();
  });

  it('returns undefined when not disabled', () => {
    expect(forceSamplingTooltip(false)).toBeUndefined();
  });

  it('returns tooltip when disabled and passthrough is on', () => {
    preferencesStore.prefs.pipeline.force_passthrough = true;
    const tip = forceSamplingTooltip(true);
    expect(tip).toBeTruthy();
    expect(typeof tip).toBe('string');
  });

  it('returns tooltip when disabled and not sampling capable', () => {
    forgeStore.samplingCapable = false;
    const tip = forceSamplingTooltip(true);
    expect(tip).toBeTruthy();
  });

  it('returns tooltip when samplingCapable is null', () => {
    forgeStore.samplingCapable = null;
    const tip = forceSamplingTooltip(true);
    expect(tip).toBeTruthy();
  });

  it('returns tooltip when MCP is disconnected', () => {
    forgeStore.mcpDisconnected = true;
    const tip = forceSamplingTooltip(true);
    expect(tip).toBeTruthy();
    expect(tip).toContain('disconnected');
  });

  it('returns undefined when disabled but all conditions are clear', () => {
    // samplingCapable=true, mcpDisconnected=false, force_passthrough=false
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    preferencesStore.prefs.pipeline.force_passthrough = false;
    // When disabled=true but none of the blocking conditions apply, returns undefined
    expect(forceSamplingTooltip(true)).toBeUndefined();
  });
});

describe('forcePassthroughTooltip', () => {
  beforeEach(() => {
    forgeStore._reset();
    preferencesStore._reset();
  });

  it('returns undefined when not disabled', () => {
    expect(forcePassthroughTooltip(false)).toBeUndefined();
  });

  it('returns tooltip when disabled and sampling is on', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    const tip = forcePassthroughTooltip(true);
    expect(tip).toBeTruthy();
    expect(typeof tip).toBe('string');
  });

  it('returns tooltip when sampling is available and MCP connected', () => {
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    const tip = forcePassthroughTooltip(true);
    expect(tip).toBeTruthy();
    expect(tip).toContain('sampling');
  });

  it('returns undefined when disabled but sampling not available', () => {
    forgeStore.samplingCapable = false;
    forgeStore.mcpDisconnected = false;
    preferencesStore.prefs.pipeline.force_sampling = false;
    // No sampling available — no tip needed for passthrough
    expect(forcePassthroughTooltip(true)).toBeUndefined();
  });

  it('returns undefined when disabled and MCP disconnected (sampling not available)', () => {
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = true;
    preferencesStore.prefs.pipeline.force_sampling = false;
    // MCP disconnected means sampling is effectively not available
    expect(forcePassthroughTooltip(true)).toBeUndefined();
  });
});
