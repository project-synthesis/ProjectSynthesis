export interface HistoryEntry {
  id: string;
  raw_prompt: string;
  optimized_prompt?: string;
  overall_score?: number;
  primary_framework?: string;   // API field name from to_dict()
  model_optimize?: string;      // API returns per-stage model fields
  created_at: string;
  duration_ms?: number;
  tags?: string[];
  linked_repo_full_name?: string;  // for repo context restore on re-forge
  linked_repo_branch?: string;
}

export interface HistoryFilters {
  search: string;
  strategy: string | null;
  sortBy: 'created_at' | 'overall_score';
  sortDir: 'asc' | 'desc';
  offset: number;
  limit: number;
  has_repo?: boolean;
  min_score?: number;
  max_score?: number;
  task_type?: string;
  status?: string;
}

function loadFilters(): HistoryFilters {
  const defaults: HistoryFilters = {
    search: '',
    strategy: null,
    sortBy: 'created_at',
    sortDir: 'desc',
    offset: 0,
    limit: 20
  };
  if (typeof window === 'undefined') return defaults;
  try {
    const stored = localStorage.getItem('pf_historyFilters');
    if (stored) {
      const parsed = JSON.parse(stored);
      return {
        ...defaults,
        sortBy: parsed.sortBy || defaults.sortBy,
        sortDir: parsed.sortDir || defaults.sortDir,
        strategy: parsed.strategy ?? null,
        limit: parsed.limit || defaults.limit
      };
    }
  } catch { /* ignore */ }
  return defaults;
}

function saveFilters(filters: HistoryFilters) {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem('pf_historyFilters', JSON.stringify({
      sortBy: filters.sortBy,
      sortDir: filters.sortDir,
      strategy: filters.strategy,
      limit: filters.limit
    }));
  } catch { /* ignore */ }
}

class HistoryStore {
  entries = $state<HistoryEntry[]>([]);
  totalCount = $state(0);
  isLoading = $state(false);
  selectedId = $state<string | null>(null);
  filters = $state<HistoryFilters>(loadFilters());

  get selectedEntry(): HistoryEntry | undefined {
    return this.entries.find(e => e.id === this.selectedId);
  }

  get hasMore(): boolean {
    return this.entries.length < this.totalCount;
  }

  setEntries(entries: HistoryEntry[], total: number) {
    this.entries = entries;
    this.totalCount = total;
  }

  appendEntries(entries: HistoryEntry[], total: number) {
    this.entries = [...this.entries, ...entries];
    this.totalCount = total;
  }

  removeEntry(id: string) {
    this.entries = this.entries.filter(e => e.id !== id);
    this.totalCount--;
    if (this.selectedId === id) {
      this.selectedId = null;
    }
  }

  select(id: string) {
    this.selectedId = id;
  }

  updateFilters(partial: Partial<HistoryFilters>) {
    this.filters = { ...this.filters, ...partial };
    saveFilters(this.filters);
  }
}

export const history = new HistoryStore();
