"""Deterministic offline regressions; fixtures are not real Kaggle performance."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/composite/rsi-kaggle-optimize/scripts/regression.py'
gate = types.ModuleType('gate')
exec(compile(SCRIPT.read_bytes(), str(SCRIPT), 'exec'), gate.__dict__)


class OptimizeRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = dict(competition='test', metric='metric', direction='higher', protocol='split1', baseline_id='base', candidates=[], uploads=[])
        self.add('base', 0.5)
        self.add('trial', 0.6)

    def add(self, cid, score):
        (self.root / f'{cid}.py').write_text('candidate ' + cid)
        (self.root / f'{cid}.csv').write_text('id,value\n1,0\n')
        report = dict(candidate_id=cid, competition='test', metric='metric', direction=self.plan['direction'], protocol='split1', kind='validation', status='complete', artifact_valid=True, score=score,
                      code_sha256=gate.digest(self.root / f'{cid}.py'), artifact_sha256=gate.digest(self.root / f'{cid}.csv'))
        (self.root / f'{cid}.json').write_text(json.dumps(report))
        self.plan['candidates'].append(dict(id=cid, code=f'{cid}.py', artifact=f'{cid}.csv', evaluation=f'{cid}.json'))

    def change(self, cid, **updates):
        file = self.root / f'{cid}.json'
        report = json.loads(file.read_text()); report.update(updates); file.write_text(json.dumps(report))

    def result(self):
        return gate.check(self.plan, self.root)

    def test_higher_improvement_is_not_public_proof(self):
        r = self.result()
        self.assertEqual(r['selected_id'], 'trial')
        self.assertFalse(r['public_improvement_proven'])
        self.assertFalse(r['remote_privacy_verified'])

    def test_buildings_final_comparison_uses_v15_not_frozen_score(self):
        self.plan.update(direction='lower', frozen_reference={'public_score':.01224},
            comparison_started_at='2026-11-03T00:00:00Z',
            final_reference={'freshness':'live','fetched_at':'2026-11-03T00:01:00Z',
                'competition':'test','metric':'metric','direction':'lower','board':'public',
                'candidates':[{'public_score':.01126,'best_score_version_number':15}]},
            official_submission={'submission_id':123,'competition':'test','metric':'metric',
                'direction':'lower','board':'public','score':.01217})
        result=gate.compare_public(self.plan)
        self.assertEqual(result['numeric_relation'],'behind')
        self.assertEqual(result['reference_score'],.01126)
        self.assertFalse(result['current_open_sota_beaten'])
        self.plan['final_reference']['fetched_at']='2026-10-29T00:00:00Z'
        self.assertEqual(gate.compare_public(self.plan)['status'],'unverified')
        self.plan.pop('final_reference')
        self.assertEqual(gate.compare_public(self.plan)['status'],'unverified')

    def test_traffic_reuse_and_casmi_smoke_are_distinct_from_score_matched_execution(self):
        for mode,scope,expected in [('public_output_reuse','full','public_output_reuse'),
                                    ('source_execution','smoke','smoke_only'),
                                    ('source_execution','full','full_execution_claimed')]:
            report={'mode':mode,'execution_scope':scope,'public_score_reproduced':True}
            result=gate.reproduction_level(report)
            self.assertEqual(result['declared_level'],expected)
            self.assertFalse(result['official_score_match_verified'])

    def test_lower_and_zero(self):
        self.plan['direction'] = 'lower'
        self.change('base', direction='lower'); self.change('trial', direction='lower', score=0)
        self.assertEqual(self.result()['selected_id'], 'trial')

    def test_no_improvement_or_tie_falls_back(self):
        for score in (0.4, 0.5):
            self.change('trial', score=score)
            self.assertEqual(self.result()['decision'], 'baseline_fallback')

    def test_invalid_scores_do_not_win(self):
        for score in (None, True, float('nan'), float('inf'), '0.9'):
            self.change('trial', score=score)
            self.assertEqual(self.result()['selected_id'], 'base')

    def test_changed_code_or_artifact_invalidates_score(self):
        for file in ('trial.py', 'trial.csv'):
            original = (self.root / file).read_bytes()
            (self.root / file).write_text('changed')
            self.assertEqual(self.result()['selected_id'], 'base')
            (self.root / file).write_bytes(original)

    def test_different_protocol_training_failed_and_invalid_artifact(self):
        for fields in ({'protocol':'split2'}, {'kind':'training'}, {'status':'failed'}, {'artifact_valid':False}, {'competition':'other'}):
            original = (self.root / 'trial.json').read_bytes()
            self.change('trial', **fields)
            self.assertEqual(self.result()['selected_id'], 'base')
            (self.root / 'trial.json').write_bytes(original)

    def test_invalid_baseline_blocks(self):
        self.change('base', score=None)
        self.assertFalse(self.result()['passed'])
        self.assertIsNone(self.result()['selected_id'])

    def test_privacy_requires_boolean_true(self):
        self.plan['uploads'] = [dict(kind='kaggle-notebook', resource='owner/private', visibility='private', metadata='metadata.json')]
        for metadata, expected in [({},False), ({'is_private':False},False), ({'is_private':'true'},False), ({'is_private':1},False), ({'is_private':True,'is_public':True},False), ({'is_private':True},True)]:
            (self.root / 'metadata.json').write_text(json.dumps(metadata))
            self.assertEqual(self.result()['passed'], expected)
        self.plan['uploads'][0]['visibility'] = 'public'
        self.assertFalse(self.result()['passed'])

    def test_unknown_privacy_and_missing_config_block(self):
        for uploads in (None, [{}], [dict(kind='other-private-resource',resource='weights', visibility='private',config_evidence='missing.json')]):
            self.plan['uploads'] = uploads
            self.assertFalse(self.result()['passed'])

    def test_duplicate_ids_block(self):
        self.plan['candidates'].append(copy.deepcopy(self.plan['candidates'][0]))
        self.assertFalse(self.result()['passed'])

    def test_cli_exit_status_and_json(self):
        path = self.root / 'plan.json'; path.write_text(json.dumps(self.plan))
        run = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout)['selected_id'], 'trial')
        path.write_text('{}')
        run = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
