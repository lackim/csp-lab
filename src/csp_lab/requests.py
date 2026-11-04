"""Strict public request limits and a distinguishable network timeout."""


class RequestTimeout(TimeoutError):
    """No reply arrived; the remote service may already have executed the request."""

    def __init__(self, address: int, port: int, timeout_ms: int) -> None:
        self.address = address
        self.port = port
        self.timeout_ms = timeout_ms
        super().__init__(f"Node {address} port {port} did not reply within {timeout_ms} ms.")


def validate_request(port: int, payload: bytes, timeout_ms: int) -> None:
    if type(port) is not int or not 10 <= port <= 63:
        raise ValueError("Application port must be an integer between 10 and 63.")
    if type(payload) is not bytes or len(payload) > 256:
        raise ValueError("Payload must be bytes with length between 0 and 256.")
    if type(timeout_ms) is not int or not 100 <= timeout_ms <= 10000:
        raise ValueError("Timeout must be an integer between 100 and 10000 ms.")
