<script lang="ts">
  import DrillIntoClusterModal from './DrillIntoClusterModal.svelte';

  interface ClusterRef {
    id: string;
    label: string;
    domain: string;
    task_type: string;
  }

  interface Props {
    cluster: ClusterRef;
    onDrilled?: (runId: string) => void;
  }

  let { cluster, onDrilled }: Props = $props();
  let open = $state(false);
</script>

<button
  type="button"
  class="btn-outline-secondary"
  onclick={() => open = true}
  aria-label="Drill into cluster {cluster.label}"
>
  Drill
</button>

{#if open}
  <DrillIntoClusterModal
    {cluster}
    onClose={() => open = false}
    onDrilled={(runId) => { open = false; onDrilled?.(runId); }}
  />
{/if}
