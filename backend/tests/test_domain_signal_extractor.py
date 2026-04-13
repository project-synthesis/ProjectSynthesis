"""Tests for domain signal auto-enrichment (A3)."""

import pytest
import pytest_asyncio

from app.services.domain_signal_extractor import extract_domain_signals
from app.services.domain_signal_loader import DomainSignalLoader


@pytest_asyncio.fixture
async def db():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_domain_with_members(db, domain_label: str, prompts: list[str], coherence: float = 0.6):
    """Helper: create a domain node with child cluster and optimizations."""
    from app.models import Optimization, PromptCluster

    domain = PromptCluster(
        label=domain_label,
        state="domain",
        coherence=coherence,
        member_count=0,
        usage_count=0,
    )
    db.add(domain)
    await db.flush()

    child = PromptCluster(
        label=f"{domain_label}-cluster-1",
        state="active",
        parent_id=domain.id,
        coherence=0.5,
        member_count=len(prompts),
        usage_count=0,
    )
    db.add(child)
    await db.flush()

    for prompt_text in prompts:
        opt = Optimization(
            raw_prompt=prompt_text,
            optimized_prompt="optimized",
            overall_score=7.0,
            strategy_used="auto",
            task_type="coding",
            domain=domain_label,
            status="completed",
            cluster_id=child.id,
        )
        db.add(opt)
    await db.commit()
    return domain


async def _seed_noise_prompts(db, count: int = 20):
    """Add non-domain prompts so global frequency differs from domain frequency."""
    from app.models import Optimization

    noise_prompts = [
        "Write a blog post about sustainable energy trends",
        "Analyze the quarterly sales data and generate a report",
        "Create a marketing campaign for the new product launch",
        "Draft an email to the stakeholders about the project timeline",
        "Design a logo for the brand refresh initiative",
        "Evaluate the performance metrics of the content strategy",
        "Write documentation for the onboarding process",
        "Review the budget proposal for next quarter",
        "Create a presentation slide deck for the board meeting",
        "Draft social media copy for the holiday campaign",
        "Analyze customer feedback from the survey results",
        "Write a press release for the partnership announcement",
        "Design a newsletter template for the weekly digest",
        "Create a user persona based on interview data",
        "Write a case study about the client success story",
        "Review the competitive landscape analysis report",
        "Draft talking points for the conference presentation",
        "Analyze website traffic patterns from last month",
        "Create an infographic summarizing the annual results",
        "Write a white paper on industry best practices",
    ]
    for text in noise_prompts[:count]:
        opt = Optimization(
            raw_prompt=text,
            optimized_prompt="optimized",
            overall_score=7.0,
            strategy_used="auto",
            task_type="writing",
            domain="general",
            status="completed",
        )
        db.add(opt)
    await db.commit()


class TestExtractDomainSignals:
    @pytest.mark.asyncio
    async def test_extracts_top_keywords(self, db):
        """Domain with enough members should produce keyword signals."""
        # Add noise so global frequency differs from domain frequency
        await _seed_noise_prompts(db, 20)

        prompts = [
            "Implement a REST API endpoint for the authentication service",
            "Add rate limiting to the API gateway with Redis backend",
            "Refactor the API authentication middleware for JWT tokens",
            "Create a health check endpoint for the API service",
            "Build a caching layer for the API response pipeline",
            "Debug the API timeout issue in the authentication module",
        ]
        await _seed_domain_with_members(db, "api-services", prompts, coherence=0.6)

        signals = await extract_domain_signals(db, "api-services", min_members=5)
        assert len(signals) > 0
        assert len(signals) <= 8  # top_k default
        # "api" should be highly discriminative for this domain
        keywords = [kw for kw, _ in signals]
        assert any("api" in kw for kw in keywords)
        # Weights should be in [0.5, 1.0]
        for _, weight in signals:
            assert 0.5 <= weight <= 1.0

    @pytest.mark.asyncio
    async def test_skips_sparse_domain(self, db):
        """Domain with too few members should return empty."""
        prompts = ["One prompt only", "Two prompts"]
        await _seed_domain_with_members(db, "sparse-domain", prompts)

        signals = await extract_domain_signals(db, "sparse-domain", min_members=5)
        assert signals == []

    @pytest.mark.asyncio
    async def test_skips_low_coherence_domain(self, db):
        """Domain with low coherence should return empty."""
        prompts = [f"Prompt number {i} about random topics" for i in range(10)]
        await _seed_domain_with_members(db, "incoherent", prompts, coherence=0.2)

        signals = await extract_domain_signals(db, "incoherent", min_coherence=0.4)
        assert signals == []

    @pytest.mark.asyncio
    async def test_nonexistent_domain_returns_empty(self, db):
        signals = await extract_domain_signals(db, "does-not-exist")
        assert signals == []

    @pytest.mark.asyncio
    async def test_register_signals_updates_loader(self, db):
        """After registration, the loader's classify() recognizes the new domain."""
        # Add noise prompts for global frequency contrast
        await _seed_noise_prompts(db, 20)

        prompts = [
            "Deploy the Kubernetes cluster with Helm charts",
            "Configure the Kubernetes ingress controller for TLS",
            "Set up horizontal pod autoscaling in Kubernetes",
            "Debug the Kubernetes deployment rollout failure",
            "Monitor Kubernetes pod health with Prometheus",
            "Manage Kubernetes secrets and config maps",
        ]
        await _seed_domain_with_members(db, "k8s-ops", prompts, coherence=0.7)

        signals = await extract_domain_signals(db, "k8s-ops", min_members=5)
        assert len(signals) > 0

        loader = DomainSignalLoader()
        loader.register_signals("k8s-ops", signals)

        # Now the loader should score prompts with extracted keywords
        extracted_kws = {kw for kw, _ in signals}
        scored = loader.score(extracted_kws)
        assert "k8s-ops" in scored
        assert scored["k8s-ops"] > 0
