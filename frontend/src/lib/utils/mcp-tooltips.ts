/**
 * Tooltip text generators for MCP force-toggle buttons.
 *
 * Replaces the nested ternary expressions in Navigator.svelte with
 * readable function calls.
 */

import { forgeStore } from '$lib/stores/forge.svelte';
import { preferencesStore } from '$lib/stores/preferences.svelte';

/**
 * Returns tooltip text for the "Force IDE sampling" toggle when disabled,
 * or `undefined` when the toggle is enabled (no tooltip needed).
 */
export function forceSamplingTooltip(disabled: boolean, pending?: boolean): string | undefined {
  if (pending) return 'Sampling unavailable — activates when IDE connects';
  if (!disabled) return undefined;
  if (forgeStore.samplingCapable === null) return 'No sampling-capable MCP client detected';
  if (forgeStore.samplingCapable === false) return 'Your MCP client does not support sampling';
  if (preferencesStore.pipeline.force_passthrough) return 'Disable Force passthrough first';
  return undefined;
}

/**
 * Returns tooltip text for the "Force passthrough" toggle when disabled,
 * or `undefined` when the toggle is enabled (no tooltip needed).
 */
export function forcePassthroughTooltip(disabled: boolean): string | undefined {
  if (!disabled) return undefined;
  if (preferencesStore.pipeline.force_sampling) return 'Disable Force IDE sampling first';
  if (forgeStore.samplingCapable === true) {
    return 'Sampling is available — use Force IDE sampling instead';
  }
  return undefined;
}
