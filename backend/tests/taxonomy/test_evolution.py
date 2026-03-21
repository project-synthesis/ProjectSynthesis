"""Layer 3 behavioral test: evolution simulation (Spec 9.1).

Verifies Q_system monotonicity across warm-path cycles with realistic
synthetic data. Uses hash-based embedding to produce deterministic clusters.
"""

import numpy as np
import pytest

from app.models import Optimization
from app.services.taxonomy.engine import TaxonomyEngine
from tests.taxonomy.conftest import EMBEDDING_DIM


@pytest.mark.asyncio
@pytest.mark.slow
async def test_q_system_non_regressive_over_100_optimizations(
    db, mock_embedding, mock_provider,
):
    """Q_system should remain non-regressive across warm-path cycles.

    Simulates 100 optimizations across 5 distinct domains, running
    warm-path every 20 optimizations. Q_system at each checkpoint
    should be >= previous checkpoint (within tolerance).
    """
    engine = TaxonomyEngine(
        embedding_service=mock_embedding, provider=mock_provider,
    )
    rng = np.random.RandomState(42)

    # 5 domain centers — well-separated in embedding space
    domains = ["REST API", "SQL queries", "React components", "Docker configs", "Auth tokens"]
    centers = {}
    for i, domain in enumerate(domains):
        center = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        center[i * 10:(i + 1) * 10] = 1.0
        center /= np.linalg.norm(center)
        centers[domain] = center

    q_checkpoints: list[float] = []

    for batch in range(5):
        for i in range(20):
            domain = domains[i % len(domains)]
            noise = rng.randn(EMBEDDING_DIM).astype(np.float32) * 0.05
            emb = centers[domain] + noise
            emb /= np.linalg.norm(emb)

            opt = Optimization(
                raw_prompt=f"{domain} prompt {batch * 20 + i}",
                optimized_prompt=f"Optimized {domain}",
                task_type="coding",
                intent_label=domain.lower(),
                domain=domain,
                domain_raw=domain,
                strategy_used="auto",
                embedding=emb.tobytes(),
            )
            db.add(opt)
            await db.flush()

            await engine.process_optimization(opt.id, db)

        # Run warm path at each checkpoint
        result = await engine.run_warm_path(db)
        if result and result.q_system is not None:
            q_checkpoints.append(result.q_system)

    # Must have at least 2 checkpoints to verify non-regression
    assert len(q_checkpoints) >= 2, (
        f"Expected at least 2 Q checkpoints but got {len(q_checkpoints)}"
    )

    # Verify non-regression (within tolerance)
    for i in range(1, len(q_checkpoints)):
        assert q_checkpoints[i] >= q_checkpoints[i - 1] - 0.05, (
            f"Q_system regressed at checkpoint {i}: "
            f"{q_checkpoints[i]:.4f} < {q_checkpoints[i - 1]:.4f}"
        )
