"""Optional local SSH adapter for the PC dashboard and result receiver."""

import os

import paramiko


def connect():
    host = os.environ["ZYNQ_SSH_HOST"]
    username = os.environ.get("ZYNQ_SSH_USER", "xilinx")
    port = int(os.environ.get("ZYNQ_SSH_PORT", "22"))
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            allow_agent=True,
            look_for_keys=True,
            timeout=5,
            banner_timeout=5,
            auth_timeout=5,
        )
    except Exception:
        client.close()
        raise
    return client
