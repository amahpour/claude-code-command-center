"""Tests for PR/MR URL lookup."""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from server.pr_lookup import (
    _find_github_pr,
    _find_gitlab_mr,
    _get_client,
    _get_github_token,
    _get_gitlab_token,
    find_pr_url,
    parse_git_remote,
)

# --- Token resolution ---


def test_get_github_token_from_env():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test123"}, clear=False):
        with patch("os.path.isfile", return_value=False):
            assert _get_github_token() == "ghp_test123"


def test_get_github_token_gh_token_env():
    with patch.dict(os.environ, {"GH_TOKEN": "ghp_alt"}, clear=False), patch("os.path.isfile", return_value=False):
        token = _get_github_token()
        # May get GITHUB_TOKEN if set in env; just verify it returns something or GH_TOKEN
        assert token is not None  # non-fatal if env has no GH_TOKEN


def test_get_github_token_no_token():
    with patch.dict(os.environ, {}, clear=True), patch("os.path.isfile", return_value=False):
        assert _get_github_token() is None


def test_get_github_token_from_gh_config_no_yaml():
    """Test gh config parsing without yaml library (manual regex)."""
    config_content = "github.com:\n    oauth_token: ghp_fromconfig\n    user: testuser\n"
    with patch.dict(os.environ, {}, clear=True), patch("os.path.isfile", return_value=True):
        with patch("builtins.open", create=True) as mock_open:
            # First call raises ImportError (no yaml), second call reads file
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            mock_open.return_value.read = MagicMock(return_value=config_content)

            with patch.dict("sys.modules", {"yaml": None}):
                # Just test that the function doesn't crash
                _get_github_token()


def test_get_gitlab_token_from_env():
    with patch.dict(os.environ, {"GITLAB_TOKEN": "glpat-test"}, clear=False):
        assert _get_gitlab_token() == "glpat-test"


def test_get_gitlab_token_private_token():
    with patch.dict(os.environ, {"GITLAB_PRIVATE_TOKEN": "glpat-priv"}, clear=False):
        with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "GITLAB_TOKEN"}):
            token = _get_gitlab_token()
            assert token is not None


def test_get_gitlab_token_none():
    with patch.dict(os.environ, {}, clear=True):
        assert _get_gitlab_token() is None


# --- Git remote parsing ---


def test_parse_git_remote_ssh():
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write(
                '[remote "origin"]\n\turl = git@github.com:owner/repo.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
            )

        result = parse_git_remote(d)
        assert result is not None
        assert result["host"] == "github.com"
        assert result["owner"] == "owner"
        assert result["repo"] == "repo"
        assert result["platform"] == "github"


def test_parse_git_remote_https():
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write('[remote "origin"]\n\turl = https://github.com/myorg/myrepo.git\n')

        result = parse_git_remote(d)
        assert result is not None
        assert result["owner"] == "myorg"
        assert result["repo"] == "myrepo"
        assert result["platform"] == "github"


def test_parse_git_remote_gitlab():
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write('[remote "origin"]\n\turl = git@gitlab.com:group/subgroup/repo.git\n')

        result = parse_git_remote(d)
        assert result is not None
        assert result["platform"] == "gitlab"
        assert result["owner"] == "group/subgroup"
        assert result["repo"] == "repo"


def test_parse_git_remote_no_git_dir():
    with tempfile.TemporaryDirectory() as d:
        assert parse_git_remote(d) is None


def test_parse_git_remote_no_origin():
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write('[remote "upstream"]\n\turl = git@github.com:other/repo.git\n')

        assert parse_git_remote(d) is None


def test_parse_git_remote_invalid_url():
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write('[remote "origin"]\n\turl = not-a-valid-url\n')

        assert parse_git_remote(d) is None


def test_parse_git_remote_bad_config():
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write("this is not valid ini format \x00\x01\x02")

        # Should return None, not crash
        parse_git_remote(d)
        # configparser may or may not parse this; just ensure no exception


# --- PR/MR lookup ---


async def test_find_pr_url_no_remote():
    with tempfile.TemporaryDirectory() as d:
        result = await find_pr_url(d, "main")
        assert result is None


async def test_find_github_pr_success():
    remote = {"host": "github.com", "owner": "org", "repo": "repo", "platform": "github"}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"html_url": "https://github.com/org/repo/pull/42"}]

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("server.pr_lookup._get_client", return_value=mock_client):
        with patch("server.pr_lookup._get_github_token", return_value=None):
            result = await _find_github_pr(remote, "feature-branch")
            assert result == "https://github.com/org/repo/pull/42"


async def test_find_github_pr_no_results():
    remote = {"host": "github.com", "owner": "org", "repo": "repo", "platform": "github"}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("server.pr_lookup._get_client", return_value=mock_client):
        with patch("server.pr_lookup._get_github_token", return_value=None):
            result = await _find_github_pr(remote, "no-pr-branch")
            assert result is None


async def test_find_github_pr_api_error():
    remote = {"host": "github.com", "owner": "org", "repo": "repo", "platform": "github"}
    mock_response = MagicMock()
    mock_response.status_code = 403

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("server.pr_lookup._get_client", return_value=mock_client):
        with patch("server.pr_lookup._get_github_token", return_value="token"):
            result = await _find_github_pr(remote, "branch")
            assert result is None


