import copy
import unittest
from unittest.mock import patch
from shortdrama_mcp import preflight_payload as payload
from shortdrama_mcp.canonical import canonical_sha256


class PreflightPayloadTests(unittest.TestCase):
    def setUp(self):
        import test_v056_source_dialogue_authority as fixture_module
        self.fixture=fixture_module.SourceDialoguePreflightTests(methodName='runTest')
        self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.service=self.fixture.service;self.ns=self.fixture.namespace
        self.pid=str(self.service.create_project(self.ns,'payload-reference').project_id)
        self.candidate={'shots':[{'镜头编号':'EP01-001','画面内容':'江氏说道：“好。”'}],'source_lock':{'keep':'原文'}}
        self.wo='WO-payload';self.dpf='DPF-payload'
        self.service.store.save_project_record(self.ns,self.pid,kind='draft_preflights',record_id=self.dpf,
            value={'draft_preflight_id':self.dpf,'work_order_id':self.wo,'candidate_sha256':canonical_sha256(self.candidate),'status':'pass'})

    def pack(self):
        with patch.object(payload,'INLINE_LIMIT',1):
            return payload.pack(self.service,self.ns,self.pid,self.wo,self.dpf,self.candidate)

    def unpack(self,ref,wo=None):return payload.unpack(self.service,self.ns,self.pid,wo or self.wo,ref)

    def test_small_candidates_remain_compatible_and_copied(self):
        packed=payload.pack(self.service,self.ns,self.pid,self.wo,self.dpf,self.candidate)
        self.assertEqual(packed,self.candidate);self.assertIsNot(packed,self.candidate)

    def test_large_candidate_reference_is_exact_idempotent_and_small(self):
        ref=self.pack();self.assertEqual(ref,self.pack())
        self.assertEqual(set(ref),{'contract','draft_preflight_id','canonical_sha256'})
        actual=self.unpack(ref);self.assertEqual(actual,self.candidate);self.assertIsNot(actual,self.candidate)

    def test_reference_cannot_cross_work_orders_or_change_hash(self):
        ref=self.pack()
        with self.assertRaisesRegex(ValueError,'reference_binding'):self.unpack(ref,'WO-other')
        ref['canonical_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'reference_binding'):self.unpack(ref)

    def test_reference_cannot_smuggle_modified_fields(self):
        ref=self.pack();ref['shots']=[]
        with self.assertRaisesRegex(ValueError,'reference_shape'):self.unpack(ref)

    def test_even_signed_changed_payload_fails_original_hash(self):
        ref=self.pack();record=self.service.store.get_project_record(self.ns,self.pid,kind='preflight_candidates',record_id=self.dpf)
        record['candidate']['shots'][0]['画面内容']='伪造对白'
        self.service.store.save_project_record(self.ns,self.pid,kind='preflight_candidates',record_id=self.dpf,value=record)
        with self.assertRaisesRegex(ValueError,'reference_binding'):self.unpack(ref)
        with self.assertRaisesRegex(ValueError,'reference_conflict'):self.pack()

    def test_reference_requires_original_signed_preflight_hash(self):
        ref=self.pack()
        self.service.store.save_project_record(self.ns,self.pid,kind='draft_preflights',record_id=self.dpf,
            value={'draft_preflight_id':self.dpf,'work_order_id':self.wo,'candidate_sha256':'0'*64})
        with self.assertRaisesRegex(ValueError,'reference_binding'):self.unpack(ref)

    def test_reference_requires_signed_payload(self):
        ref=self.pack()
        with patch.object(self.service.store,'project_record_is_signed',return_value=False):
            with self.assertRaisesRegex(ValueError,'signature_required'):self.unpack(ref)
