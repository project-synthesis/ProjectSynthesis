/**
 * Shared SSE stream parser and polling constants.
 *
 * Extracts the identical parsing logic used by both client.ts (startOptimization)
 * and forge.svelte.ts (_consumeSSEResponse) into a single async generator.
 */

export interface SSEEvent {
  event: string;
  data: unknown;
}

/** Polling interval for fallback status polling (ms). */
export const POLL_INTERVAL_MS = 5000;

/** Maximum polling attempts before giving up (60s total). */
export const MAX_POLL_ATTEMPTS = 12;

/**
 * Parse an SSE ReadableStream into individual events.
 *
 * Buffers incoming bytes, splits on double newlines, extracts event type
 * and data lines per the SSE specification. Multi-line `data:` fields are
 * concatenated. Yields once per complete event, with a microtask yield
 * between events so UI frameworks can flush.
 */
export async function* parseSSEStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal
): AsyncGenerator<SSEEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      if (signal?.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Split on double newlines to find complete events
      const events = buffer.split('\n\n');
      buffer = events.pop() || '';

      for (const raw of events) {
        if (!raw.trim()) continue;
        const typeMatch = raw.match(/^event: (.+)$/m);
        // Concatenate all `data:` lines per SSE spec (multi-line safe)
        const dataLines = raw.match(/^data: (.+)$/gm);
        if (typeMatch && dataLines) {
          const payload = dataLines.map((l) => l.slice(6)).join('\n');
          let parsed: unknown;
          try {
            parsed = JSON.parse(payload);
          } catch {
            parsed = payload;
          }
          yield { event: typeMatch[1], data: parsed };
          await Promise.resolve(); // yield so UI framework flushes between events
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
