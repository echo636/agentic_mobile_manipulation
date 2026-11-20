"""Owner-thread append log with immediate visibility and one open per lifetime."""


class LineWriter:
    def __init__(self, path):
        self.path = path
        self.stream = None
        self.closed = False

    def write(self, line):
        if self.closed:
            raise ValueError("Append log is closed")
        if self.stream is None:
            self.stream = self.path.open("a", encoding="utf-8")
        self.stream.write(line)
        # Keep tailers and crash evidence as current as open/write/close did.
        # This is a userspace flush, with the same fsync policy as before.
        self.stream.flush()

    def close(self):
        self.closed = True
        stream, self.stream = self.stream, None
        if stream is not None:
            stream.close()
