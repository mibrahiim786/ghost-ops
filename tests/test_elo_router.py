"""Unit tests for ELORouter."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Allow imports from the parent package
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.elo_router import ELORouter, _DEFAULT_MODELS


class TestELORouterDefaults(unittest.TestCase):
    """ELORouter falls back gracefully when the ELO file is unavailable."""

    def test_missing_file_returns_defaults(self) -> None:
        router = ELORouter(elo_path="/nonexistent/path/hackathon-elo.json")
        ranked = router.ranked_models()
        # Defaults must be present
        for m in _DEFAULT_MODELS:
            self.assertIn(m, ranked)

    def test_empty_file_returns_defaults(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("")
            tmp = f.name
        try:
            router = ELORouter(elo_path=tmp)
            ranked = router.ranked_models()
            for m in _DEFAULT_MODELS:
                self.assertIn(m, ranked)
        finally:
            os.unlink(tmp)

    def test_malformed_json_returns_defaults(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            tmp = f.name
        try:
            router = ELORouter(elo_path=tmp)
            ranked = router.ranked_models()
            for m in _DEFAULT_MODELS:
                self.assertIn(m, ranked)
        finally:
            os.unlink(tmp)

    def test_missing_models_key_returns_defaults(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"total_hackathons": 5}, f)
            tmp = f.name
        try:
            router = ELORouter(elo_path=tmp)
            ranked = router.ranked_models()
            for m in _DEFAULT_MODELS:
                self.assertIn(m, ranked)
        finally:
            os.unlink(tmp)


class TestELORouterRanking(unittest.TestCase):
    """ELORouter correctly ranks models by ELO score."""

    def _make_elo_file(self, models: dict) -> str:
        data = {"models": models, "total_hackathons": 10}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            return f.name

    def test_ranked_by_elo_descending(self) -> None:
        tmp = self._make_elo_file({
            "model-a": {"elo": 1200.0, "wins": 5, "losses": 1, "total": 6},
            "model-b": {"elo": 1500.0, "wins": 8, "losses": 1, "total": 9},
            "model-c": {"elo": 1000.0, "wins": 2, "losses": 5, "total": 7},
        })
        try:
            router = ELORouter(elo_path=tmp)
            ranked = router.ranked_models()
            self.assertEqual(ranked[0], "model-b")
            self.assertEqual(ranked[1], "model-a")
            self.assertEqual(ranked[2], "model-c")
        finally:
            os.unlink(tmp)

    def test_top_model_is_highest_elo(self) -> None:
        tmp = self._make_elo_file({
            "low-model": {"elo": 900.0, "wins": 1, "losses": 9, "total": 10},
            "top-model": {"elo": 2000.0, "wins": 19, "losses": 1, "total": 20},
        })
        try:
            router = ELORouter(elo_path=tmp)
            self.assertEqual(router.top_model(), "top-model")
        finally:
            os.unlink(tmp)

    def test_defaults_appended_when_not_in_file(self) -> None:
        tmp = self._make_elo_file({
            "custom-model": {"elo": 1100.0, "wins": 3, "losses": 2, "total": 5},
        })
        try:
            router = ELORouter(elo_path=tmp)
            ranked = router.ranked_models()
            self.assertEqual(ranked[0], "custom-model")
            for m in _DEFAULT_MODELS:
                self.assertIn(m, ranked)
        finally:
            os.unlink(tmp)

    def test_fallback_chain_excludes_specified(self) -> None:
        tmp = self._make_elo_file({
            "model-x": {"elo": 1500.0, "wins": 5, "losses": 0, "total": 5},
        })
        try:
            router = ELORouter(elo_path=tmp)
            chain = router.fallback_chain(exclude=["model-x"])
            self.assertNotIn("model-x", chain)
        finally:
            os.unlink(tmp)


class TestELORouterCache(unittest.TestCase):
    """Cache TTL and invalidation."""

    def test_cache_returns_same_result(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"models": {"m": {"elo": 1000.0, "wins": 1, "losses": 0, "total": 1}}}, f)
            tmp = f.name
        try:
            router = ELORouter(elo_path=tmp, cache_ttl=60)
            first = router.ranked_models()
            second = router.ranked_models()
            self.assertEqual(first, second)
        finally:
            os.unlink(tmp)

    def test_invalidate_cache_forces_reload(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"models": {"m": {"elo": 1000.0, "wins": 1, "losses": 0, "total": 1}}}, f)
            tmp = f.name
        try:
            router = ELORouter(elo_path=tmp, cache_ttl=9999)
            router.ranked_models()  # populate cache
            # Overwrite the file with new data
            with open(tmp, "w") as fh:
                json.dump({"models": {"new-model": {"elo": 9999.0, "wins": 10, "losses": 0, "total": 10}}}, fh)
            # Without invalidate, stale cache should be used
            stale = router.ranked_models()
            self.assertNotEqual(stale[0], "new-model")
            # After invalidate, new data should be loaded
            router.invalidate_cache()
            fresh = router.ranked_models()
            self.assertEqual(fresh[0], "new-model")
        finally:
            os.unlink(tmp)

    def test_expired_cache_reloads(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"models": {"m": {"elo": 1000.0, "wins": 1, "losses": 0, "total": 1}}}, f)
            tmp = f.name
        try:
            router = ELORouter(elo_path=tmp, cache_ttl=0)
            first = router.ranked_models()
            # Even with ttl=0, second call should still return valid data
            second = router.ranked_models()
            for m in _DEFAULT_MODELS:
                self.assertIn(m, second)
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
