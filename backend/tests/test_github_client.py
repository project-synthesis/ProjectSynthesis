import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.github_client import GitHubClient

def make_mock_response(status_code=200, json_data=None):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data or {}
    def raise_for_status():
        if status_code >= 400:
            raise Exception("HTTP Error")
    mock_resp.raise_for_status.side_effect = raise_for_status
    return mock_resp

@pytest.fixture
def mock_httpx_client():
    client = AsyncMock()
    return client

@pytest.fixture
def github_client(mock_httpx_client):
    return GitHubClient(http_client=mock_httpx_client)

@pytest.mark.asyncio
async def test_get_user(github_client, mock_httpx_client):
    mock_httpx_client.get.return_value = make_mock_response(200, {"login": "testuser"})
    res = await github_client.get_user("fake_token")
    assert res == {"login": "testuser"}

@pytest.mark.asyncio
async def test_list_repos(github_client, mock_httpx_client):
    mock_httpx_client.get.return_value = make_mock_response(200, [{"name": "repo1"}])
    res = await github_client.list_repos("fake_token", per_page=10, page=2)
    assert res == [{"name": "repo1"}]

@pytest.mark.asyncio
async def test_get_repo(github_client, mock_httpx_client):
    mock_httpx_client.get.return_value = make_mock_response(200, {"name": "repo1"})
    res = await github_client.get_repo("fake_token", "user/repo1")
    assert res == {"name": "repo1"}

@pytest.mark.asyncio
async def test_get_branch_and_sha(github_client, mock_httpx_client):
    mock_httpx_client.get.return_value = make_mock_response(200, {"commit": {"sha": "1234abcd"}})
    res = await github_client.get_branch("fake_token", "user/repo1", "main")
    assert res == {"commit": {"sha": "1234abcd"}}
    
    sha = await github_client.get_branch_head_sha("fake_token", "user/repo1", "main")
    assert sha == "1234abcd"

@pytest.mark.asyncio
async def test_get_tree(github_client, mock_httpx_client):
    mock_httpx_client.get.return_value = make_mock_response(200, {"tree": [{"type": "blob", "path": "file1"}, {"type": "tree", "path": "dir1"}]})
    res = await github_client.get_tree("fake_token", "user/repo1", "main")
    assert res == [{"type": "blob", "path": "file1"}]

@pytest.mark.asyncio
async def test_get_file_content_404(github_client, mock_httpx_client):
    mock_httpx_client.get.return_value = make_mock_response(404)
    res = await github_client.get_file_content("fake_token", "user/repo1", "path/to/file", "main")
    assert res is None

@pytest.mark.asyncio
async def test_get_file_content_base64(github_client, mock_httpx_client):
    import base64
    content = base64.b64encode(b"hello world").decode('ascii')
    mock_httpx_client.get.return_value = make_mock_response(200, {"encoding": "base64", "content": content})
    res = await github_client.get_file_content("fake_token", "user/repo1", "path/to/file", "main")
    assert res == "hello world"

@pytest.mark.asyncio
async def test_get_file_content_plain(github_client, mock_httpx_client):
    mock_httpx_client.get.return_value = make_mock_response(200, {"encoding": "plain", "content": "plain text"})
    res = await github_client.get_file_content("fake_token", "user/repo1", "path/to/file", "main")
    assert res == "plain text"

@pytest.mark.asyncio
async def test_get_file_content_no_encoding(github_client, mock_httpx_client):
    mock_httpx_client.get.return_value = make_mock_response(200, {"content": "default text"})
    res = await github_client.get_file_content("fake_token", "user/repo1", "path/to/file", "main")
    assert res == "default text"
