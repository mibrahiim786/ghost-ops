"""Portfolio Watchdog — nightly security and compliance scan of watched repos."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _gh_api(path: str) -> dict[str, Any] | list[Any] | None:
    """Run `gh api <path>` and return parsed JSON, or None on error."""
    proc = await asyncio.create_subprocess_exec(
        "gh", "api", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.debug("gh api %s failed: %s", path, stderr.decode().strip())
        return None
    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        return None


@dataclass
class RepoFindings:
    repo: str
    dependabot_alerts: int = 0
    code_scanning_enabled: bool = False
    secret_scanning_enabled: bool = False
    push_protection_enabled: bool = False
    days_since_commit: int | None = None
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


# ---------------------------------------------------------------------------
# Per-repo scan
# ---------------------------------------------------------------------------

async def _scan_repo(repo: str, dry_run: bool) -> RepoFindings:
    findings = RepoFindings(repo=repo)

    if dry_run:
        findings.dependabot_alerts = 0
        findings.code_scanning_enabled = True
        findings.secret_scanning_enabled = True
        findings.push_protection_enabled = True
        findings.days_since_commit = 1
        return findings

    owner, name = (repo.split("/", 1) + [""])[:2]
    if not owner or not name:
        findings.errors.append(f"Invalid repo format: {repo!r}")
        return findings

    # Dependabot alerts (open)
    alerts_data = await _gh_api(f"repos/{repo}/dependabot/alerts?state=open&per_page=1")
    if isinstance(alerts_data, list):
        # GitHub returns full list; just count
        all_alerts = await _gh_api(f"repos/{repo}/dependabot/alerts?state=open&per_page=100")
        findings.dependabot_alerts = len(all_alerts) if isinstance(all_alerts, list) else 0

    # Security features
    security_data = await _gh_api(f"repos/{repo}/security-advisories")
    repo_meta = await _gh_api(f"repos/{repo}")
    if isinstance(repo_meta, dict):
        ss = repo_meta.get("security_and_analysis") or {}
        secret_scan = ss.get("secret_scanning", {})
        findings.secret_scanning_enabled = secret_scan.get("status") == "enabled"
        push_prot = ss.get("secret_scanning_push_protection", {})
        findings.push_protection_enabled = push_prot.get("status") == "enabled"

    # Code scanning
    code_scan = await _gh_api(f"repos/{repo}/code-scanning/analyses?per_page=1")
    findings.code_scanning_enabled = isinstance(code_scan, list) and len(code_scan) > 0

    # Last commit date
    commits = await _gh_api(f"repos/{repo}/commits?per_page=1")
    if isinstance(commits, list) and commits:
        try:
            from datetime import datetime, timezone
            commit_date_str = commits[0]["commit"]["committer"]["date"]
            commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
            now = datetime.now(tz=timezone.utc)
            findings.days_since_commit = (now - commit_date).days
        except (KeyError, ValueError):
            pass

    return findings


# ---------------------------------------------------------------------------
# Mission entry point
# ---------------------------------------------------------------------------

async def run(ctx: Any) -> dict[str, Any]:
    """Portfolio Watchdog mission — scan all watched repos."""
    log = ctx.logger
    store = ctx.store
    config = ctx.config.get("missions", {}).get("portfolio_watchdog", {})

    # Collect repos: config list + DB watched_repos
    config_repos: list[str] = config.get("repos", [])
    db_repos = await store.get_watched_repos()
    db_repo_names = [r["repo"] for r in db_repos]

    all_repos = list(dict.fromkeys(config_repos + db_repo_names))  # deduplicate, preserve order

    if not all_repos:
        log.info("[portfolio_watchdog] No repos to scan")
        return {"scanned": 0, "alerts_written": 0}

    log.info("[portfolio_watchdog] Scanning %d repos", len(all_repos))

    # Concurrent scan with per-repo isolation
    tasks = [_scan_repo(repo, ctx.dry_run) for repo in all_repos]
    results: list[RepoFindings | BaseException] = await asyncio.gather(*tasks, return_exceptions=True)

    alerts_written = 0
    scan_results = []

    for repo, result in zip(all_repos, results):
        if isinstance(result, BaseException):
            log.warning("[portfolio_watchdog] %s scan raised: %s", repo, result)
            await store.write_alert(
                severity="WARN",
                source="portfolio_watchdog",
                title=f"Scan error for {repo}",
                detail=str(result),
                repo=repo,
            )
            alerts_written += 1
            continue

        f = result
        scan_results.append({"repo": repo, "findings": f.__dict__})

        # Update last_scanned in DB
        await store.upsert_watched_repo(repo, last_scanned="CURRENT_TIMESTAMP")

        # --- Alert rules ---
        if f.dependabot_alerts >= 10:
            await store.write_alert(
                severity="CRITICAL",
                source="portfolio_watchdog",
                title=f"{repo}: {f.dependabot_alerts} open Dependabot alerts",
                detail=f"High number of open Dependabot alerts ({f.dependabot_alerts})",
                repo=repo,
            )
            alerts_written += 1
        elif f.dependabot_alerts >= 3:
            await store.write_alert(
                severity="WARN",
                source="portfolio_watchdog",
                title=f"{repo}: {f.dependabot_alerts} open Dependabot alerts",
                detail=f"Multiple open Dependabot alerts ({f.dependabot_alerts})",
                repo=repo,
            )
            alerts_written += 1

        if not f.code_scanning_enabled:
            await store.write_alert(
                severity="WARN",
                source="portfolio_watchdog",
                title=f"{repo}: Code scanning not enabled",
                repo=repo,
            )
            alerts_written += 1

        if not f.secret_scanning_enabled:
            await store.write_alert(
                severity="WARN",
                source="portfolio_watchdog",
                title=f"{repo}: Secret scanning not enabled",
                repo=repo,
            )
            alerts_written += 1

        if not f.push_protection_enabled:
            await store.write_alert(
                severity="WARN",
                source="portfolio_watchdog",
                title=f"{repo}: Push protection not enabled",
                repo=repo,
            )
            alerts_written += 1

        if f.days_since_commit is not None and f.days_since_commit > 180:
            await store.write_alert(
                severity="WARN",
                source="portfolio_watchdog",
                title=f"{repo}: Stale — {f.days_since_commit} days since last commit",
                repo=repo,
            )
            alerts_written += 1

        log.info(
            "[portfolio_watchdog] %s: alerts=%d code_scan=%s secret=%s push=%s days_inactive=%s",
            repo, f.dependabot_alerts, f.code_scanning_enabled,
            f.secret_scanning_enabled, f.push_protection_enabled, f.days_since_commit,
        )

    return {"scanned": len(all_repos), "alerts_written": alerts_written, "results": scan_results}
