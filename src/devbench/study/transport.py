"""Study protocol adapter over the engine's local Unix transport."""
from devbench.engine.transport import BrokerTransport as _EngineBrokerTransport, request_deadline
from devbench.engine.workspace import UNIX_SOCKET_PATH_MAX, bounded_unix_socket

from .broker import MAX_REQUEST_BYTES, MAX_RESULT_BYTES, parse_request


class BrokerTransport(_EngineBrokerTransport):
    def __init__(self, broker, socket_path, *, allow_data_execution=False):
        super().__init__(broker, socket_path, parse_request=parse_request,
                         max_request_bytes=MAX_REQUEST_BYTES, max_result_bytes=MAX_RESULT_BYTES,
                         allow_data_execution=allow_data_execution)
