"""Tests for the /api/domains router."""

import pytest

from app.models import PromptCluster


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
        # conftest seeds 7 domain nodes
        assert len(data) == 7
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
        assert len(color) == 7
