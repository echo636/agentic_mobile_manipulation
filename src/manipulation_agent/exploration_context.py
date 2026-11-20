"""Controller API. No simulator imports, credentials or lifecycle operations."""
import json
import os
import socket
import uuid

class Context:
    def __init__(self, socket_path=None):
        self.socket_path = socket_path or os.environ["MAS_EXPLORATION_SOCKET"]
        self.revision = None
        self._manip = None
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def call(self, tool, **arguments):
        if tool == 'manip':
            if not callable(self._manip):
                raise RuntimeError('This frozen project has no Manip implementation')
            arguments.setdefault('revision', self.revision)
            ident = str(uuid.uuid4())
            begin = self._rpc(tool, arguments, ident, stage='begin')
            try:
                result = self._manip(self, **begin['invocation'])
            except Exception as exc:
                result = {'ok': False, 'error': str(exc), 'phase': 'recipe_exception'}
            return self._rpc(tool, result, ident, stage='end')
        ident = str(uuid.uuid4())
        return self._rpc(tool, arguments, ident)

    def bind_manip(self, implementation):
        """The runner binds the function from this project's read-only bundle."""
        self._manip = implementation

    def _rpc(self, tool, arguments, ident, **metadata):
        request = {"name": tool, "arguments": arguments, "request_id": ident}
        request.update(metadata)
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(self.socket_path)
            stream = s.makefile("rwb")
            stream.write(json.dumps(request, allow_nan=False).encode() + b"\n")
            stream.flush()
            line = stream.readline(8_000_001)
        if not line or len(line) > 8_000_000:
            raise RuntimeError("Invalid context response; dispatched motion may still exist")
        packet = json.loads(line)
        observation = packet.get("observation", {})
        self.revision = observation.get("revision", self.revision)
        if packet.get("ok") is False:
            # Preserve current observation on failure, never automatically retry.
            error = RuntimeError(json.dumps(packet, ensure_ascii=False))
            error.reply = packet
            raise error
        return packet
    def require(self, condition, why):
        if not condition:
            raise RuntimeError(why)
