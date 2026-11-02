"""Regression tests for MicroPython ujson without JSONDecodeError."""
import importlib.util
import json
import sys
import types
import unittest
from unittest.mock import patch


class FakeClient:
    def __init__(self):
        self.response = b""
        self.closed = False

    def send(self, data):
        self.response += data
        return len(data)

    def close(self):
        self.closed = True


class JsonErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake_logger = types.ModuleType("logger")
        fake_logger.Logger = lambda name: types.SimpleNamespace(debug=lambda message: None, error=lambda message: cls.errors.append(message))
        cls.errors = []
        fake_version = types.ModuleType("version")
        fake_version.VERSION = "test"
        with patch.dict(sys.modules, {"logger": fake_logger, "version": fake_version}):
            spec = importlib.util.spec_from_file_location("api_server_under_test", "api_server.py")
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.errors.clear()
        self.server = self.module.APIServer(None, {"name": "test", "id": "test"})
        self.micro_json = types.SimpleNamespace(loads=json.loads, dumps=json.dumps)
        self.server_json = self.module.json
        self.module.json = self.micro_json

    def tearDown(self):
        self.module.json = self.server_json

    def test_invalid_json_returns_400_without_jsondecodeerror_attribute(self):
        client = FakeClient()
        self.server._handle_control(client, '{"power":')
        self.assertIn(b"HTTP/1.1 400 Bad Request", client.response)
        self.assertIn(b'"error": "Invalid JSON"', client.response)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("Invalid JSON in control request:", self.errors[0])
        self.assertNotIn('{"power":', self.errors[0])

    def test_unrelated_error_is_not_mislabeled_invalid_json(self):
        client = FakeClient()
        controller = types.SimpleNamespace(turn_on=lambda: 1 / 0)
        self.server.controller = controller
        self.server._handle_control(client, '{"power":"on"}')
        self.assertIn(b"HTTP/1.1 500 Internal Server Error", client.response)
        self.assertTrue(any("Control error:" in error for error in self.errors))
        self.assertFalse(any("JSONDecodeError" in error for error in self.errors))

    def test_client_handler_reports_original_exception(self):
        client = FakeClient()
        self.server._extract_body = lambda request: '{"power":"on"}'
        self.server.controller = types.SimpleNamespace(turn_on=lambda: 1 / 0)
        client.recv = lambda size: b'POST /control HTTP/1.1\r\n\r\n{"power":"on"}'
        self.server._handle_client(client)
        self.assertIn(b"HTTP/1.1 500 Internal Server Error", client.response)
        self.assertTrue(client.closed)
        self.assertFalse(any("JSONDecodeError" in error for error in self.errors))
        self.assertTrue(any("Control error:" in error for error in self.errors))


if __name__ == "__main__":
    unittest.main()
