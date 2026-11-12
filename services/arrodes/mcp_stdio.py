"""Unprivileged stdio transport. No application credentials or imports."""
import json
import socket
import sys

with socket.socket(socket.AF_UNIX) as connection:
    connection.connect(sys.argv[1])
    stream = connection.makefile('rwb')
    for line in sys.stdin.buffer:
        stream.write(line)
        stream.flush()
        # Notifications have no JSON-RPC response.
        if 'id' in json.loads(line):
            answer = stream.readline()
            if not answer:
                break
            sys.stdout.buffer.write(answer)
            sys.stdout.buffer.flush()
