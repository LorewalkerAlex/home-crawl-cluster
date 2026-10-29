"""Bounded standard-library JSON transport for the local verification servers."""

from http.server import BaseHTTPRequestHandler
import json

from .contracts import MAX_BODY_BYTES, ValidationError


class HttpError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("Duplicate JSON key")
        result[key] = value
    return result


class JsonHandler(BaseHTTPRequestHandler):
    validation_code = "invalid_callback"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_args):
        pass

    def respond(self, status, payload):
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def error(self, status, code):
        self.respond(status, {"error": code})

    def read_json(self):
        if self.headers.get("Transfer-Encoding") is not None:
            raise HttpError(400, "transfer_encoding_not_supported")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HttpError(415, "application_json_required")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise HttpError(411, "one_content_length_required")
        if len(lengths[0]) > 10 or not 1 <= int(lengths[0]) <= MAX_BODY_BYTES:
            raise HttpError(413, "body_size_limit")
        raw = self.rfile.read(int(lengths[0]))
        if len(raw) != int(lengths[0]):
            raise ValidationError("Incomplete body")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)

    def run_json(self, action):
        try:
            try:
                status, payload = action()
            except HttpError as error:
                self.error(error.status, error.code)
                return
            except (ValidationError, ValueError, UnicodeError, RecursionError):
                self.error(422, self.validation_code)
                return
            except TimeoutError:
                self.error(408, "body_timeout")
                return
            except Exception:
                self.error(500, "internal_error")
                return
            self.respond(status, payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A lost response does not undo a previously committed transaction.
            self.close_connection = True
