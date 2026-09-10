"""Synthetic voice ingredients and the recorded v2 final-field boundary."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'skills/shortdrama-storyboard/scripts')]
from authoring_contract_blocks import ContractBlockError, voice_block
from author_storyboard import CONTRACT, record_attempt
from shortdrama_mcp.materializer import compile_formal_merged_field, compile_gpt_keyframe_instruction
from test_v0527_authoring_contract_blocks import shot
from test_v0527_staging_text_bindings import bound_decisions
import validate_storyboard as validator


def voice_arguments():
    return dict(mode='spoken', dialogue_rows=[('甲', '还能来吗？')],
                baseline='年龄感为成年，中音区，厚薄适中，明暗中性，常态清楚。',
                speech_act='询问来访安排。', current_expression='温和，意在确认安排。',
                volume='正常交谈。', rhythm='短句自然说出。', breath='开口前自然吸气。',
                pauses='句末短停。', emphasis='“还能”。', tail_direction='上抬',
                tail_execution='末字温和而克制。', prohibited_tones=['喊叫', '嘲讽'])


def voice_decisions(silent=False):
    data = bound_decisions(); row = data['shots'][0]
    values = shot()
    for field in row['fields']:
        if field not in ['机位', '起始状态']:
            row['fields'][field] = values[field]
    row['fields']['起始状态'] = values['起始状态'] + '[[staging:side]]'
    row['fields']['音色'] = (voice_block(mode='silent', dialogue_rows=[]) if silent
                             else voice_block(**voice_arguments()))
    if not silent:
        row['phases'][1]['text'] = '甲保持双手放在桌边。甲说道：“还能来吗？”甲口型与对白一致，末字温和上扬。'
        data['project']['prevention_evidence']['audio_workflow']['timing']['utterances'] = [{
            'id': 'say-1', 'kind': 'dialogue', 'speaker': '甲',
            'source': {'shot_id': row['shot_id'], 'dialogue_index': 0},
            'segments': [{'shot_id': row['shot_id'], 'start': 0.7, 'end': 3.3,
                          'char_range': [0, len('还能来吗？')]}]}]
    return data


def control_cases():
    positive = voice_decisions()
    result = [('VC01', positive, None)]
    bad = copy.deepcopy(positive)
    bad['shots'][0]['fields']['音色'] = bad['shots'][0]['fields']['音色'].replace('尾音：上抬', '尾音：上扬')
    result.append(('VC02_bypass', bad, '尾音必须明确选择'))
    bad = copy.deepcopy(positive)
    bad['shots'][0]['fields']['音色'] = bad['shots'][0]['fields']['音色'].replace('发声方式：\n', '发声方式：轻声\n')
    result.append(('VC03_heading', bad, '空值分组标题'))
    bad = copy.deepcopy(positive); bad['shots'][0]['fields']['音色'] += '\n尾音：上抬。'
    result.append(('VC03_duplicate', bad, '尾音重复'))
    bad = copy.deepcopy(positive)
    bad['shots'][0]['phases'][1]['text'] = bad['shots'][0]['phases'][1]['text'].replace('上扬', '上抬')
    result.append(('VC04_body', bad, 'PUNCT: 问号尾音/意图'))
    silent = voice_decisions(silent=True); result.append(('VC05_silent', silent, None))
    bad = copy.deepcopy(silent); bad['shots'][0]['fields']['音色'] = positive['shots'][0]['fields']['音色']
    result.append(('VC05_spoken_on_silent', bad, '无对白镜必须'))
    return result


def actual_control(root, name, data, expected_fault):
    """Keep all raw bytes and return exact public entry observations."""
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    path = root / (name + '.json')
    with path.open('xb') as stream: stream.write(raw)
    report = record_attempt(path, root / 'attempts', attempt_id=name)
    attempt = Path(report['report_path']).parent
    result = dict(id=name, expected_fault=expected_fault, report=report,
                  raw_preserved=(attempt / 'input.original.json').read_bytes() == raw,
                  raw_sha256=hashlib.sha256(raw).hexdigest(),
                  canonical_exists=(attempt / 'canonical.json').exists())
    if report['canonical_path']:
        canonical = json.loads(Path(report['canonical_path']).read_text(encoding='utf-8'))
        value = canonical['shots'][0]
        validation = validator.validate_project(canonical)
        result['voice_punct_issues'] = [issue for issue in validation['issues']
                                        if 'VOICE' in issue['error_code'] or 'PUNCT' in issue['error_code']]
        result['other_machine_issue_count'] = len(validation['issues']) - len(result['voice_punct_issues'])
        formal = validator.project_formal_master(value)
        static = validator.project_gpt_keyframe(value)
        result['projections'] = dict(
            formal=formal, gpt=static,
            formal_equal=formal == compile_formal_merged_field(value, assume_compact=True),
            gpt_equal=static == compile_gpt_keyframe_instruction(value, assume_compact=True),
            voice_preserved=value['音色'] in formal,
            gpt_has_voice_label='基础声线：' in static)
    message = '; '.join(item['message'] for item in report.get('findings', []))
    result['matched'] = (result['raw_preserved'] and report['semantic_accuracy_percent'] is None
                         and not report['commit_eligible'] and report['include_in_attempt_accounting'])
    if expected_fault:
        result['matched'] &= (report['status'] == 'rejected' and expected_fault in message
                              and not result['canonical_exists'])
    else:
        result['matched'] &= (report['status'] == 'rendered_pending_preflight'
                              and not result['voice_punct_issues']
                              and result['projections']['formal_equal'] and result['projections']['gpt_equal']
                              and result['projections']['voice_preserved']
                              and not result['projections']['gpt_has_voice_label'])
    return result


class VoiceAuthorContractTests(unittest.TestCase):
    def run_group(self, prefix):
        with tempfile.TemporaryDirectory() as folder:
            for name, data, fault in control_cases():
                if name.startswith(prefix):
                    with self.subTest(control=name):
                        result = actual_control(folder, name, data, fault)
                        self.assertTrue(result['matched'], result)

    def test_vc01_registered_pair_all_public_projections(self):
        value = voice_block(**voice_arguments())
        parsed, order, errors = validator._parse_voice_template(value)
        self.assertEqual(order, list(validator.VOICE_TEMPLATE_LABELS))
        self.assertEqual(parsed['发声方式'], '')
        self.assertEqual(errors, [])
        self.run_group('VC01')

    def test_vc02_exact_enum_and_post_build_bypass(self):
        args = voice_arguments(); args['tail_direction'] = '上扬'
        with self.assertRaisesRegex(ContractBlockError, 'voice.tail_direction'):
            voice_block(**args)
        self.run_group('VC02')

    def test_vc03_group_and_label_injection(self):
        for field, value in [('tail_execution', '自然。\n尾音：落稳'), ('volume', '音量：正常')]:
            with self.subTest(field=field):
                args = voice_arguments(); args[field] = value
                with self.assertRaisesRegex(ContractBlockError, 'fixed-label injection'):
                    voice_block(**args)
        self.run_group('VC03')

    def test_vc04_body_contract_does_not_read_voice_and_v1_is_unchanged(self):
        self.run_group('VC04')
        _, bad, _ = next(item for item in control_cases() if item[0] == 'VC04_body')
        bad['contract'] = CONTRACT; del bad['shots'][0]['staging']
        bad['shots'][0]['fields']['机位'] = '固定机位，中景。'
        bad['shots'][0]['fields']['起始状态'] = '甲双手放在桌边。'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'v1.json'; path.write_text(json.dumps(bad, ensure_ascii=False), encoding='utf-8')
            report = record_attempt(path, Path(folder) / 'attempts', attempt_id='legacy')
            self.assertEqual(report['status'], 'rendered_pending_preflight')
            self.assertNotIn('VOICE/PUNCT', report['checked_scope'])
            canonical = json.loads(Path(report['canonical_path']).read_text(encoding='utf-8'))
            self.assertTrue(any('PUNCT' in issue['error_code'] for issue in validator.validate_project(canonical)['issues']))

    def test_vc05_explicit_silent_and_no_invented_content(self):
        self.assertEqual(voice_block(mode='silent', dialogue_rows=[]), '无对白，不适用。')
        with self.assertRaisesRegex(ContractBlockError, 'no dialogue rows or spoken specification'):
            voice_block(mode='silent', dialogue_rows=[], baseline='成年')
        args = voice_arguments(); args['baseline'] = None
        with self.assertRaisesRegex(ContractBlockError, 'voice.基础声线'):
            voice_block(**args)
        self.run_group('VC05')


if __name__ == '__main__':
    unittest.main()
