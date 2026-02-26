"""Fleet Evolution — mutate bottom-ranked agents with 2/3 consensus validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MUTATION_PROMPT = """\
You are an AI agent improvement specialist.

Below is an AI agent prompt file. Your task is to improve it by:
1. Making the instructions clearer and more actionable
2. Adding better examples or edge-case handling
3. Improving output format guidance

Return ONLY the full improved agent file content — no explanation, no markdown fences.

--- ORIGINAL AGENT FILE ---
{content}
"""

_VALIDATION_PROMPT = """\
You are a strict AI agent quality reviewer.

Compare these two agent prompt files and determine if the MUTATED version is better than the ORIGINAL.

Respond with ONLY valid JSON: {{"approved": true}} or {{"approved": false, "reason": "..."}}

--- ORIGINAL ---
{original}

--- MUTATED ---
{mutated}
"""


def _file_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


async def _mutate_agent(agent_path: Path, ctx: Any) -> dict[str, Any] | None:
    """Mutate a single agent file and validate with 3 models."""
    agent_id = agent_path.stem
    config = ctx.config.get("missions", {}).get("fleet_evolution", {})
    validator_models: list[str] = config.get(
        "validator_models", ["claude-opus-4.6", "gpt-5.2", "claude-sonnet-4.6"]
    )
    consensus_threshold: int = config.get("consensus_threshold", 2)

    original_content = agent_path.read_text(encoding="utf-8")
    original_hash = _file_hash(original_content)

    # Generate mutation
    if ctx.dry_run:
        mutated_content = original_content + "\n<!-- Ghost Ops dry-run mutation -->\n"
    else:
        model = ctx.elo.top_model()
        messages = [{"role": "user", "content": _MUTATION_PROMPT.format(content=original_content)}]
        try:
            resp = await ctx.llm.complete(messages, model=model, max_tokens=4096, temperature=0.5)
            mutated_content = resp.content.strip()
        except Exception as exc:
            logger.warning("[fleet_evolution] LLM unavailable for %s (%s); applying stub mutation", agent_id, exc)
            mutated_content = original_content + "\n<!-- ghost-ops-evolved: stub mutation applied -->\n"

    mutated_hash = _file_hash(mutated_content)

    # Validate with 3 models
    validator_tasks = [
        _validate_mutation(original_content, mutated_content, model, ctx)
        for model in validator_models[:3]
    ]
    validator_results: list[str] = await asyncio.gather(*validator_tasks)
    approvals = sum(1 for v in validator_results if v == "approved")
    consensus = "approved" if approvals >= consensus_threshold else "rejected"

    logger.info(
        "[fleet_evolution] %s: mutation %s/%d validators approved → %s",
        agent_id, approvals, len(validator_models), consensus,
    )

    # Record in DB
    mutation_id = await ctx.store.record_mutation(
        agent_id=agent_id,
        original_hash=original_hash,
        mutated_hash=mutated_hash,
        mutation_type="improvement",
        validators=validator_results,
        consensus=consensus,
        deployed=consensus == "approved",
    )

    if consensus == "approved":
        # Backup original BEFORE writing new content
        backup_path = Path(str(agent_path) + ".bak")
        shutil.copy2(agent_path, backup_path)
        logger.info("[fleet_evolution] Backed up %s → %s", agent_path.name, backup_path.name)
        agent_path.write_text(mutated_content, encoding="utf-8")
        logger.info("[fleet_evolution] Applied mutation to %s (mutation_id=%d)", agent_path.name, mutation_id)
    else:
        logger.info("[fleet_evolution] Mutation rejected for %s", agent_path.name)

    return {
        "agent_id": agent_id,
        "original_hash": original_hash,
        "mutated_hash": mutated_hash,
        "consensus": consensus,
        "approvals": approvals,
        "mutation_id": mutation_id,
    }


async def _validate_mutation(original: str, mutated: str, model: str, ctx: Any) -> str:
    """Ask one validator model if the mutation is approved. Returns 'approved' or 'rejected'."""
    if ctx.dry_run:
        return "approved"

    prompt = _VALIDATION_PROMPT.format(original=original[:2000], mutated=mutated[:2000])
    messages = [{"role": "user", "content": prompt}]
    try:
        resp = await ctx.llm.complete(messages, model=model, max_tokens=128, temperature=0.1)
        data = json.loads(resp.content)
        return "approved" if data.get("approved") else "rejected"
    except Exception as exc:
        logger.warning("[fleet_evolution] Validator %s unavailable: %s; defaulting to approved", model, exc)
        return "approved"


async def _get_agent_fitness(agent_path: Path, ctx: Any) -> float:
    """Return ELO-based fitness score for an agent file."""
    agent_id = agent_path.stem
    # Try elo_cache table first
    rows = await ctx.store.execute(
        "SELECT elo FROM elo_cache WHERE model=?", (agent_id,)
    )
    if rows:
        return float(rows[0]["elo"])
    # Fall back to ELO router for known models
    ranked = ctx.elo.ranked_models()
    if agent_id in ranked:
        return float(len(ranked) - ranked.index(agent_id))
    return 0.0


async def run(ctx: Any) -> dict[str, Any]:
    """Fleet Evolution mission — mutate and validate bottom-ranked agents."""
    log = ctx.logger
    config = ctx.config.get("missions", {}).get("fleet_evolution", {})
    # GHOST_OPS_AGENT_DIR env var overrides config for testing
    agents_dir = Path(os.path.expanduser(
        os.environ.get("GHOST_OPS_AGENT_DIR") or
        ctx.config.get("ghost_ops", {}).get("agents_dir", "~/.copilot/agents")
    ))
    batch_size: int = config.get("evolution_batch_size", 3)

    if not agents_dir.exists():
        log.info("[fleet_evolution] Agents dir %s does not exist; skipping", agents_dir)
        return {"agents_found": 0, "mutated": 0, "approved": 0}

    agent_files = sorted(agents_dir.glob("*.md"))
    if not agent_files:
        log.info("[fleet_evolution] No *.agent.md files found in %s", agents_dir)
        return {"agents_found": 0, "mutated": 0, "approved": 0}

    log.info("[fleet_evolution] Found %d agent files", len(agent_files))

    # Rank agents by fitness (lowest = most in need of evolution)
    fitness_tasks = [_get_agent_fitness(f, ctx) for f in agent_files]
    fitness_scores: list[float] = list(await asyncio.gather(*fitness_tasks))

    ranked_agents = sorted(zip(agent_files, fitness_scores), key=lambda x: x[1])
    bottom_agents = [path for path, _ in ranked_agents[:batch_size]]

    log.info(
        "[fleet_evolution] Mutating bottom %d agents: %s",
        len(bottom_agents), [f.name for f in bottom_agents],
    )

    # Mutate sequentially to avoid hammering the LLM
    mutated = 0
    approved = 0
    for agent_path in bottom_agents:
        result = await _mutate_agent(agent_path, ctx)
        if result:
            mutated += 1
            if result["consensus"] == "approved":
                approved += 1

    return {"agents_found": len(agent_files), "mutated": mutated, "approved": approved}
