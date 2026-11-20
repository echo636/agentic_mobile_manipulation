"""CPU checks of opt-in launcher configuration; not real startup validation."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.omnigibson_backend import configure_native_fault_reporting


class SimulatorFaultReportingTests(unittest.TestCase):
    def test_normal_launch_does_not_import_or_configure_isaacsim(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            with patch.dict(sys.modules, {"isaacsim": None}):
                configure_native_fault_reporting(Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_external_capture_disables_only_builtin_reporter_and_records_scope(self):
        defaults = {"enable_crashreporter": True, "headless": True, "physics_gpu": 3}
        module = SimpleNamespace(SimulationApp=SimpleNamespace(DEFAULT_LAUNCHER_CONFIG=defaults))
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"MAS_NATIVE_FAULT_DIR": folder}, clear=True):
                with patch.dict(sys.modules, {"isaacsim": module}):
                    configure_native_fault_reporting(Path(folder))
            self.assertEqual(defaults, {"enable_crashreporter": False, "headless": True, "physics_gpu": 3})
            record = json.loads((Path(folder) / "native_fault_reporting.json").read_text())
            self.assertEqual(record["capture"], "external_gdb_first_native_signal")
            self.assertFalse(record["simulation_startup_validated"])

    def test_incompatible_launcher_fails_explicitly(self):
        module = SimpleNamespace(SimulationApp=SimpleNamespace(DEFAULT_LAUNCHER_CONFIG={}))
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"MAS_NATIVE_FAULT_DIR": folder}, clear=True):
                with patch.dict(sys.modules, {"isaacsim": module}):
                    with self.assertRaisesRegex(RuntimeError, "enable_crashreporter"):
                        configure_native_fault_reporting(Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])
