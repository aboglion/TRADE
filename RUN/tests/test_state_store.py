"""
Tests for state store — save/load roundtrip, atomic writes, corruption handling.
"""

import json
import os
import tempfile

import pytest
from src.core.models import BotState
from src.services.state_store import JsonStateStore


class TestSaveLoadRoundtrip:
    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "state.json")
        store = JsonStateStore(path)

        state = BotState()
        state.last_processed_candle_ts = {"BTC/USDT": 1700000000000}
        state.last_regime = "bull"
        state.pending_orders = [{"id": "test1", "status": "submitted"}]
        state.last_run_ts = 1700000000000

        store.save_state(state)
        loaded = store.load_state()

        assert loaded.last_processed_candle_ts == {"BTC/USDT": 1700000000000}
        assert loaded.last_regime == "bull"
        assert len(loaded.pending_orders) == 1
        assert loaded.last_run_ts == 1700000000000

    def test_fresh_state_on_no_file(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        store = JsonStateStore(path)
        state = store.load_state()
        assert isinstance(state, BotState)
        assert state.last_processed_candle_ts == {}


class TestAtomicWrite:
    def test_backup_created(self, tmp_path):
        path = str(tmp_path / "state.json")
        store = JsonStateStore(path)

        # First save
        state1 = BotState()
        state1.last_regime = "bull"
        store.save_state(state1)

        # Second save — should create backup
        state2 = BotState()
        state2.last_regime = "bear"
        store.save_state(state2)

        # Check backup exists
        backup_path = path + ".bak"
        assert os.path.exists(backup_path)

        # Backup has previous state
        with open(backup_path) as f:
            backup_data = json.load(f)
        assert backup_data["last_regime"] == "bull"

        # Main has new state
        loaded = store.load_state()
        assert loaded.last_regime == "bear"


class TestCorruptionHandling:
    def test_corrupt_file_uses_backup(self, tmp_path):
        path = str(tmp_path / "state.json")
        backup_path = path + ".bak"
        store = JsonStateStore(path)

        # Write valid backup
        valid_state = BotState()
        valid_state.last_regime = "recovered"
        with open(backup_path, "w") as f:
            json.dump(valid_state.to_dict(), f)

        # Write corrupt main
        with open(path, "w") as f:
            f.write("{invalid json!!!")

        # Should recover from backup
        loaded = store.load_state()
        assert loaded.last_regime == "recovered"


class TestCompletedOrdersTruncation:
    def test_keeps_last_100_orders(self, tmp_path):
        path = str(tmp_path / "state.json")
        store = JsonStateStore(path)

        state = BotState()
        state.completed_orders = [{"id": f"order_{i}"} for i in range(200)]

        store.save_state(state)
        loaded = store.load_state()

        assert len(loaded.completed_orders) <= 100
