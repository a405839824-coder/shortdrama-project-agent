"""Reusing pure compile work never reuses candidate approval or source authority."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/shortdrama-storyboard/scripts'))
import production_derivation as pd
import production_decisions as decisions
from test_v0535_production_contract import complete_episode, generate


class ReplayCompilationCacheTests(unittest.TestCase):
    def setUp(self):
        pd._replay_compilation.cache_clear()
        d,g=complete_episode()
        with tempfile.TemporaryDirectory() as folder:self.candidate=generate(folder,d,g)
        pd._replay_compilation.cache_clear()

    def tearDown(self):pd._replay_compilation.cache_clear()

    def test_identical_input_reuses_only_compilation_and_caller_mutation_is_isolated(self):
        with patch.object(decisions,'lower_production_decisions',wraps=decisions.lower_production_decisions) as compile:
            first=pd.inspect_derivation(self.candidate)
            self.assertTrue(first['valid'],first)
            first['source_obligations'].clear()
            second=pd.inspect_derivation(copy.deepcopy(self.candidate))
            self.assertTrue(second['valid'],second)
            self.assertTrue(second['source_obligations'])
            self.assertEqual(compile.call_count,1)
            self.assertEqual(pd._replay_compilation.cache_info().maxsize,2)

    def test_warm_cache_rejects_changed_content_input_source_and_approval(self):
        self.assertTrue(pd.inspect_derivation(self.candidate)['valid'])
        for kind in ('content','input','source','approval','trace'):
            c=copy.deepcopy(self.candidate);p=c['prevention_evidence']['production_derivation']
            if kind=='content':c['shots'][0]['画面内容']+='改变原意。'
            elif kind=='input':p['input']['shots'][0]['ticks'][1]+=1
            elif kind=='source':p['source_graph_json']+=' '
            elif kind=='approval':p['independent_audit_status']='pass'
            else:p['action_trace']['shots'][0]['shot_id']='wrong-shot'
            # Even re-signing a self-authored outer digest cannot authorize it.
            p['binding_sha256']=pd.digest({k:v for k,v in p.items() if k!='binding_sha256'})
            with self.subTest(kind=kind):self.assertFalse(pd.inspect_derivation(c)['valid'])

    def test_transitive_runtime_hash_change_invalidates_warm_cache(self):
        self.assertIn('production_actions.py',pd.compiler_fingerprints())
        self.assertIn('production_bridge.py',pd.compiler_fingerprints())
        self.assertTrue(pd.inspect_derivation(self.candidate)['valid'])
        hashes=pd.compiler_fingerprints();hashes['production_actions.py']='changed'
        with patch.object(pd,'compiler_fingerprints',return_value=hashes):
            result=pd.inspect_derivation(self.candidate)
        self.assertFalse(result['valid'])
        self.assertTrue(any('compiler_fingerprints' in f['path'] for f in result['faults']))


if __name__=='__main__':unittest.main()
