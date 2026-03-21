import { describe, it, expect, afterEach, vi } from 'vitest';
import { matchPattern, listFamilies, getFamilyDetail, renameFamily } from './patterns';
import { mockFetch, mockPatternFamily } from '../test-utils';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('matchPattern - URL and method', () => {
  it('sends POST to /patterns/match with prompt_text in body', async () => {
    const mock = mockFetch([{
      match: '/patterns/match',
      response: { match: null },
    }]);
    await matchPattern('hello world');
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/patterns/match');
    expect((opts as RequestInit).method).toBe('POST');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.prompt_text).toBe('hello world');
  });

  it('returns null match when no family matches', async () => {
    mockFetch([{ match: '/patterns/match', response: { match: null } }]);
    const result = await matchPattern('something unrelated');
    expect(result.match).toBeNull();
  });
});

describe('listFamilies - params', () => {
  it('appends offset, limit, and domain params', async () => {
    const mock = mockFetch([{
      match: '/patterns/families',
      response: { total: 0, count: 0, offset: 10, has_more: false, next_offset: null, items: [] },
    }]);
    await listFamilies({ offset: 10, limit: 5, domain: 'backend' });
    const [url] = mock.mock.calls[0];
    expect(url).toContain('offset=10');
    expect(url).toContain('limit=5');
    expect(url).toContain('domain=backend');
  });

  it('does not append params when none provided', async () => {
    const mock = mockFetch([{
      match: '/patterns/families',
      response: { total: 0, count: 0, offset: 0, has_more: false, next_offset: null, items: [] },
    }]);
    await listFamilies();
    const [url] = mock.mock.calls[0];
    expect(url).not.toContain('?');
  });
});

describe('getFamilyDetail - URL construction', () => {
  it('calls GET /patterns/families/:id', async () => {
    const detail = { ...mockPatternFamily({ id: 'fam-42' }), updated_at: null, meta_patterns: [], optimizations: [] };
    const mock = mockFetch([{ match: '/patterns/families/fam-42', response: detail }]);
    const result = await getFamilyDetail('fam-42');
    expect(result.id).toBe('fam-42');
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/patterns/families/fam-42');
  });
});

describe('renameFamily - URL and body', () => {
  it('sends PATCH to /patterns/families/:id with intent_label', async () => {
    const mock = mockFetch([{ match: '/patterns/families/fam-1', response: { id: 'fam-1', intent_label: 'Renamed' } }]);
    await renameFamily('fam-1', 'Renamed');
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/patterns/families/fam-1');
    expect((opts as RequestInit).method).toBe('PATCH');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.intent_label).toBe('Renamed');
  });
});
