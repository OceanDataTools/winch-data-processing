"""Loaded at start-up (this directory is on PYTHONPATH) when tests/test_openvdm_contract.py runs
OpenVDM's utils/update_md5_summary.py, as the hook does: OpenVDM's class as it is, its argument
parsing and path checks included, but for where it reads its settings, which are the test's,
and the Gearman job it queues, which is recorded (OPENVDM_CONTRACT_RECORD) instead.
"""

import json
import os
import sys

if os.environ.get("OPENVDM_CONTRACT_RECORD"):
    sys.path.insert(0, os.environ["OPENVDM_SOURCE"])
    import python3_gearman
    from server.lib import openvdm

    class _Recorded:
        def __init__(self, servers):
            pass

        def submit_job(self, task, data, background=True):
            with open(os.environ["OPENVDM_CONTRACT_RECORD"], "w") as file:
                json.dump({"task": task, "data": json.loads(data)}, file)

    def _init(self, config_file=None):
        self.config = {}

    openvdm.OpenVDM.__init__ = _init
    openvdm.OpenVDM.get_gearman_server = lambda self: "localhost:4730"
    openvdm.OpenVDM.get_shipboard_data_warehouse_config = lambda self: {
        "shipboardDataWarehouseBaseDir": os.environ["OPENVDM_CONTRACT_WAREHOUSE"]
    }
    python3_gearman.GearmanClient = _Recorded
