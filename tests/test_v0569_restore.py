"""The new release keeps the existing signed, single-use data transfer."""
from contextlib import ExitStack
from unittest.mock import patch
import os
import shutil
import unittest

import test_upgrade_0566_backup as existing
import upgrade_prompt_library_0569 as upgrade
from shortdrama_mcp import startup_restore as restore


class PromptLibraryRestoreTests(unittest.TestCase):
    def test_old_image_adapter_matches_new_image_contract(self):
        self.assertEqual((upgrade.DIRECTORY, upgrade.CONTRACT, upgrade.TARGET_VERSION,
                          upgrade.GATE, upgrade.GATE_SQL),
                         (restore.PROMPT_LIBRARY_DIRECTORY, restore.PROMPT_LIBRARY_CONTRACT,
                          restore.PROMPT_LIBRARY_VERSION, restore.PROMPT_LIBRARY_GATE,
                          restore.PROMPT_LIBRARY_GATE_SQL))
        self.assertEqual(existing.prepare.MAX_EXPANDED, restore.PROMPT_LIBRARY_MAX_EXPANDED_BYTES)

    @unittest.skipUnless(os.name == 'posix', 'Linux maintenance uses procfs and directory fsync')
    def test_adapter_restores_all_bytes_without_replaying_a_consumed_transfer(self):
        with ExitStack() as scope:
            for module, keys in [(existing.prepare, ('DIRECTORY', 'CONTRACT', 'TARGET_VERSION')),
                                 (existing.arm, ('DIRECTORY', 'CONTRACT', 'TARGET_VERSION', 'GATE', 'GATE_SQL'))]:
                for key in keys:
                    scope.enter_context(patch.object(module, key, getattr(module, key)))
            upgrade.configure()
            f = existing.Upgrade0566BackupTests(methodName='runTest')
            f.setUp()
            self.addCleanup(f.doCleanups)
            original = restore._tree(f.runtime)
            protected = restore.protected_fingerprints(f.data)
            f.publish()
            shutil.rmtree(f.runtime)
            with self.assertRaisesRegex(ValueError, 'manifest_binding'):
                restore._restore(f.data, f.runtime, version='0.5.68',
                                 directory=upgrade.DIRECTORY, contract=upgrade.CONTRACT)
            self.assertTrue(f.gate_present())
            self.assertFalse(f.runtime.exists())
            self.assertTrue(f.restore()['restored'])
            self.assertEqual(restore._tree(f.runtime), original)
            self.assertEqual(restore.protected_fingerprints(f.data), protected)
            with patch('shortdrama_mcp.__version__', '0.5.69'):
                self.assertEqual(existing.arm.verify_release(f.data, f.runtime)['status'],
                                 'RESTORE_VERIFIED_GATE_CLOSED')
                self.assertTrue(f.gate_present())
                self.assertEqual(existing.arm.verify_release(f.data, f.runtime, reopen=True)['status'],
                                 'RESTORE_VERIFIED_DISPATCH_REOPENED')
                self.assertFalse(f.gate_present())
            self.assertIsNone(f.restore())
