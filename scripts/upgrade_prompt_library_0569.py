"""Version-bound CLI adapter for the existing signed transfer implementation.

Only run on an inventoried Linux service with its current runtime intact.
This adapter can use the 0.5.68 image's helpers without replacing server code.
"""
import sys

import prepare_upgrade_0566_backup as prepare
import arm_upgrade_0566_backup as arm

DIRECTORY = '.upgrade-prompt-library-0569'
CONTRACT = 'library-runtime-transfer-prompt-library-0569-v1'
TARGET_VERSION = '0.5.69'
GATE = 'prompt_library_upgrade_gate_0569'
GATE_SQL = (f'CREATE TRIGGER {GATE} BEFORE INSERT ON access_audit '
            "WHEN NEW.outcome='started' BEGIN "
            "SELECT RAISE(ABORT, 'prompt_library_upgrade_in_progress'); END")


def configure():
    # These modules live only in this maintenance command's process.
    for module in (prepare, arm):
        module.DIRECTORY = DIRECTORY
        module.CONTRACT = CONTRACT
        module.TARGET_VERSION = TARGET_VERSION
    arm.GATE, arm.GATE_SQL = GATE, GATE_SQL


def main():
    configure()
    if sys.argv[1:] == ['prepare']:
        prepare.prepare()
    else:
        arm.main()


if __name__ == '__main__':
    main()
