"""Run the tested signed-transfer lifecycle under new immutable release IDs.

No older transfer is changed. Run prepare, freeze, mark-publication, verify,
and verify-and-reopen in that order; keep freeze alive through publication.
"""
import sys
import prepare_upgrade_0566_backup as prepare
import arm_upgrade_0566_backup as arm

DIRECTORY = '.upgrade-master-lighting-0569'
CONTRACT = 'library-runtime-transfer-master-lighting-0569-v1'
TARGET_VERSION = '0.5.69'
GATE = 'master_lighting_upgrade_gate_0569'
GATE_SQL = (f'CREATE TRIGGER {GATE} BEFORE INSERT ON access_audit '
            "WHEN NEW.outcome='started' BEGIN "
            "SELECT RAISE(ABORT, 'master_lighting_upgrade_in_progress'); END")


def configure():
    for module in (prepare, arm):
        module.DIRECTORY, module.CONTRACT, module.TARGET_VERSION = DIRECTORY, CONTRACT, TARGET_VERSION
    arm.GATE, arm.GATE_SQL = GATE, GATE_SQL


if __name__ == '__main__':
    configure()
    if sys.argv[1:] == ['prepare']:
        prepare.prepare()
    else:
        arm.main()
