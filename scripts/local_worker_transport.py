#!/usr/bin/env python3
"""SSH-shaped local transport for the already authorized V05 worker account."""
import os
import shlex
import socket
import sys


def main():
    if len(sys.argv) < 3 or sys.argv[1] != 'local-v05' or socket.gethostname() != 'ZJU3DV-V05':
        raise SystemExit('local-v05 transport must run on ZJU3DV-V05 with a command')
    # SSH receives one shell command from the runner/MCP client; rsync supplies
    # a sequence of server arguments. Preserve both forms and streaming stdio.
    command = sys.argv[2] if len(sys.argv) == 3 else shlex.join(sys.argv[2:])
    os.execv('/bin/bash', ['bash', '-lc', command])


if __name__ == '__main__':
    main()
