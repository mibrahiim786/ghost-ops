"""Unit tests for StateStore — uses :memory: SQLite, no network."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.state import StateStore


def run(coro):
    """Helper: run a coroutine in a fresh event loop."""
    return asyncio.run(coro)


class TestSchemaCreation(unittest.TestCase):
    """Schema auto-creates all 6 tables."""

    def setUp(self) -> None:
        self.store = StateStore(":memory:")
        self.store.open()

    def tearDown(self) -> None:
        self.store.close()

    def test_all_tables_exist(self) -> None:
        rows = run(self.store.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ))
        table_names = {r["name"] for r in rows}
        expected = {"missions", "runs", "alerts", "watched_repos", "mutations", "elo_cache"}
        self.assertTrue(expected.issubset(table_names), f"Missing tables: {expected - table_names}")

    def test_double_open_idempotent(self) -> None:
        """Opening again (IF NOT EXISTS) should not raise."""
        self.store.open()
        rows = run(self.store.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        self.assertGreater(len(rows), 0)


class TestMissions(unittest.TestCase):
    """CRUD for missions table."""

    def setUp(self) -> None:
        self.store = StateStore(":memory:")
        self.store.open()

    def tearDown(self) -> None:
        self.store.close()

    def test_upsert_mission(self) -> None:
        run(self.store.upsert_mission("portfolio_watchdog", "0 6 * * *"))
        rows = run(self.store.execute("SELECT * FROM missions WHERE id='portfolio_watchdog'"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["schedule"], "0 6 * * *")

    def test_upsert_mission_idempotent(self) -> None:
        run(self.store.upsert_mission("test", "0 * * * *"))
        run(self.store.upsert_mission("test", "0 * * * *"))
        rows = run(self.store.execute("SELECT COUNT(*) as n FROM missions WHERE id='test'"))
        self.assertEqual(rows[0]["n"], 1)

    def test_record_run_start(self) -> None:
        run(self.store.upsert_mission("m1", "0 * * * *"))
        run_id = run(self.store.record_run_start("m1"))
        self.assertIsInstance(run_id, int)
        rows = run(self.store.execute("SELECT * FROM runs WHERE id=?", (run_id,)))
        self.assertEqual(rows[0]["status"], "running")
        self.assertEqual(rows[0]["mission_id"], "m1")

    def test_record_run_finish_success(self) -> None:
        run(self.store.upsert_mission("m2", "0 * * * *"))
        run_id = run(self.store.record_run_start("m2"))
        run(self.store.record_run_finish(
            run_id, "m2", "completed",
            model_used="claude-sonnet-4.6",
            tokens_in=100,
            tokens_out=200,
            results={"alerts": 3},
        ))
        rows = run(self.store.execute("SELECT * FROM runs WHERE id=?", (run_id,)))
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["model_used"], "claude-sonnet-4.6")
        self.assertEqual(rows[0]["tokens_in"], 100)
        parsed = json.loads(rows[0]["results"])
        self.assertEqual(parsed["alerts"], 3)

    def test_record_run_finish_increments_run_count(self) -> None:
        run(self.store.upsert_mission("m3", "0 * * * *"))
        for _ in range(3):
            rid = run(self.store.record_run_start("m3"))
            run(self.store.record_run_finish(rid, "m3", "completed"))
        rows = run(self.store.execute("SELECT run_count, fail_count FROM missions WHERE id='m3'"))
        self.assertEqual(rows[0]["run_count"], 3)
        self.assertEqual(rows[0]["fail_count"], 0)

    def test_record_run_finish_increments_fail_count(self) -> None:
        run(self.store.upsert_mission("m4", "0 * * * *"))
        rid = run(self.store.record_run_start("m4"))
        run(self.store.record_run_finish(rid, "m4", "failed", error="timeout"))
        rows = run(self.store.execute("SELECT run_count, fail_count FROM missions WHERE id='m4'"))
        self.assertEqual(rows[0]["fail_count"], 1)
        self.assertEqual(rows[0]["run_count"], 0)


class TestAlerts(unittest.TestCase):
    """CRUD for alerts table."""

    def setUp(self) -> None:
        self.store = StateStore(":memory:")
        self.store.open()

    def tearDown(self) -> None:
        self.store.close()

    def test_write_alert_returns_id(self) -> None:
        alert_id = run(self.store.write_alert(
            severity="CRITICAL",
            source="portfolio_watchdog",
            title="Too many alerts",
            detail="10 open Dependabot alerts",
            repo="owner/repo",
        ))
        self.assertIsInstance(alert_id, int)
        self.assertGreater(alert_id, 0)

    def test_alert_stored_correctly(self) -> None:
        run(self.store.write_alert("WARN", "test", "A warning", detail="details", repo="r/r"))
        rows = run(self.store.execute("SELECT * FROM alerts WHERE source='test'"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "WARN")
        self.assertEqual(rows[0]["title"], "A warning")
        self.assertFalse(rows[0]["acknowledged"])

    def test_multiple_alerts(self) -> None:
        for i in range(5):
            run(self.store.write_alert("INFO", "src", f"Alert {i}"))
        rows = run(self.store.execute("SELECT COUNT(*) as n FROM alerts"))
        self.assertEqual(rows[0]["n"], 5)


class TestWatchedRepos(unittest.TestCase):
    """CRUD for watched_repos table."""

    def setUp(self) -> None:
        self.store = StateStore(":memory:")
        self.store.open()

    def tearDown(self) -> None:
        self.store.close()

    def test_upsert_and_get_repos(self) -> None:
        run(self.store.upsert_watched_repo("owner/repo-a", notes="test repo"))
        run(self.store.upsert_watched_repo("owner/repo-b"))
        repos = run(self.store.get_watched_repos())
        repo_names = {r["repo"] for r in repos}
        self.assertIn("owner/repo-a", repo_names)
        self.assertIn("owner/repo-b", repo_names)

    def test_upsert_updates_existing(self) -> None:
        run(self.store.upsert_watched_repo("owner/repo", security_score=5.0))
        run(self.store.upsert_watched_repo("owner/repo", security_score=8.5))
        rows = run(self.store.execute("SELECT security_score FROM watched_repos WHERE repo='owner/repo'"))
        self.assertAlmostEqual(rows[0]["security_score"], 8.5)

    def test_empty_repos(self) -> None:
        repos = run(self.store.get_watched_repos())
        self.assertEqual(repos, [])


class TestMutations(unittest.TestCase):
    """CRUD for mutations table."""

    def setUp(self) -> None:
        self.store = StateStore(":memory:")
        self.store.open()

    def tearDown(self) -> None:
        self.store.close()

    def test_record_mutation(self) -> None:
        mid = run(self.store.record_mutation(
            agent_id="my-agent",
            original_hash="abc123",
            mutated_hash="def456",
            mutation_type="improvement",
            validators=["approved", "approved", "rejected"],
            consensus="approved",
            deployed=True,
        ))
        self.assertIsInstance(mid, int)
        rows = run(self.store.execute("SELECT * FROM mutations WHERE id=?", (mid,)))
        self.assertEqual(rows[0]["agent_id"], "my-agent")
        self.assertEqual(rows[0]["consensus"], "approved")
        self.assertTrue(rows[0]["deployed"])
        self.assertEqual(rows[0]["validator_1"], "approved")
        self.assertEqual(rows[0]["validator_2"], "approved")
        self.assertEqual(rows[0]["validator_3"], "rejected")


class TestELOCache(unittest.TestCase):
    """CRUD for elo_cache table."""

    def setUp(self) -> None:
        self.store = StateStore(":memory:")
        self.store.open()

    def tearDown(self) -> None:
        self.store.close()

    def test_upsert_and_read(self) -> None:
        run(self.store.upsert_elo_cache("claude-sonnet-4.6", 1450.0, 20, 5, "code,analysis"))
        rows = run(self.store.execute("SELECT * FROM elo_cache WHERE model='claude-sonnet-4.6'"))
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["elo"], 1450.0)
        self.assertEqual(rows[0]["wins"], 20)
        self.assertEqual(rows[0]["best_task_types"], "code,analysis")

    def test_upsert_overwrites(self) -> None:
        run(self.store.upsert_elo_cache("model-x", 1000.0, 1, 1))
        run(self.store.upsert_elo_cache("model-x", 1200.0, 5, 2))
        rows = run(self.store.execute("SELECT elo, wins FROM elo_cache WHERE model='model-x'"))
        self.assertAlmostEqual(rows[0]["elo"], 1200.0)
        self.assertEqual(rows[0]["wins"], 5)


class TestTransactionContextManager(unittest.TestCase):
    """Synchronous transaction context manager."""

    def setUp(self) -> None:
        self.store = StateStore(":memory:")
        self.store.open()

    def tearDown(self) -> None:
        self.store.close()

    def test_transaction_commits_on_success(self) -> None:
        with self.store.transaction() as conn:
            conn.execute("INSERT INTO alerts (severity, source, title) VALUES (?,?,?)",
                         ("INFO", "test", "tx test"))
        rows = run(self.store.execute("SELECT * FROM alerts WHERE source='test'"))
        self.assertEqual(len(rows), 1)

    def test_transaction_rolls_back_on_error(self) -> None:
        try:
            with self.store.transaction() as conn:
                conn.execute("INSERT INTO alerts (severity, source, title) VALUES (?,?,?)",
                             ("INFO", "rollback-test", "should not persist"))
                raise ValueError("simulated error")
        except ValueError:
            pass
        rows = run(self.store.execute("SELECT * FROM alerts WHERE source='rollback-test'"))
        self.assertEqual(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
