"""Exact bytes and protected state across the project-template release."""
from contextlib import ExitStack
from unittest.mock import patch
import shutil
import os
import unittest
import test_upgrade_0566_backup as existing
from shortdrama_mcp import startup_restore as restore


@unittest.skipUnless(os.name == 'posix', 'Linux release transfer requires directory fsync')
class MasterLightingRestoreTests(unittest.TestCase):
    def test_fresh_release_keeps_bytes_and_requires_reopen(self):
        f = existing.Upgrade0566BackupTests(methodName='runTest')
        f.setUp(); self.addCleanup(f.doCleanups)
        with ExitStack() as scope:
            for module in (existing.prepare, existing.arm):
                for key in ('DIRECTORY', 'CONTRACT', 'TARGET_VERSION'):
                    name = 'VERSION' if key == 'TARGET_VERSION' else key
                    scope.enter_context(patch.object(module, key, getattr(restore, 'MASTER_LIGHTING_'+name)))
            scope.enter_context(patch.object(existing.arm, 'GATE', restore.MASTER_LIGHTING_GATE))
            scope.enter_context(patch.object(existing.arm, 'GATE_SQL', restore.MASTER_LIGHTING_GATE_SQL))
            f.target = f.data / restore.MASTER_LIGHTING_DIRECTORY
            original = restore._tree(f.runtime)
            protected = restore.protected_fingerprints(f.data)
            f.publish(); shutil.rmtree(f.runtime)
            with self.assertRaisesRegex(ValueError, 'manifest_binding'):
                restore._restore(f.data, f.runtime, version='0.5.68',
                    directory=restore.MASTER_LIGHTING_DIRECTORY, contract=restore.MASTER_LIGHTING_CONTRACT)
            self.assertTrue(f.gate_present())
            self.assertTrue(f.restore()['restored'])
            self.assertEqual(restore._tree(f.runtime), original)
            self.assertEqual(restore.protected_fingerprints(f.data), protected)
            self.assertEqual(existing.arm.verify_release(f.data,f.runtime)['status'], 'RESTORE_VERIFIED_GATE_CLOSED')
            self.assertTrue(f.gate_present())
            self.assertEqual(existing.arm.verify_release(f.data,f.runtime,reopen=True)['status'],
                             'RESTORE_VERIFIED_DISPATCH_REOPENED')
