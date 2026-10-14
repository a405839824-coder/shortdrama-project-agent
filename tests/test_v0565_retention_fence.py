"""A restored tree is immutable until explicit release verification reopens dispatch."""
import sqlite3
from contextlib import closing
from pathlib import Path
import unittest
import test_retention as fixtures
from shortdrama_mcp.startup_restore import PRODUCTION_GROUNDING_GATE_SQL, PRODUCTION_GROUNDING_GATE
from shortdrama_mcp.retention import EXPORT_GRACE


class ReleaseRetentionFenceTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.RetentionTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def test_consumed_marker_with_gate_preserves_expired_exports_until_reopen(self):
        f = self.fx
        export = f.external.export_snapshot(f.namespace, f.project)
        path = Path(export['path'])
        now = export['expires_epoch'] + EXPORT_GRACE + 1
        with closing(sqlite3.connect(f.store.root / 'account_access.sqlite3')) as db, db:
            db.execute('CREATE TABLE access_audit (outcome TEXT)')
            db.execute(PRODUCTION_GROUNDING_GATE_SQL)
        receipt = f.store.root / '.upgrade-production-grounding-0565'
        receipt.mkdir()
        (receipt / 'manifest.consumed.json').write_text('{}')
        before = path.read_bytes()
        result = f.worker.run_once(now=now)
        self.assertEqual(result['deleted'], [])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result['skipped'][0]['reason'], 'release_verification_pending')
        with closing(sqlite3.connect(f.store.root / 'account_access.sqlite3')) as db, db:
            db.execute('DROP TRIGGER ' + PRODUCTION_GROUNDING_GATE)
        self.assertEqual([x['path'] for x in f.worker.run_once(now=now)['deleted']], [str(path)])

    def test_pending_marker_blocks_exports_even_before_gate_installation(self):
        f = self.fx
        export = f.external.export_snapshot(f.namespace, f.project)
        d = f.store.root / '.upgrade-production-grounding-0565'
        d.mkdir(); (d / 'manifest.json').write_text('{}')
        result = f.worker.run_once(now=export['expires_epoch'] + EXPORT_GRACE + 1)
        self.assertEqual(result['deleted'], [])
        self.assertTrue(Path(export['path']).exists())

    def test_unreadable_database_keeps_every_copy(self):
        f = self.fx
        paths = [f.archive(n) for n in range(3)]
        (f.store.root / 'account_access.sqlite3').write_bytes(b'not sqlite')
        result = f.worker.run_once(now=f.now)
        self.assertEqual(result['deleted'], [])
        self.assertTrue(all(p.exists() for p in paths))
        self.assertEqual(result['skipped'][0]['reason'], 'maintenance_gate_state_unreadable')
