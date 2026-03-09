"""
REST API Server for NeoPixel Controller
Provides HTTP endpoints for controlling the LED strip
"""
import socket
import json
from logger import Logger

logger = Logger("API")


class APIServer:
    def __init__(self, neopixel_controller, device_info, port=80, network_mgr=None):
        """Initialize API server"""
        self.controller = neopixel_controller
        self.device_info = device_info
        self.port = port
        self.network_mgr = network_mgr
        self.socket = None
        self.running = False

    def start(self):
        """Start the API server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.listen(5)
            self.socket.setblocking(False)  # Non-blocking mode
            self.running = True
            logger.info("API server started on port {}".format(self.port))
            return True
        except Exception as e:
            logger.error("Failed to start API server: {}".format(e))
            return False

    def stop(self):
        """Stop the API server"""
        try:
            self.running = False
            if self.socket:
                self.socket.close()
            logger.info("API server stopped")
        except Exception as e:
            logger.error("Failed to stop API server: {}".format(e))

    def handle_requests(self):
        """Non-blocking request handler - call frequently in main loop"""
        if not self.running:
            return

        try:
            # Accept new connection (non-blocking)
            try:
                client, addr = self.socket.accept()
                client.settimeout(5.0)  # 5 second timeout for client operations
                logger.debug("Connection from {}".format(addr))
                self._handle_client(client)
            except OSError:
                # No connection available, continue
                pass
        except Exception as e:
            logger.error("Error handling request: {}".format(e))

    def _handle_client(self, client):
        """Handle individual client request"""
        try:
            # Read request
            request = client.recv(1024).decode('utf-8')

            if not request:
                client.close()
                return

            # Parse HTTP request
            lines = request.split('\r\n')
            if len(lines) < 1:
                client.close()
                return

            request_line = lines[0].split(' ')
            if len(request_line) < 2:
                client.close()
                return

            method = request_line[0]
            path = request_line[1]

            logger.debug("{} {}".format(method, path))

            # Route request
            if method == "GET" and path == "/":
                self._handle_root(client)
            elif method == "GET" and path == "/state":
                self._handle_get_state(client)
            elif method == "GET" and path == "/info":
                self._handle_get_info(client)
            elif method == "POST" and path.startswith("/control"):
                body = self._extract_body(request)
                self._handle_control(client, body)
            else:
                self._send_response(client, 404, {"error": "Not found"})

        except Exception as e:
            logger.error("Client handling error: {}".format(e))
            try:
                self._send_response(client, 500, {"error": "Internal server error"})
            except Exception:
                pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _extract_body(self, request):
        """Extract JSON body from POST request"""
        try:
            parts = request.split('\r\n\r\n')
            if len(parts) >= 2:
                return parts[1]
            return ""
        except Exception:
            return ""

    def _handle_root(self, client):
        """Handle root endpoint"""
        response = {
            "device": self.device_info["name"],
            "id": self.device_info["id"],
            "version": "1.0",
            "endpoints": ["/", "/state", "/info", "/control"]
        }
        self._send_response(client, 200, response)

    def _handle_get_state(self, client):
        """Handle state request"""
        try:
            state = self.controller.get_state()
            # Add WiFi diagnostics to state if network_mgr is available
            if self.network_mgr and "diagnostics" in state:
                state["diagnostics"]["wifi_rssi"] = self.network_mgr.get_rssi()
                state["diagnostics"]["wifi_signal_percent"] = self.network_mgr.get_signal_percent()
                state["diagnostics"]["wifi_channel"] = self.network_mgr.get_channel()
            diag = state.get('diagnostics', {})
            logger.debug("Returning state: is_on={}, brightness={}, effect={}, color=({},{},{}), current={}mA, free_mem={}, rssi={}".format(
                state.get('is_on'),
                state.get('brightness'),
                state.get('effect'),
                state.get('color', {}).get('r', 0),
                state.get('color', {}).get('g', 0),
                state.get('color', {}).get('b', 0),
                diag.get('estimated_current_ma'),
                diag.get('free_memory'),
                diag.get('wifi_rssi'),
            ))
            self._send_response(client, 200, state)
        except Exception as e:
            logger.error("Failed to get state: {}".format(e))
            self._send_response(client, 500, {"error": "Failed to get state"})

    def _handle_get_info(self, client):
        """Handle device info request"""
        import gc
        info = {
            "device": self.device_info,
            "capabilities": {
                "effects": [
                    "static", "rainbow", "fade", "chase",
                    "breathing", "twinkle", "scanner", "strobe"
                ],
                "num_leds": self.controller.num_leds,
                "supports_rgb": True,
                "supports_brightness": True,
                "supports_play_once": True
            },
            "diagnostics": {
                "estimated_current_ma": self.controller.get_estimated_current_ma(),
                "free_memory": gc.mem_free(),
            }
        }
        # Add WiFi diagnostics if network_mgr is available
        if self.network_mgr:
            info["diagnostics"]["wifi_rssi"] = self.network_mgr.get_rssi()
            info["diagnostics"]["wifi_signal_percent"] = self.network_mgr.get_signal_percent()
            info["diagnostics"]["wifi_channel"] = self.network_mgr.get_channel()
        logger.debug("Returning device info: name={}, id={}, num_leds={}".format(
            self.device_info.get('name'),
            self.device_info.get('id'),
            self.controller.num_leds
        ))
        self._send_response(client, 200, info)

    def _handle_control(self, client, body):
        """Handle control command"""
        try:
            if not body:
                logger.debug("Control request with no body")
                self._send_response(client, 400, {"error": "No body provided"})
                return

            command = json.loads(body)
            logger.debug("Control command received: {}".format(json.dumps(command)))

            # Handle one-shot animation (exclusive — returns immediately)
            if "play_once" in command:
                effect = command["play_once"]
                speed = command.get("speed", 50)
                color = command.get("color", None)
                color_tuple = None
                if color:
                    color_tuple = (color.get("r", 255), color.get("g", 0), color.get("b", 0))
                logger.debug("Playing one-shot: {} (speed: {})".format(effect, speed))
                self.controller.play_once(effect, speed, color_tuple)
                state = self.controller.get_state()
                self._send_response(client, 200, {"success": True, "state": state})
                return

            # Process command
            if "power" in command:
                if command["power"] == "on":
                    logger.debug("Turning on")
                    self.controller.turn_on()
                elif command["power"] == "off":
                    logger.debug("Turning off")
                    self.controller.turn_off()

            if "brightness" in command:
                brightness = command["brightness"]
                logger.debug("Setting brightness to {}".format(brightness))
                self.controller.set_brightness(brightness)

            if "speed" in command and "effect" not in command:
                speed = command["speed"]
                logger.debug("Setting animation speed to {}".format(speed))
                self.controller.set_speed(speed)

            if "color" in command and "effect" not in command:
                color = command["color"]
                r = color.get("r", 0)
                g = color.get("g", 0)
                b = color.get("b", 0)
                logger.debug("Setting color to RGB({},{},{})".format(r, g, b))
                self.controller.set_color(r, g, b)

            # Process progress BEFORE effect so that set_effect can snap to it
            if "progress" in command:
                progress = command["progress"]
                logger.debug("Setting progress to {}".format(progress))
                self.controller.set_progress(progress)

            if "effect" in command:
                effect = command["effect"]
                speed = command.get("speed", 50)

                # Set effect color if provided
                if "color" in command:
                    color = command["color"]
                    self.controller.effect_color = (
                        color.get("r", 255),
                        color.get("g", 0),
                        color.get("b", 0)
                    )
                    logger.debug("Setting effect '{}' with speed {} and color RGB({},{},{})".format(
                        effect, speed,
                        color.get("r", 255),
                        color.get("g", 0),
                        color.get("b", 0)
                    ))
                else:
                    logger.debug("Setting effect '{}' with speed {}".format(effect, speed))

                self.controller.set_effect(effect, speed)

            # Return updated state
            state = self.controller.get_state()
            logger.debug("Control successful, new state: is_on={}, brightness={}, effect={}".format(
                state.get('is_on'),
                state.get('brightness'),
                state.get('effect')
            ))
            self._send_response(client, 200, {"success": True, "state": state})

        except json.JSONDecodeError:
            self._send_response(client, 400, {"error": "Invalid JSON"})
        except Exception as e:
            logger.error("Control error: {}".format(e))
            self._send_response(client, 500, {"error": str(e)})

    def _send_response(self, client, status_code, data):
        """Send HTTP JSON response"""
        try:
            status_messages = {
                200: "OK",
                400: "Bad Request",
                404: "Not Found",
                500: "Internal Server Error"
            }

            status_message = status_messages.get(status_code, "Unknown")
            json_data = json.dumps(data)

            response = "HTTP/1.1 {} {}\r\n".format(status_code, status_message)
            response += "Content-Type: application/json\r\n"
            response += "Content-Length: {}\r\n".format(len(json_data))
            response += "Access-Control-Allow-Origin: *\r\n"
            response += "Connection: close\r\n"
            response += "\r\n"
            response += json_data

            client.send(response.encode('utf-8'))
        except Exception as e:
            logger.error("Failed to send response: {}".format(e))
