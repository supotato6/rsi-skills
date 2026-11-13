"""Framework configs select an explicit whole library, including outside CWD."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shared_api_test', ROOT / '__init__.py',
                                             submodule_search_locations=[str(ROOT)])
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


class ConfigTest(unittest.TestCase):
    def test_reproduction_atom_installs_with_its_evidence_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot=api.install(Path(folder)/'skills',source=ROOT)
            snapshot.resolve(['rsi-kaggle-reproduce'])
            self.assertEqual(snapshot.read('rsi-kaggle-reproduce'),
                (ROOT/'skills/atomic/rsi-kaggle-reproduce/SKILL.md').read_text())
            contract=snapshot.root/'rsi-kaggle-reproduce/references/evidence-contract.md'
            self.assertTrue(contract.is_file())
            self.assertIn('rsi-kaggle-reproduce',snapshot.read('rsi-kaggle-optimize'))
    def test_config_relative_source_is_the_library_actually_frozen(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'pinned library'
            shutil.copytree(ROOT / 'skills', source / 'skills')
            shutil.copytree(ROOT / 'scripts', source / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            body = source / 'skills/atomic/rsi-artifact-check/SKILL.md'
            body.write_text(body.read_text() + '\nUnique source fixture.\n')
            config_path = root / 'skills.json'
            config_path.write_text(json.dumps({'library_path': './pinned library', 'selection': 'auto'}))
            config = api.load_config(config_path)
            self.assertEqual(config['library_path'], str(source))
            snapshot = api.install(root / 'run', source=config['library_path'])
            self.assertEqual(snapshot.identity, api.fingerprint(api.catalog(source)))
            self.assertIn('Unique source fixture.', snapshot.read('rsi-artifact-check'))
            self.assertEqual(len(snapshot.entries()), len(api.catalog()['skills']))
            shutil.rmtree(source)
            self.assertEqual(api.load_config(config_path), config)
            self.assertIn('Unique source fixture.', api.open_snapshot(root / 'run').read('rsi-artifact-check'))

    def test_rejects_partial_selection_and_duplicate_version(self):
        for value in ({}, {'library_path': '.', 'selection': 'fixed'},
                      {'library_path': '.', 'selection': 'auto', 'version': 'main'},
                      {'library_path': '', 'selection': 'auto'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                api.load_config(value)
