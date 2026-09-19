"""No server-recorded errors must not be presented as completed content audit."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from shortdrama_mcp.diagnostic_view import diagnostic_page
from shortdrama_mcp.models import ToolResponse
from shortdrama_mcp.workflow_state import observed_workflow


class WorkflowStateTests(unittest.TestCase):
    def test_zero_issues_and_authorized_order_are_not_completed_audit(self):
        state=observed_workflow({'runtime_status':'pass','state_valid':True,
            'latest_storyboard':None,'independent_audit_pending':False,
            'open_issue_pattern_count':0,'project':{'latest_work_order':{'status':'approved'}}})
        self.assertEqual(state['stage'],'not_submitted')
        self.assertEqual(state['independent_audit_state'],'not_started')
        self.assertFalse(state['counts_as_complete'])
        self.assertFalse(state['allows_next_batch'])

    def test_claimed_pass_needs_actual_evidence_and_delivery_receipt(self):
        row={'artifact_id':'ART-one','effective_audit_status':'PASS','fingerprint':'a'*64}
        state=observed_workflow({'latest_storyboard':row})
        self.assertEqual(state['stage'],'audit_unverified')
        row['audit_evidence_state']='verified_independent_certificate'
        state=observed_workflow({'latest_storyboard':row})
        self.assertEqual(state['stage'],'audit_recorded')
        self.assertEqual(state['formal_delivery_state'],'not_verified')
        self.assertFalse(state['counts_as_complete'])
        self.assertEqual(state['delivery_verification']['artifact_id'],'ART-one')

    def test_open_dependency_or_current_preflight_failure_overrides_summary(self):
        row={'artifact_id':'ART-one','effective_audit_status':'PASS',
             'audit_evidence_state':'verified_independent_certificate'}
        self.assertEqual(observed_workflow({'latest_storyboard':row,
            'independent_audit_pending':True})['stage'],'awaiting_audit')
        self.assertEqual(observed_workflow({'latest_storyboard':row,
            'audit_coverage_pending':True})['stage'],'awaiting_audit')
        for reason in ({'draft_preflight_repair_pending':True},
                       {'production_quality_block':{'active':True}}, {'state_valid':False}):
            self.assertEqual(observed_workflow({'latest_storyboard':row,**reason})['stage'],'blocked')


class DiagnosticViewTests(unittest.TestCase):
    def report(self):
        return ToolResponse(status='diagnostic_completed', summary='synthetic machine findings',
            project_id='SBP-test',artifact_id='ART-test',data={
            'artifact_fingerprint':'a'*64,'ruleset_version':'rule-1','validator_version':'validator-1',
            'requested_episode_ids':['EP01'],'checked_shot_count':50,
            'issues':[{'error_code':'E-VIS-001','classification':'input_mapping',
                'location':'shots[1].可见主体','evidence':'主体类型待核验，不能当作缺眨眼'},
                {'error_code':'E-FIELD-CONTENT-001','location':'shots[2].画面内容','evidence':'长内容'*3000}]+
                [{'error_code':'E-CONTENT-001','location':f'shots[{i}].画面内容','evidence':'候选问题'} for i in range(3,55)],
            'unscoped_or_dependency_issues':[{'error_code':'E-GATE-001',
                'location':'prevention_evidence','evidence':'缺证'}],
            'outside_scope_issues':[{'error_code':'E-CONTENT-001','location':'shots[90]','evidence':'范围外'}],
            'formal_audit_status':'not_performed','source_coverage_status':'not_independently_verified',
            'certificate_created':False,'historical_evidence_repaired':False,'raw_generation_quality':None})

    def test_all_pages_cover_exact_findings_without_mutating_or_certifying(self):
        report=self.report(); before=copy.deepcopy(report.model_dump())
        seen=[];args={'offset':0,'limit':7,'max_chars':1200}
        pages=0
        while True:
            result=diagnostic_page(report,**args).data;pages+=1
            rows=sum((result[k] for k in ('issues','unscoped_or_dependency_issues','outside_scope_issues')),[])
            self.assertLessEqual(len(json.dumps(rows,ensure_ascii=False,separators=(',',':'))),1200)
            seen.extend(row['issue_index'] for row in rows)
            self.assertEqual(result['finding_counts']['unscoped_or_dependency_issues'],1)
            self.assertEqual(result['classification_counts']['input_mapping'],1)
            self.assertFalse(result['certificate_created'])
            if not result['page']['has_more']:break
            next_call=result['page']['next_call']
            args={key:next_call[key] for key in ('section','offset','limit','max_chars')}
            self.assertLess(pages,100)
        self.assertEqual(seen,list(range(56)))
        self.assertEqual(before,report.model_dump())

    def test_oversize_issue_can_be_recovered_exactly(self):
        report=self.report()
        result=diagnostic_page(report,offset=1,limit=1,max_chars=512).data
        row=result['issues'][0]
        self.assertTrue(row['detail_required'])
        chunks=[];offset=0
        while True:
            result=diagnostic_page(report,section=row['detail_section'],offset=offset,max_chars=512).data
            chunks.append(result['detail']['text'])
            if not result['page']['has_more']:break
            offset=result['page']['next_call']['offset']
        self.assertEqual(json.loads(''.join(chunks)),report.data['issues'][1])

    def test_changed_rules_or_content_invalidates_pagination_snapshot(self):
        report=self.report();first=diagnostic_page(report,limit=1).data
        section=first['page']['next_call']['section']
        for key,value in [('ruleset_version','rule-2'),('artifact_fingerprint','b'*64)]:
            changed=report.model_copy(deep=True);changed.data[key]=value
            with self.assertRaisesRegex(ValueError,'diagnostic_snapshot_changed'):
                diagnostic_page(changed,section=section,offset=1)

    def test_invalid_pagination_is_explicit(self):
        for args in ({'offset':-1},{'offset':True},{'limit':0},{'max_chars':1},
                     {'offset':10000},{'section':'unrecognized'}):
            with self.subTest(args=args),self.assertRaises(ValueError):diagnostic_page(self.report(),**args)


if __name__=='__main__':unittest.main()
