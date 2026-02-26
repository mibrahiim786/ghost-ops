"""Inbox Autopilot — classify and draft responses for new issues and PRs."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_CLASSIFICATION_PROMPT = """\
You are a GitHub repository triage assistant.

Classify the following GitHub issue or PR into exactly one category:
  bug | feature-request | question | spam | other

Then draft a brief, friendly response (2-4 sentences).

Respond ONLY with valid JSON in this exact format:
{{"category": "<category>", "draft_response": "<response>"}}

---
Title: {title}
Body: {body}
"""

_STUB_CLASSIFICATION = {
    "category": "other",
    "draft_response": "DRY-RUN: Thank you for your submission. This is a stub response.",
}


async def _gh_api(path: str, method: str = "GET", body: str | None = None) -> Any:
    """Run `gh api` and return parsed JSON, or None on error."""
    args = ["gh", "api", "--method", method]
    if body:
        args += ["--input", "-"]
    args.append(path)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if body else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdin_data = body.encode() if body else None
    stdout, stderr = await proc.communicate(input=stdin_data)

    if proc.returncode != 0:
        logger.debug("gh api %s %s failed: %s", method, path, stderr.decode().strip())
        return None
    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        return None


async def _classify_item(item: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Use LLM to classify a single issue/PR and draft a response."""
    title = item.get("title", "")
    body = (item.get("body") or "")[:2000]

    if ctx.dry_run:
        return _STUB_CLASSIFICATION

    prompt = _CLASSIFICATION_PROMPT.format(title=title, body=body)
    messages = [{"role": "user", "content": prompt}]

    try:
        model = ctx.elo.top_model()
        response = await ctx.llm.complete(messages, model=model, max_tokens=512, temperature=0.3)
        data = json.loads(response.content)
        if "category" not in data or "draft_response" not in data:
            raise ValueError("Missing required keys")
        return data
    except Exception as exc:
        logger.warning("Classification failed for #%s: %s", item.get("number"), exc)
        return {"category": "other", "draft_response": "Unable to classify at this time."}


async def _process_repo(repo: str, last_run_iso: str | None, ctx: Any) -> dict[str, Any]:
    """Fetch and process new issues/PRs for a single repo."""
    since_param = f"&since={last_run_iso}" if last_run_iso else ""
    issues_raw = await _gh_api(
        f"repos/{repo}/issues?state=open&per_page=50{since_param}"
    )
    if not isinstance(issues_raw, list):
        return {"repo": repo, "processed": 0, "errors": 1}

    # Deduplicate by node_id
    seen_node_ids: set[str] = set()
    items = []
    for item in issues_raw:
        node_id = item.get("node_id", "")
        if node_id and node_id in seen_node_ids:
            continue
        seen_node_ids.add(node_id)
        items.append(item)

    processed = 0
    for item in items:
        try:
            classification = await _classify_item(item, ctx)
            item_type = "PR" if item.get("pull_request") else "issue"
            title = f"[{classification['category'].upper()}] {repo} #{item['number']}: {item['title']}"
            detail = json.dumps({
                "type": item_type,
                "number": item["number"],
                "url": item.get("html_url"),
                "category": classification["category"],
                "draft_response": classification["draft_response"],
                "node_id": item.get("node_id"),
            })
            await ctx.store.write_alert(
                severity="INFO",
                source="inbox_autopilot",
                title=title,
                detail=detail,
                repo=repo,
            )
            processed += 1
            logger.info("[inbox_autopilot] %s #%s → %s", repo, item["number"], classification["category"])
        except Exception as exc:
            logger.warning("[inbox_autopilot] Error processing %s #%s: %s", repo, item.get("number"), exc)

    return {"repo": repo, "processed": processed, "errors": 0}


async def run(ctx: Any) -> dict[str, Any]:
    """Inbox Autopilot mission — classify and draft responses for new items."""
    log = ctx.logger
    config = ctx.config.get("missions", {}).get("inbox_autopilot", {})
    repos: list[str] = config.get("repos", [])

    if not repos:
        log.info("[inbox_autopilot] No repos configured")
        return {"repos_processed": 0, "items_processed": 0}

    if ctx.dry_run:
        log.info("[inbox_autopilot] dry_run mode: skipping all API calls for %d repos", len(repos))
        return {"repos_processed": len(repos), "items_processed": 0}

    # Get last run time for this mission from DB
    rows = await ctx.store.execute(
        "SELECT last_run FROM missions WHERE id='inbox_autopilot'"
    )
    last_run_iso: str | None = rows[0]["last_run"] if rows else None

    log.info("[inbox_autopilot] Processing %d repos (since=%s)", len(repos), last_run_iso or "beginning")

    tasks = [_process_repo(repo, last_run_iso, ctx) for repo in repos]
    results: list[Any] = await asyncio.gather(*tasks, return_exceptions=True)

    total_processed = 0
    for repo, result in zip(repos, results):
        if isinstance(result, BaseException):
            log.warning("[inbox_autopilot] %s raised: %s", repo, result)
        else:
            total_processed += result.get("processed", 0)

    return {"repos_processed": len(repos), "items_processed": total_processed}
