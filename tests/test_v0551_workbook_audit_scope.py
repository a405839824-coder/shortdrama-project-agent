"""Scope boundaries on real XLSX bytes; no semantic PASS is claimed here."""
import io
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

from shortdrama_mcp import file_io
from shortdrama_mcp.audit_coverage import build_obligations
from shortdrama_mcp.formal_delivery import delivery_contract
from shortdrama_mcp.r5_workbook import is_reading_sheet, workbook_audit_scope
from historical_workbook_fixtures import previous_workbook
from test_v050_compact_master_io import compact_storyboard


class WorkbookAuditScopeTests(unittest.TestCase):
    def setUp(self):
        self.storyboard = compact_storyboard()
        self.payload = file_io.storyboard_to_xlsx(self.storyboard, {'status': 'fail', 'issues': []})
        self.canonical = file_io.extract_xlsx(self.payload)['canonical_storyboard']

    def changed(self, edit, payload=None):
        book = load_workbook(io.BytesIO(payload or self.payload))
        edit(book)
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        return stream.getvalue()

    def test_reading_edits_and_removal_leave_all_canonical_obligations_intact(self):
        context = {key: 'a' * 64 for key in ('rules', 'validator', 'runtime', 'schema')}
        expected = build_obligations(self.canonical, context)
        for edit in (lambda book: setattr(book['6集_逐镜阅读']['C9'], 'value', '阅读页的错误机位'),
                     lambda book: book.remove(book['6集_逐镜阅读'])):
            with self.subTest(edit=edit):
                canonical = file_io.extract_xlsx(self.changed(edit))['canonical_storyboard']
                self.assertEqual(canonical, self.canonical)
                self.assertEqual(build_obligations(canonical, context), expected)
                recovered = file_io.extract_xlsx_for_controlled_handoff_revalidation(self.changed(edit))
                self.assertEqual(recovered['canonical_storyboard'], self.canonical)

    def test_reading_headers_cannot_supply_a_second_or_replacement_master(self):
        def copy_master(book):
            book.remove(book['6集_逐镜阅读'])
            book.copy_worksheet(book['正式分镜母版']).title = '6集_逐镜阅读'
            book['6集_逐镜阅读']['E2'] = '阅读页错误正文'
        changed = self.changed(copy_master)
        self.assertEqual(file_io.extract_xlsx(changed)['canonical_storyboard'], self.canonical)
        with self.assertRaisesRegex(ValueError, 'exactly one versioned master'):
            file_io.extract_xlsx(self.changed(lambda book: book.remove(book['正式分镜母版']), changed))

    def test_formal_gpt_lighting_guide_and_audit_page_mismatches_still_block(self):
        for sheet, cell in [('正式分镜母版', 'E2'), ('正式分镜母版', 'F2'),
                            ('正式分镜母版', 'K2'), ('使用与来源', 'C2'), ('00_范围与审计', 'A1')]:
            with self.subTest(sheet=sheet, cell=cell):
                raw = self.changed(lambda book: setattr(book[sheet][cell], 'value', '错误内容'))
                with self.assertRaisesRegex(ValueError, 'canonical sidecar invalid'):
                    file_io.extract_xlsx(raw)

    def test_default_reads_select_master_explicit_reading_is_marked_excluded(self):
        value = file_io.extract_xlsx(self.payload)
        for section in (None, '6集_逐镜阅读'):
            single = file_io.preview_extracted(value, section, 0, 1)
            bulk = file_io.preview_extracted_bulk(value, [{'section': section, 'unit': 'characters', 'limit': 100}])
            self.assertEqual(single['section'], section or '正式分镜母版')
            self.assertEqual(bulk['ranges'][0]['section'], section or '正式分镜母版')
            self.assertEqual(single['audit_included'], section is None)
            self.assertEqual(bulk['ranges'][0]['audit_included'], section is None)
            self.assertEqual(single['workbook_audit_scope'], workbook_audit_scope())
        self.assertEqual(delivery_contract()['workbook_audit_scope'], workbook_audit_scope())

    def test_exact_suffix_only_and_historical_projection_also_excludes_reading(self):
        self.assertTrue(is_reading_sheet('EP06_逐镜阅读'))
        self.assertFalse(is_reading_sheet('正式分镜母版'))
        self.assertFalse(is_reading_sheet('EP06_逐镜阅读_备注'))
        old = previous_workbook(self.storyboard)
        raw = self.changed(lambda book: setattr(book.copy_worksheet(book['正式生产母版']), 'title', '6集_逐镜阅读'), old)
        self.assertEqual(file_io.extract_xlsx(raw)['canonical_storyboard'], self.storyboard)

    def test_exporter_still_checks_its_fresh_reading_copies(self):
        original = file_io.write_reading_sheets
        def corrupt(book, matrices):
            result = original(book, matrices)
            book['6集_逐镜阅读']['C9'] = '导出器错误'
            return result
        with patch.object(file_io, 'write_reading_sheets', corrupt):
            with self.assertRaisesRegex(ValueError, 'reading/source projection mismatch'):
                file_io.storyboard_to_xlsx(self.storyboard, {'issues': []})


if __name__ == '__main__':
    unittest.main()