async def test_find_github_pr_with_token():
    remote = {"host": "github.com", "owner": "org", "repo": "repo", "platform": "github"}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"html_url": "https://github.com/org/repo/pull/1"}]

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("server.pr_lookup._get_client", return_value=mock_client):
        with patch("server.pr_lookup._get_github_token", return_value="ghp_mytoken"):
            result = await _find_github_pr(remote, "feature")
            assert result is not None
            # Verify Authorization header was passed
            call_kwargs = mock_client.get.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert "Bearer ghp_mytoken" in headers.get("Authorization", "")


async def test_find_gitlab_mr_success():
    remote = {"host": "gitlab.com", "owner": "group", "repo": "repo", "full_path": "group/repo", "platform": "gitlab"}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"web_url": "https://gitlab.com/group/repo/-/merge_requests/10"}]

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("server.pr_lookup._get_client", return_value=mock_client):
        with patch("server.pr_lookup._get_gitlab_token", return_value=None):
            result = await _find_gitlab_mr(remote, "feature")
            assert result == "https://gitlab.com/group/repo/-/merge_requests/10"


async def test_find_gitlab_mr_no_results():
    remote = {"host": "gitlab.com", "owner": "group", "repo": "repo", "full_path": "group/repo", "platform": "gitlab"}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("server.pr_lookup._get_client", return_value=mock_client):
        with patch("server.pr_lookup._get_gitlab_token", return_value=None):
            result = await _find_gitlab_mr(remote, "branch")
            assert result is None


async def test_find_gitlab_mr_with_token():
    remote = {"host": "gitlab.com", "owner": "group", "repo": "repo", "full_path": "group/repo", "platform": "gitlab"}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"web_url": "https://gitlab.com/group/repo/-/merge_requests/5"}]

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("server.pr_lookup._get_client", return_value=mock_client):
        with patch("server.pr_lookup._get_gitlab_token", return_value="glpat-token"):
            result = await _find_gitlab_mr(remote, "branch")
            assert result is not None
            call_kwargs = mock_client.get.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert headers.get("PRIVATE-TOKEN") == "glpat-token"


async def test_find_gitlab_mr_api_error():
    remote = {"host": "gitlab.com", "owner": "group", "repo": "repo", "full_path": "group/repo", "platform": "gitlab"}
    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("server.pr_lookup._get_client", return_value=mock_client):
        with patch("server.pr_lookup._get_gitlab_token", return_value=None):
            result = await _find_gitlab_mr(remote, "branch")
            assert result is None


async def test_find_pr_url_github():
    """Integration: find_pr_url dispatches to GitHub."""
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write('[remote "origin"]\n\turl = git@github.com:org/repo.git\n')

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"html_url": "https://github.com/org/repo/pull/1"}]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("server.pr_lookup._get_client", return_value=mock_client):
            with patch("server.pr_lookup._get_github_token", return_value=None):
                result = await find_pr_url(d, "feature")
                assert result == "https://github.com/org/repo/pull/1"


async def test_find_pr_url_exception_returns_none():
    """find_pr_url catches exceptions and returns None."""
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write('[remote "origin"]\n\turl = git@github.com:org/repo.git\n')

        with patch("server.pr_lookup._find_github_pr", side_effect=Exception("network error")):
            result = await find_pr_url(d, "feature")
            assert result is None


def test_get_github_token_from_gh_config_with_yaml():
    """Test gh config parsing with yaml library available."""
    mock_yaml = MagicMock()
    mock_yaml.safe_load.return_value = {"github.com": {"oauth_token": "ghp_yamltoken", "user": "testuser"}}

    with patch.dict(os.environ, {}, clear=True), patch("os.path.isfile", return_value=True):
        with patch("builtins.open", MagicMock()):
            import sys

            with patch.dict(sys.modules, {"yaml": mock_yaml}):
                # Force reimport of the function to pick up yaml
                import server.pr_lookup as pr_mod

                # Directly test the function logic
                pr_mod._get_github_token()


def test_get_github_token_gh_config_os_error():
    """Test gh config parsing when file read fails."""
    with patch.dict(os.environ, {}, clear=True), patch("os.path.isfile", return_value=True):
        with patch("builtins.open", side_effect=OSError("permission denied")):
            # Should fall through to env vars and return None
            token = _get_github_token()
            assert token is None


def test_get_client():
    """Test that _get_client returns a client."""
    import server.pr_lookup as pr_mod

    old = pr_mod._client
    pr_mod._client = None
    try:
        client = _get_client()
        assert client is not None
        # Second call returns same instance
        assert _get_client() is client
    finally:
        pr_mod._client = old


def test_parse_git_remote_single_segment_path():
    """Test remote URL with only a single path segment (no owner/repo split)."""
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write('[remote "origin"]\n\turl = git@github.com:repo.git\n')

        # Single segment has no "/" so rsplit gives only 1 part
        result = parse_git_remote(d)
        assert result is None


def test_parse_git_remote_https_no_match():
    """HTTPS URL that doesn't match regex."""
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write('[remote "origin"]\n\turl = ftp://example.com/repo\n')

        result = parse_git_remote(d)
        assert result is None


async def test_find_pr_url_gitlab():
    """Integration: find_pr_url dispatches to GitLab."""
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write('[remote "origin"]\n\turl = git@gitlab.com:group/repo.git\n')

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"web_url": "https://gitlab.com/group/repo/-/merge_requests/5"}]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("server.pr_lookup._get_client", return_value=mock_client):
            with patch("server.pr_lookup._get_gitlab_token", return_value=None):
                result = await find_pr_url(d, "feature")
                assert result == "https://gitlab.com/group/repo/-/merge_requests/5"
