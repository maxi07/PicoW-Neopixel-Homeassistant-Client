"""Host-side regression tests for the MicroPython HTTP request reader."""
import json
import unittest

from api_server import APIServer


PAYLOAD = b'{"power":"on","color":{"r":0,"g":100,"b":0}}'


class SocketDouble:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = bytearray()
        self.closed = False

    def recv(self, size):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
        return chunk[:size]

    def send(self, data):
        self.sent.extend(data)
        return len(data)

    def close(self):
        self.closed = True


class ControllerDouble:
    def __init__(self):
        self.color = (255, 255, 255)
        self.is_on = False

    def turn_on(self):
        self.is_on = True

    def set_color(self, r, g, b):
        self.color = (r, g, b)

    def get_state(self):
        return {"is_on": self.is_on, "color": dict(zip("rgb", self.color))}


class HTTPRequestTests(unittest.TestCase):
    def setUp(self):
        self.controller = ControllerDouble()
        self.server = APIServer(self.controller, {"name": "test", "id": "test"})

    def request(self, chunks):
        sock = SocketDouble(chunks)
        self.server._handle_client(sock)
        status, _, body = bytes(sock.sent).partition(b"\r\n\r\n")
        return status, json.loads(body), sock

    def test_fragmented_header_and_body(self):
        header = (b"POST /control HTTP/1.1\r\nHost: pico\r\nContent-Type: application/json\r\nContent-Length: " + str(len(PAYLOAD)).encode() + b"\r\n\r\n")
        status, response, sock = self.request([header[:17], header[17:-4], header[-4:] + PAYLOAD[:7], PAYLOAD[7:]])
        self.assertIn(b"200 OK", status)
        self.assertEqual(response["state"]["color"], {"r": 0, "g": 100, "b": 0})
        self.assertTrue(sock.closed)

    def test_http11_without_connection_close(self):
        header = (b"POST /control HTTP/1.1\r\nHost: pico\r\nUser-Agent: curl/8.7.1\r\nAccept: */*\r\nContent-Type: application/json\r\nContent-Length: " + str(len(PAYLOAD)).encode() + b"\r\n\r\n")
        status, response, _ = self.request([header + PAYLOAD])
        self.assertIn(b"200 OK", status)
        self.assertEqual(response["state"]["color"]["g"], 100)

    def test_malformed_content_length(self):
        status, _, _ = self.request([b"POST /control HTTP/1.1\r\nContent-Length: nope\r\n\r\n{}"])
        self.assertIn(b"400 Bad Request", status)

    def test_truncated_body(self):
        status, _, _ = self.request([b"POST /control HTTP/1.1\r\nContent-Length: " + str(len(PAYLOAD)).encode() + b"\r\n\r\n" + PAYLOAD[:8]])
        self.assertIn(b"400 Bad Request", status)

    def test_oversized_body_rejected(self):
        status, _, _ = self.request([b"POST /control HTTP/1.1\r\nContent-Length: 2049\r\n\r\n"])
        self.assertIn(b"400 Bad Request", status)

    def test_duplicate_content_length_rejected(self):
        status, _, _ = self.request([b"POST /control HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}"])
        self.assertIn(b"400 Bad Request", status)

    def test_get_state(self):
        status, response, _ = self.request([b"GET /state HTTP/1.1\r\nHost: pico\r\n\r\n"])
        self.assertIn(b"200 OK", status)
        self.assertFalse(response["is_on"])


if __name__ == "__main__":
    unittest.main()
