"""Reuse the signed-transfer implementation with the new immutable release IDs."""
from contextlib import ExitStack
from unittest.mock import patch
import shutil
import unittest
import test_upgrade_0566_backup as existing
from shortdrama_mcp import startup_restore as restore


class ScriptScopeRestoreTests(unittest.TestCase):
    def test_new_release_restores_all_bytes_and_requires_explicit_reopen(self):
        f=existing.Upgrade0566BackupTests(methodName='runTest');f.setUp();self.addCleanup(f.doCleanups)
        with ExitStack() as scope:
            for module in (existing.prepare,existing.arm):
                scope.enter_context(patch.object(module,'DIRECTORY',restore.SCRIPT_SCOPE_DIRECTORY))
                scope.enter_context(patch.object(module,'CONTRACT',restore.SCRIPT_SCOPE_CONTRACT))
                scope.enter_context(patch.object(module,'TARGET_VERSION',restore.SCRIPT_SCOPE_VERSION))
            scope.enter_context(patch.object(existing.arm,'GATE',restore.SCRIPT_SCOPE_GATE))
            scope.enter_context(patch.object(existing.arm,'GATE_SQL',restore.SCRIPT_SCOPE_GATE_SQL))
            f.target=f.data/restore.SCRIPT_SCOPE_DIRECTORY
            original=restore._tree(f.runtime);protected=restore.protected_fingerprints(f.data)
            f.publish()
            shutil.rmtree(f.runtime)
            with self.assertRaisesRegex(ValueError,'manifest_binding'):
                restore._restore(f.data,f.runtime,version='0.5.66',directory=restore.SCRIPT_SCOPE_DIRECTORY,contract=restore.SCRIPT_SCOPE_CONTRACT)
            self.assertTrue(f.gate_present());self.assertFalse(f.runtime.exists())
            receipt=f.restore();self.assertTrue(receipt['restored'])
            self.assertEqual(restore._tree(f.runtime),original)
            self.assertEqual(restore.protected_fingerprints(f.data),protected)
            with patch('shortdrama_mcp.__version__','0.5.67'):
                self.assertEqual(existing.arm.verify_release(f.data,f.runtime)['status'],'RESTORE_VERIFIED_GATE_CLOSED')
                self.assertTrue(f.gate_present())
                self.assertEqual(existing.arm.verify_release(f.data,f.runtime,reopen=True)['status'],'RESTORE_VERIFIED_DISPATCH_REOPENED')
                self.assertFalse(f.gate_present())
