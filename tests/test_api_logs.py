import io
import json
import unittest
from unittest.mock import Mock, patch

from api_server import APIServer
from logger import Logger


class Client:
    def __init__(self, request=b"GET /logs HTTP/1.1\r\n\r\n", max_send=100000):
        self.request = request
        self.max_send = max_send
        self.output = bytearray()
        self.closed = False
        self.timeouts = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def recv(self, size):
        return self.request[:size]

    def send(self, data):
        size = min(len(data), self.max_send)
        self.output.extend(data[:size])
        return size

    def close(self):
        self.closed = True


class LogFile(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []

    def read(self, size=-1):
        if not 0 < size <= 512:
            raise AssertionError("Unbounded log read")
        self.read_sizes.append(size)
        return super().read(size)


class LogEndpointTests(unittest.TestCase):
    def setUp(self):
        self.server = APIServer(None, {"name": "Test", "id": "test"})
        self.logger = Mock(log_file="logs.txt")
        self.logger_patch = patch("api_server.logger", self.logger)
        self.logger_patch.start()
        self.addCleanup(self.logger_patch.stop)
        self.clock = Mock()
        self.clock.ticks_ms.return_value = 0
        self.clock.ticks_diff.side_effect = lambda now, start: now - start
        clock_patch = patch("api_server.time", self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def download(self, log_file, client=None):
        client = client or Client()
        with patch("builtins.open", return_value=log_file) as open_file:
            self.server._handle_client(client)
        open_file.assert_called_once_with("logs.txt", "rb")
        self.assertTrue(client.closed)
        self.assertTrue(log_file.closed)
        headers, body = bytes(client.output).split(b"\r\n\r\n", 1)
        return headers, body

    def test_large_utf8_log_is_streamed_in_bounded_blocks(self):
        content = ("[WARNING] Temperatur zu hoch: \u00e4\n" * 1000).encode("utf-8")
        log_file = LogFile(content)
        headers, body = self.download(log_file)
        self.assertEqual(body, content)
        self.assertIn(b"HTTP/1.1 200 OK", headers)
        self.assertIn(b"Content-Type: text/plain; charset=utf-8", headers)
        self.assertIn(
            "Content-Length: {}".format(len(content)).encode(), headers
        )
        self.assertIn(b"Cache-Control: no-store", headers)
        self.assertGreater(len(log_file.read_sizes), 1)
        self.assertEqual(max(log_file.read_sizes), 512)

    def test_partial_socket_writes(self):
        content = b"Warning\n" * 100
        _, body = self.download(LogFile(content), Client(max_send=7))
        self.assertEqual(body, content)

    def test_deadline_applies_to_partial_header_writes(self):
        client = Client(max_send=7)
        log_file = LogFile(b"Warning\n")
        original_send = client.send

        def slow_send(data):
            self.clock.ticks_ms.return_value += 1000
            return original_send(data)

        client.send = slow_send
        with patch("builtins.open", return_value=log_file):
            self.server._handle_client(client)
        self.assertEqual(client.timeouts, [5, 4, 3, 2, 1])
        self.assertEqual(len(client.output), 35)
        self.assertTrue(client.closed)
        self.assertTrue(log_file.closed)
        self.logger.error.assert_called_once()

    def test_deadline_is_shared_across_body_chunks(self):
        client = Client()
        original_send = client.send

        def slow_send(data):
            self.clock.ticks_ms.return_value += 1000
            return original_send(data)

        client.send = slow_send
        headers, body = self.download(LogFile(b"x" * 4096), client)
        self.assertEqual(client.timeouts, [5, 4, 3, 2, 1])
        self.assertIn(b"Content-Length: 4096", headers)
        self.assertEqual(body, b"x" * 2048)
        self.assertEqual(bytes(client.output).count(b"HTTP/1.1"), 1)
        self.logger.error.assert_called_once()

    def test_socket_timeout_uses_remaining_millisecond_budget(self):
        self.clock.ticks_ms.side_effect = [0, 4999]
        client = Client()
        self.download(LogFile(b""), client)
        self.assertEqual(client.timeouts, [0.001])

    def test_close_error_does_not_append_second_response(self):
        log_file = LogFile(b"Warning\n")
        close = log_file.close

        def failing_close():
            close()
            raise OSError("Close failed")

        log_file.close = failing_close
        client = Client()
        _, body = self.download(log_file, client)
        self.assertEqual(body, b"Warning\n")
        self.assertEqual(bytes(client.output).count(b"HTTP/1.1"), 1)
        self.logger.error.assert_called_once_with(
            "Failed to close log file: Close failed"
        )

    def test_empty_file(self):
        log_file = LogFile(b"")
        headers, body = self.download(log_file)
        self.assertIn(b"Content-Length: 0", headers)
        self.assertEqual(body, b"")
        self.assertEqual(log_file.read_sizes, [])

    def test_missing_file_is_empty_success(self):
        client = Client()
        with patch("builtins.open", side_effect=OSError(2, "No such file")):
            self.server._handle_client(client)
        self.assertIn(b"HTTP/1.1 200 OK", client.output)
        self.assertIn(b"Content-Length: 0\r\n", client.output)
        self.assertTrue(bytes(client.output).endswith(b"\r\n\r\n"))
        self.logger.error.assert_not_called()

    def test_file_error_returns_500_and_releases_lock(self):
        client = Client()
        with patch("builtins.open", side_effect=OSError(13, "Permission denied")):
            self.server._handle_client(client)
        self.assertIn(b"HTTP/1.1 500 Internal Server Error", client.output)
        self.logger.error.assert_called_once()
        self.assertTrue(Logger._lock.acquire(False))
        Logger._lock.release()

    def test_new_entries_do_not_extend_snapshot_or_block_logger(self):
        content = b"Original warning\n"
        log_file = LogFile(content)
        client = Client()
        original_send = client.send

        def append_during_send(data):
            self.assertTrue(Logger._lock.acquire(False))
            Logger._lock.release()
            if not client.output:
                position = log_file.tell()
                log_file.seek(0, 2)
                log_file.write(b"New warning\n")
                log_file.seek(position)
            return original_send(data)

        client.send = append_during_send
        headers, body = self.download(log_file, client)
        self.assertEqual(body, content)
        self.assertIn(b"Content-Length: 17", headers)

    def test_disconnect_does_not_send_second_response(self):
        log_file = LogFile(b"Warning\n")
        client = Client()
        original_send = client.send

        def disconnect(data):
            if client.output:
                raise OSError("Disconnected")
            return original_send(data)

        client.send = disconnect
        self.download(log_file, client)
        self.assertEqual(bytes(client.output).count(b"HTTP/1.1"), 1)
        self.logger.error.assert_called_once()

    def test_snapshot_error_closes_file_and_returns_500(self):
        log_file = LogFile(b"Warning\n")
        log_file.seek = Mock(side_effect=OSError("Seek failed"))
        client = Client()
        with patch("builtins.open", return_value=log_file):
            self.server._handle_client(client)
        self.assertIn(b"HTTP/1.1 500 Internal Server Error", client.output)
        self.assertTrue(log_file.closed)
        self.assertTrue(Logger._lock.acquire(False))
        Logger._lock.release()

    def test_read_failure_or_early_eof_closes_incomplete_response(self):
        for read in (
            Mock(side_effect=OSError("Read failed")),
            Mock(return_value=b""),
        ):
            with self.subTest(read=read):
                self.logger.error.reset_mock()
                log_file = LogFile(b"Warning\n")
                log_file.read = read
                client = Client()
                self.download(log_file, client)
                self.assertEqual(bytes(client.output).count(b"HTTP/1.1"), 1)
                self.logger.error.assert_called_once()

    def test_zero_or_none_socket_write_stops_transfer(self):
        for result in (0, None):
            with self.subTest(result=result):
                client = Client()
                client.send = Mock(return_value=result)
                log_file = LogFile(b"Warning\n")
                with patch("builtins.open", return_value=log_file):
                    self.server._handle_client(client)
                client.send.assert_called_once()
                self.assertTrue(log_file.closed)
                self.assertTrue(client.closed)

    def test_root_advertises_logs(self):
        client = Client(request=b"GET / HTTP/1.1\r\n\r\n")
        self.server._handle_client(client)
        body = bytes(client.output).split(b"\r\n\r\n", 1)[1]
        self.assertIn("/logs", json.loads(body)["endpoints"])

    def test_post_logs_is_not_found(self):
        client = Client(request=b"POST /logs HTTP/1.1\r\n\r\n")
        self.server._handle_client(client)
        self.assertIn(b"HTTP/1.1 404 Not Found", client.output)


if __name__ == "__main__":
    unittest.main()
