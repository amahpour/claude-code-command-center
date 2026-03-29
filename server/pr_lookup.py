"""Look up pull/merge request URLs from git remote and branch name.

Auth token resolution order:
1. gh CLI config (~/.config/gh/hosts.yml) — for GitHub
2. Environment variables (GITHUB_TOKEN / GH_TOKEN / GITLAB_TOKEN)
3. Unauthenticated (works for public repos)
"""

import configparser
import logging
import os
import re
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


def _get_github_token(host: str = "github.com") -> str | None:
    """Resolve GitHub token: gh CLI config first, then env vars."""
    # Try gh CLI config
    gh_config = os.path.expanduser("~/.config/gh/hosts.yml")
    if os.path.isfile(gh_config):
        try:
            import yaml
            with open(gh_config) as f:
                hosts = yaml.safe_load(f)
            if hosts and host in hosts:
                token = hosts[host].get("oauth_token")
                if token:
                    return token
        except ImportError:
            # No yaml library — parse manually for simple case
            try:
                with open(gh_config) as f:
                    content = f.read()
                # Simple pattern: find host section and extract oauth_token
                pattern = rf"{re.escape(host)}:\s*\n\s+.*?oauth_token:\s*(.+)"
                m = re.search(pattern, content)
                if m:
                    return m.group(1).strip()
            except OSError:
                pass
        except OSError:
            pass

    # Fall back to env vars
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _get_gitlab_token() -> str | None:
    """Resolve GitLab token from env vars."""
    return os.environ.get("GITLAB_TOKEN") or os.environ.get("GITLAB_PRIVATE_TOKEN")


def parse_git_remote(project_path: str) -> dict | None:
    """Parse the git remote origin URL from a project's .git/config.

    Returns {"host": ..., "owner": ..., "repo": ..., "platform": "github"|"gitlab"}
    or None if not parseable.
    """
    git_config = os.path.join(project_path, ".git", "config")
    if not os.path.isfile(git_config):
        return None

    config = configparser.ConfigParser()
    try:
        config.read(git_config)
    except configparser.Error:
        return None

    url = None
    for section in config.sections():
        if section == 'remote "origin"' and "url" in config[section]:
            url = config[section]["url"]
            break

    if not url:
        return None

    # SSH: git@github.com:owner/repo.git
    m = re.match(r"git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        host, owner, repo = m.group(1), m.group(2), m.group(3)
    else:
        # HTTPS: https://github.com/owner/repo.git
        m = re.match(r"https?://([^/]+)/([^/]+)/([^/]+?)(?:\.git)?$", url)
        if not m:
            return None
        host, owner, repo = m.group(1), m.group(2), m.group(3)

    platform = "gitlab" if "gitlab" in host.lower() else "github"
    return {"host": host, "owner": owner, "repo": repo, "platform": platform}


async def find_pr_url(project_path: str, branch: str) -> str | None:
    """Find an open PR/MR for the given branch by querying GitHub or GitLab API."""
    remote = parse_git_remote(project_path)
    if not remote:
        return None

    try:
        if remote["platform"] == "github":
            return await _find_github_pr(remote, branch)
        else:
            return await _find_gitlab_mr(remote, branch)
    except Exception:
        logger.debug("PR lookup failed for %s/%s branch %s",
                     remote["owner"], remote["repo"], branch, exc_info=True)
        return None


async def _find_github_pr(remote: dict, branch: str) -> str | None:
    """Query GitHub API for an open PR on the given branch."""
    client = _get_client()
    owner, repo = remote["owner"], remote["repo"]
    api_host = f"api.{remote['host']}" if remote["host"] == "github.com" else remote["host"]
    url = f"https://{api_host}/repos/{owner}/{repo}/pulls"
    params = {"head": f"{owner}:{branch}", "state": "open", "per_page": 1}

    headers = {"Accept": "application/vnd.github+json"}
    token = _get_github_token(remote["host"])
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = await client.get(url, params=params, headers=headers)
    if resp.status_code != 200:
        return None

    prs = resp.json()
    if prs:
        return prs[0].get("html_url")
    return None


async def _find_gitlab_mr(remote: dict, branch: str) -> str | None:
    """Query GitLab API for an open MR on the given branch."""
    client = _get_client()
    owner, repo = remote["owner"], remote["repo"]
    project_id = quote(f"{owner}/{repo}", safe="")
    url = f"https://{remote['host']}/api/v4/projects/{project_id}/merge_requests"
    params = {"source_branch": branch, "state": "opened", "per_page": 1}

    headers = {}
    token = _get_gitlab_token()
    if token:
        headers["PRIVATE-TOKEN"] = token

    resp = await client.get(url, params=params, headers=headers)
    if resp.status_code != 200:
        return None

    mrs = resp.json()
    if mrs:
        return mrs[0].get("web_url")
    return None
