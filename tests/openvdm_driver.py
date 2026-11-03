"""Runs a piece of OpenVDM's own code for tests/test_openvdm_contract.py (as nav-qa-processing's),
under OpenVDM's Python, with its source (OPENVDM_SOURCE) on the path: reads a request as JSON on stdin and
writes the result as JSON on stdout. Only what reaches outside, OpenVDM's API and its file
permissions, is stood in for.
"""

import copy
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.environ["OPENVDM_SOURCE"])


def hook_commands(request: dict) -> dict:
    """The commands OpenVDM's post_hooks worker runs for a hook: its openvdm.yaml read by
    OpenVDM, the transfer's entry chosen and the placeholders filled as OpenVDM does
    """
    from server.lib.openvdm import OpenVDM
    from server.workers import post_hooks

    ovdm = OpenVDM(request["openvdm_yaml"])
    transfer = request.get("transfer")
    ovdm.get_collection_system_transfer = lambda transfer_id: transfer  # the API's answer
    worker = object.__new__(post_hooks.OVDMGearmanWorker)
    worker.ovdm = ovdm
    worker.task = {"name": request["hook"]}
    worker.job_data = {
        "collectionSystemTransferID": (transfer or {}).get("collectionSystemTransferID")
    }
    worker.cruise_id = request["cruise_id"]
    worker.lowering_id = None
    worker.files = request.get("files") or {"new": [], "updated": []}
    worker.hook_commands = ovdm.get_post_hook_commands(request["hook"])
    return worker.get_command_list()


def export_cruise_config(request: dict) -> dict:
    """The cruise config file OpenVDM's cruise worker writes into the cruise's directory, from
    the API's cruise config and openvdm.yaml's vessel block
    """
    from server.workers import cruise

    cruise.set_owner_group_permissions = lambda *args, **kwargs: {"verdict": True}
    config = request["cruise_config"]
    worker = object.__new__(cruise.OVDMGearmanWorker)
    worker.ovdm = SimpleNamespace(
        get_cruise_config=lambda: copy.deepcopy(config),
        get_vessel_config=lambda: request.get("vessel"),
        update_md5_summary=lambda **kwargs: True,
    )
    worker.cruise_id = config["cruiseID"]
    worker.cruise_dir = request["cruise_dir"]
    worker.shipboard_data_warehouse_config = config["warehouseConfig"]
    return worker.export_cruise_config()


if __name__ == "__main__":
    request = json.load(sys.stdin)
    action = {"hook_commands": hook_commands, "export_cruise_config": export_cruise_config}
    json.dump(action[request.pop("action")](request), sys.stdout)
