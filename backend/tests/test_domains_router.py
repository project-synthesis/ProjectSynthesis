"""Tests for the /api/domains router."""

import pytest
from sqlalchemy import select

from app.models import PromptCluster
from app.schemas.sub_domain_readiness import ReadinessHistoryResponse


class TestListDomains:
    @pytest.mark.asyncio
    async def test_list_domains_empty(self, app_client, db_session):
        """GET /api/domains returns [] when no domain nodes exist (seeds removed)."""
        # Remove the seed domain nodes added by app_client fixture
        result = await db_session.execute(
            __import__("sqlalchemy").select(PromptCluster).where(
                PromptCluster.state == "domain"
            )
        )
        for node in result.scalars():
            await db_session.delete(node)
        await db_session.commit()

        resp = await app_client.get("/api/domains")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_list_domains_returns_seeds(self, app_client, db_session):
        """GET /api/domains returns domain nodes seeded by conftest."""
        resp = await app_client.get("/api/domains")
        assert resp.status_code == 200
        data = resp.json()
        # conftest seeds 6 domain nodes (fullstack is discovered, not seeded)
        assert len(data) == 8
        labels = {d["label"] for d in data}
        assert "backend" in labels
        assert "frontend" in labels

    @pytest.mark.asyncio
    async def test_list_domains_returns_expected_fields(self, app_client, db_session):
        """GET /api/domains items include all DomainInfo fields."""
        resp = await app_client.get("/api/domains")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0

        item = data[0]
        assert "id" in item
        assert "label" in item
        assert "color_hex" in item
        assert "member_count" in item
        assert "source" in item

    @pytest.mark.asyncio
    async def test_list_domains_source_defaults_to_seed(self, app_client, db_session):
        """Domain nodes without cluster_metadata source default to 'seed'."""
        resp = await app_client.get("/api/domains")
        assert resp.status_code == 200
        data = resp.json()
        # Seeded nodes have no cluster_metadata, should default to "seed"
        for item in data:
            assert item["source"] == "seed"

    @pytest.mark.asyncio
    async def test_list_domains_excludes_non_domain_states(self, app_client, db_session):
        """GET /api/domains excludes active, mature, template, archived clusters."""
        db_session.add(PromptCluster(
            id="non-domain", label="active cluster", state="active",
            domain="backend", task_type="coding",
            centroid_embedding=b'\x00' * 384,
        ))
        await db_session.commit()

        resp = await app_client.get("/api/domains")
        assert resp.status_code == 200
        data = resp.json()
        ids = [d["id"] for d in data]
        assert "non-domain" not in ids

    @pytest.mark.asyncio
    async def test_list_domains_sorted_by_label(self, app_client, db_session):
        """GET /api/domains returns results sorted by label alphabetically."""
        resp = await app_client.get("/api/domains")
        assert resp.status_code == 200
        data = resp.json()
        labels = [d["label"] for d in data]
        assert labels == sorted(labels)


