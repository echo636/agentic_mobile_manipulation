import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('remote_readiness', Path(__file__).resolve().parents[1] / 'scripts/behavior100_remote.py')
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


class ImportReadinessTests(unittest.TestCase):
    def test_wrong_host_editable_and_missing_import_are_rejected_without_loading_simulator(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = {name: root / 'src/BEHAVIOR-1K' / path for name, path in {
                'bddl': 'bddl3/bddl/__init__.py',
                'omnigibson': 'OmniGibson/omnigibson/__init__.py',
                'gello': 'joylo/gello/__init__.py'}.items()}
            for path in paths.values():
                path.parent.mkdir(parents=True); path.write_text('raise AssertionError("must not import")')
            specs = {name: SimpleNamespace(origin=str(path)) for name, path in paths.items()}
            with patch.object(remote.importlib.util, 'find_spec', side_effect=specs.get):
                self.assertEqual(remote.source_import_readiness(root)['status'], 'passed')
                specs['bddl'] = None
                self.assertEqual(remote.source_import_readiness(root)['packages']['bddl']['status'], 'failed')
                other = root / 'other-source.py'; other.write_text('')
                specs['bddl'] = SimpleNamespace(origin=str(other))
                self.assertEqual(remote.source_import_readiness(root)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