class TestListDomainsPerProject:
    """Hybrid taxonomy: ``project_id`` filters to evidence-earned domains."""

    @pytest.mark.asyncio
    async def test_project_filter_hides_zero_evidence_domains(
        self, app_client, db_session,
    ):
        """Project with no optimizations sees only the canonical general."""
        from app.models import PromptCluster

        project = PromptCluster(
            id="proj-empty",
            label="empty-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.commit()

        resp = await app_client.get(f"/api/domains?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        labels = [d["label"] for d in data]
        # Canonical general is always visible; other domains are not.
        assert labels == ["general"]
        # Per-project count is exposed.
        assert data[0]["project_member_count"] == 0

    @pytest.mark.asyncio
    async def test_project_filter_absolute_threshold(
        self, app_client, db_session,
    ):
        """Domain with ≥3 project-owned opts crosses the absolute threshold."""
        from app.models import Optimization, PromptCluster

        project = PromptCluster(
            id="proj-abs",
            label="abs-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.flush()

        # Cluster parented under a domain the conftest already seeded.
        cluster = PromptCluster(
            id="abs-cluster",
            label="API stuff",
            state="active",
            domain="backend",
            task_type="coding",
            member_count=3,
            centroid_embedding=b"\x00" * 384,
        )
        db_session.add(cluster)
        await db_session.flush()

        for i in range(3):
            db_session.add(Optimization(
                raw_prompt=f"p{i}",
                status="completed",
                cluster_id=cluster.id,
                project_id=project.id,
                overall_score=7.0,
            ))
        await db_session.commit()

        resp = await app_client.get(f"/api/domains?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        labels = {d["label"] for d in data}
        assert "backend" in labels
        backend = next(d for d in data if d["label"] == "backend")
        assert backend["project_member_count"] == 3
        assert backend["member_count"] == 3  # project-scoped view

    @pytest.mark.asyncio
    async def test_project_filter_proportional_threshold(
        self, app_client, db_session,
    ):
        """Small project: 2 backend / 2 total = 100% > 5% → visible."""
        from app.models import Optimization, PromptCluster

        project = PromptCluster(
            id="proj-prop",
            label="prop-project",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add(project)
        await db_session.flush()

        cluster = PromptCluster(
            id="prop-cluster",
            label="Small backend",
            state="active",
            domain="backend",
            task_type="coding",
            member_count=2,
            centroid_embedding=b"\x00" * 384,
        )
        db_session.add(cluster)
        await db_session.flush()

        for i in range(2):
            db_session.add(Optimization(
                raw_prompt=f"p{i}",
                status="completed",
                cluster_id=cluster.id,
                project_id=project.id,
                overall_score=6.5,
            ))
        await db_session.commit()

        resp = await app_client.get(f"/api/domains?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        labels = {d["label"] for d in data}
        # Below absolute floor (3), but 2/2 = 100% >> 5% visibility.
        assert "backend" in labels

    @pytest.mark.asyncio
    async def test_project_filter_isolates_cross_project_evidence(
        self, app_client, db_session,
    ):
        """Evidence from another project does NOT make a domain visible here."""
        from app.models import Optimization, PromptCluster

        proj_a = PromptCluster(
            id="proj-a",
            label="project-a",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        proj_b = PromptCluster(
            id="proj-b",
            label="project-b",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db_session.add_all([proj_a, proj_b])
        await db_session.flush()

        cluster = PromptCluster(
            id="shared-cluster",
            label="security stuff",
            state="active",
            domain="security",
            task_type="coding",
            member_count=5,
            centroid_embedding=b"\x00" * 384,
        )
        db_session.add(cluster)
        await db_session.flush()

        # 5 opts all belong to project A.
        for i in range(5):
            db_session.add(Optimization(
                raw_prompt=f"p{i}",
                status="completed",
                cluster_id=cluster.id,
                project_id=proj_a.id,
                overall_score=7.0,
            ))
        await db_session.commit()

        # Project B has no evidence — security must NOT be visible.
        resp_b = await app_client.get(f"/api/domains?project_id={proj_b.id}")
        assert resp_b.status_code == 200
        labels_b = {d["label"] for d in resp_b.json()}
        assert "security" not in labels_b
        assert "general" in labels_b

        # Project A has 5 opts — security IS visible.
        resp_a = await app_client.get(f"/api/domains?project_id={proj_a.id}")
        assert resp_a.status_code == 200
        labels_a = {d["label"] for d in resp_a.json()}
        assert "security" in labels_a


class TestPromoteToDomain:
    @pytest.mark.asyncio
    async def test_promote_cluster_to_domain(self, app_client, db_session):
        """POST /api/domains/{id}/promote promotes an eligible cluster."""
        cluster = PromptCluster(
            id="eligible-1", label="my-new-domain", state="active",
            domain="backend", task_type="coding", member_count=10,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post("/api/domains/eligible-1/promote")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "eligible-1"
        assert data["label"] == "my-new-domain"
        assert data["source"] == "manual"
        assert data["color_hex"].startswith("#")

        # Verify DB state changed
        await db_session.refresh(cluster)
        assert cluster.state == "domain"
        assert cluster.persistence == 1.0
        assert cluster.cluster_metadata is not None
        assert cluster.cluster_metadata["source"] == "manual"

    @pytest.mark.asyncio
    async def test_promote_mature_cluster_succeeds(self, app_client, db_session):
        """POST /api/domains/{id}/promote accepts mature clusters."""
        cluster = PromptCluster(
            id="mature-1", label="mature-domain", state="mature",
            domain="frontend", task_type="writing", member_count=8,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post("/api/domains/mature-1/promote")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_promote_rejects_low_member_count(self, app_client, db_session):
        """POST /api/domains/{id}/promote rejects cluster with member_count < 5."""
        cluster = PromptCluster(
            id="small-1", label="small-cluster", state="active",
            domain="backend", task_type="coding", member_count=3,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post("/api/domains/small-1/promote")
        assert resp.status_code == 422
        assert "3 members" in resp.json()["detail"]
        assert "minimum 5" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_promote_rejects_wrong_state_candidate(self, app_client, db_session):
        """POST /api/domains/{id}/promote rejects candidate state."""
        cluster = PromptCluster(
            id="cand-1", label="candidate-cluster", state="candidate",
            domain="backend", task_type="coding", member_count=10,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post("/api/domains/cand-1/promote")
        assert resp.status_code == 422
        assert "candidate" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_promote_rejects_wrong_state_archived(self, app_client, db_session):
        """POST /api/domains/{id}/promote rejects archived state."""
        cluster = PromptCluster(
            id="arch-1", label="archived-cluster", state="archived",
            domain="backend", task_type="coding", member_count=10,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post("/api/domains/arch-1/promote")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_promote_not_found(self, app_client, db_session):
        """POST /api/domains/{id}/promote returns 404 for nonexistent cluster."""
        resp = await app_client.post("/api/domains/nonexistent-id/promote")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_promote_already_domain(self, app_client, db_session):
        """POST /api/domains/{id}/promote returns 422 if already a domain node."""
        cluster = PromptCluster(
            id="dom-1", label="existing-domain", state="domain",
            domain="general", task_type="general", member_count=20,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post("/api/domains/dom-1/promote")
        assert resp.status_code == 422
        assert "Already a domain node" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_promote_rejects_duplicate_label(self, app_client, db_session):
        """POST /api/domains/{id}/promote returns 409 if domain label already exists."""
        # "backend" is already a domain node from conftest
        cluster = PromptCluster(
            id="dup-1", label="backend", state="active",
            domain="backend", task_type="coding", member_count=10,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post("/api/domains/dup-1/promote")
        assert resp.status_code == 409
        assert "backend" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_promote_sets_domain_field(self, app_client, db_session):
        """POST /api/domains/{id}/promote sets cluster.domain to cluster.label."""
        cluster = PromptCluster(
            id="dom-set-1", label="new-domain-label", state="active",
            domain="general", task_type="coding", member_count=6,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post("/api/domains/dom-set-1/promote")
        assert resp.status_code == 200

        await db_session.refresh(cluster)
        assert cluster.domain == "new-domain-label"

    @pytest.mark.asyncio
    async def test_promote_color_hex_is_valid(self, app_client, db_session):
        """Promoted domain gets a valid hex color."""
        cluster = PromptCluster(
            id="color-1", label="color-test-domain", state="active",
            domain="backend", task_type="coding", member_count=7,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post("/api/domains/color-1/promote")
        assert resp.status_code == 200
        color = resp.json()["color_hex"]
        assert color.startswith("#")
        assert len(color) == 7  # #rrggbb format


async def _get_seed_domain(db_session) -> PromptCluster:
    """The ``app_client`` fixture in ``conftest.py`` pre-seeds 8 domain nodes
    (backend, frontend, database, data, devops, security, fullstack, general).
    Grab the "backend" node — any domain-state node works for these tests.
    """
    result = await db_session.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.label == "backend",
        )
    )
    node = result.scalar_one()
    return node


@pytest.mark.asyncio
async def test_get_domain_readiness_history_returns_payload(
    app_client,
    db_session,
    tmp_path,
    monkeypatch,
):
    """GET /api/domains/{id}/readiness/history?window=24h returns response model."""
    # Redirect history dir to tmp_path
    from app.services.taxonomy import readiness_history
    monkeypatch.setattr(
        readiness_history, "_resolve_dir", lambda base_dir=None: tmp_path,
    )
    seeded_domain = await _get_seed_domain(db_session)

    resp = await app_client.get(
        f"/api/domains/{seeded_domain.id}/readiness/history?window=24h",
    )
    assert resp.status_code == 200
    body = ReadinessHistoryResponse.model_validate(resp.json())
    assert body.domain_id == seeded_domain.id
    assert body.window == "24h"
    assert body.bucketed is False
    assert isinstance(body.points, list)


@pytest.mark.asyncio
async def test_get_domain_readiness_history_rejects_bad_window(
    app_client,
    db_session,
):
    seeded_domain = await _get_seed_domain(db_session)
    resp = await app_client.get(
        f"/api/domains/{seeded_domain.id}/readiness/history?window=99h",
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_domain_readiness_history_404s_unknown_domain(
    app_client,
):
    resp = await app_client.get(
        "/api/domains/does-not-exist/readiness/history?window=24h",
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# R6: rebuild-sub-domains recovery endpoint (router tests)
# ---------------------------------------------------------------------------


class TestRebuildSubDomainsEndpoint:
    """R6 router contract for ``POST /api/domains/{id}/rebuild-sub-domains``.

    Error envelope (mirrors ``promote_to_domain``):
      404 — ``domain_id`` not found
      422 — node is not a domain (``state != "domain"``) OR ``min_consistency``
            outside Pydantic ``ge=0.25`` / ``le=1.0`` bounds
      503 — DB ``OperationalError`` (transient contention)
      500 — uncaught (logged with traceback)

    Acceptance criteria: AC-R6-1 / AC-R6-2 / AC-R6-3 / AC-R6-4 / AC-R6-5 /
    AC-R6-6.

    Pre-fix (R6 not merged): each test below fails with 404 (no route) or
    422 (no schema).
    """

    @pytest.mark.asyncio
    async def test_rebuild_endpoint_404_unknown_domain(
        self, app_client, db_session,
    ):
        """AC-R6-1: nonexistent domain id → 404 with the router's
        ``Domain not found`` envelope.

        The detail-string check distinguishes RED-phase
        ``404 Not Found`` (route absent) from GREEN-phase
        ``404 {"detail": "Domain not found"}`` (route present, id absent).
        Pre-fix the route doesn't exist, so FastAPI's default
        ``"Not Found"`` body fails the substring check.
        """
        resp = await app_client.post(
            "/api/domains/00000000-0000-0000-0000-000000000000/"
            "rebuild-sub-domains",
            json={},
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown domain id. Got: {resp.status_code} "
            f"body={resp.text}"
        )
        body = resp.json()
        detail = (body.get("detail") or "").lower()
        # GREEN-phase router emits "Domain not found"; RED returns the
        # FastAPI default "Not Found" without the ``Domain`` qualifier.
        assert "domain not found" in detail, (
            "Router 404 must surface a 'Domain not found' detail (RED "
            "phase: FastAPI default 'Not Found' lacks the qualifier). "
            f"Got body: {body!r}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_endpoint_422_non_domain_node(
        self, app_client, db_session,
    ):
        """AC-R6-2: non-domain node (state != ``domain``) → 422 with
        ``must be a domain``."""
        cluster = PromptCluster(
            id="rebuild-non-domain",
            label="some-active-cluster",
            state="active",
            domain="backend",
            task_type="coding",
            member_count=5,
            centroid_embedding=b"\x00" * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.post(
            f"/api/domains/{cluster.id}/rebuild-sub-domains",
            json={},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for non-domain node. Got: {resp.status_code} "
            f"body={resp.text}"
        )
        body = resp.json()
        # The router must surface a clear ``must be a domain``-style hint.
        # Allow either the canonical phrase or any synonym that conveys
        # "this id is not a domain node".
        detail = (body.get("detail") or "").lower()
        assert "must be a domain" in detail or "not a domain" in detail, (
            "422 body should explain that the node is not a domain. "
            f"Got body: {body!r}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_endpoint_422_min_consistency_above_range(
        self, app_client, db_session,
    ):
        """AC-R6-3: ``min_consistency=2.0`` → 422 (Pydantic ``le=1.0``)."""
        seed = await _get_seed_domain(db_session)
        resp = await app_client.post(
            f"/api/domains/{seed.id}/rebuild-sub-domains",
            json={"min_consistency": 2.0},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for min_consistency=2.0. Got: {resp.status_code} "
            f"body={resp.text}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_endpoint_422_min_consistency_below_floor(
        self, app_client, db_session,
    ):
        """AC-R6-4: ``min_consistency=0.10`` → 422 (Pydantic ``ge=0.25``,
        below ``SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR``)."""
        seed = await _get_seed_domain(db_session)
        resp = await app_client.post(
            f"/api/domains/{seed.id}/rebuild-sub-domains",
            json={"min_consistency": 0.10},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for min_consistency=0.10 (below 0.25 floor). "
            f"Got: {resp.status_code} body={resp.text}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_endpoint_200_dry_run(
        self, app_client, db_session,
    ):
        """AC-R6-5: dry-run on a valid domain → 200 with response shape
        matching ``RebuildSubDomainsResult``; ``dry_run is True`` and
        ``created == []``."""
        seed = await _get_seed_domain(db_session)
        resp = await app_client.post(
            f"/api/domains/{seed.id}/rebuild-sub-domains",
            json={"dry_run": True},
        )
        assert resp.status_code == 200, (
            f"Expected 200 for dry_run on valid domain. Got: {resp.status_code} "
            f"body={resp.text}"
        )
        body = resp.json()
        required = {
            "domain_id", "domain_label", "threshold_used",
            "proposed", "created", "skipped_existing", "dry_run",
        }
        missing = required - set(body.keys())
        assert not missing, (
            f"Response missing required keys: {sorted(missing)}. "
            f"Got body keys: {sorted(body.keys())}"
        )
        assert body["dry_run"] is True, (
            f"dry_run flag must echo back True. Got: {body['dry_run']!r}"
        )
        assert body["created"] == [], (
            f"Dry-run must yield created=[]. Got: {body['created']!r}"
        )

    @pytest.mark.asyncio
    async def test_rebuild_endpoint_200_idempotent_re_run(
        self, app_client, db_session,
    ):
        """AC-R6-6: a domain that already has matching sub-domains
        echoes them in ``skipped_existing``; ``created == []``.

        Setup: pre-create a sub-domain ``audit`` under ``backend``, then
        seed enough opts so the cascade would have proposed ``audit`` if
        it didn't exist. The first POST must list ``audit`` in
        ``skipped_existing``.
        """
        from app.models import Optimization

        seed = await _get_seed_domain(db_session)

        # Pre-create ``audit`` sub-domain under backend.
        sub = PromptCluster(
            id="rebuild-existing-audit",
            label="audit",
            state="domain",
            domain="backend",
            task_type="general",
            parent_id=seed.id,
            persistence=1.0,
            color_hex="#aabbcc",
            centroid_embedding=b"\x00" * 384,
        )
        db_session.add(sub)
        await db_session.flush()

        # Two distinct clusters carrying the audit qualifier (breadth=2).
        for i in range(2):
            cluster = PromptCluster(
                id=f"audit-cluster-{i}",
                label=f"audit-cluster-{i}",
                state="active",
                domain="backend",
                task_type="coding",
                member_count=5,
                centroid_embedding=b"\x00" * 384,
                parent_id=seed.id,
            )
            db_session.add(cluster)
            await db_session.flush()
            for j in range(3):
                db_session.add(Optimization(
                    raw_prompt=f"audit prompt {i}-{j}",
                    status="completed",
                    cluster_id=cluster.id,
                    domain_raw="backend: audit",
                    embedding=b"\x00" * (4 * 384),
                ))
        await db_session.commit()

        resp = await app_client.post(
            f"/api/domains/{seed.id}/rebuild-sub-domains",
            json={"min_consistency": 0.30},
        )
        assert resp.status_code == 200, (
            f"Expected 200 on idempotent re-run. Got: {resp.status_code} "
            f"body={resp.text}"
        )
        body = resp.json()
        assert "audit" in body["skipped_existing"], (
            "Pre-existing 'audit' sub-domain must appear in skipped_existing. "
            f"Got body: {body!r}"
        )
        assert "audit" not in body["created"], (
            "Pre-existing sub-domain must NOT be re-created. "
            f"Got created: {body['created']}"
        )
