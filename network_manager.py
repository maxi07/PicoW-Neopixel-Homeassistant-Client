"""
Network Manager with Auto-Reconnect
Manages WiFi connection with automatic reconnection on failure
"""
import network
import time
from logger import Logger

logger = Logger("Network")


class NetworkManager:
    def __init__(self, ssid, password):
        """Initialize network manager"""
        self.ssid = ssid
        self.password = password
        self.wlan = network.WLAN(network.STA_IF)
        self.connected = False
        self.last_check = 0
        self.check_interval = 5000  # Check connection every 5 seconds
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10

    # WLAN status code descriptions for logging
    _STATUS_NAMES = {
        0: "LINK_DOWN",
        1: "LINK_JOIN (connecting...)",
        2: "LINK_NOIP (no IP yet)",
        3: "LINK_UP (connected)",
        -1: "LINK_FAIL",
        -2: "LINK_NONET (no network found)",
        -3: "LINK_BADAUTH (wrong password)",
    }

    def _status_name(self, status):
        """Get human-readable name for WLAN status code"""
        return self._STATUS_NAMES.get(status, "UNKNOWN({})".format(status))

    def _hard_reset_interface(self):
        """Fully reset the WLAN interface to a clean state"""
        try:
            self.wlan.disconnect()
        except Exception:
            pass
        time.sleep(0.5)

        self.wlan.active(False)
        time.sleep(3)
        self.wlan.active(True)
        time.sleep(3)

        # Disable power management (improves connection reliability)
        try:
            self.wlan.config(pm=0xa11140)
        except Exception:
            pass

        # Wait until interface is ready (status should be LINK_DOWN=0)
        for _ in range(5):
            s = self.wlan.status()
            if s == 0:
                break
            logger.info("Waiting for interface ready, status: {}".format(
                self._status_name(s)))
            time.sleep(1)

    def connect(self, max_attempts=5):
        """Connect to WiFi with retry logic"""
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info("Connecting to WiFi: {} (attempt {}/{})".format(
                    self.ssid, attempt, max_attempts))

                # Full reset of WLAN interface before each attempt
                self._hard_reset_interface()

                # Check status after reset
                status = self.wlan.status()
                logger.info("Interface status after reset: {}".format(
                    self._status_name(status)))

                # If still stuck in LINK_JOIN (1), force another reset
                if status == 1:
                    logger.warning("Interface stuck in LINK_JOIN, forcing extra reset...")
                    self.wlan.active(False)
                    time.sleep(3)
                    self.wlan.active(True)
                    time.sleep(2)
                    try:
                        self.wlan.config(pm=0xa11140)
                    except Exception:
                        pass

                # Initiate connection
                self.wlan.connect(self.ssid, self.password)

                # Give the chip time to start scanning before checking status
                time.sleep(3)

                # Wait for connection with status logging
                max_wait = 30
                last_status = -99
                while max_wait > 0:
                    status = self.wlan.status()
                    if status != last_status:
                        logger.info("WiFi status: {}".format(
                            self._status_name(status)))
                        last_status = status
                    if status < 0 or status >= 3:
                        break
                    max_wait -= 1
                    time.sleep(1)

                # Check if connected
                if self.wlan.status() == 3:
                    ifconfig = self.wlan.ifconfig()
                    logger.info("Connected! IP: {}".format(ifconfig[0]))
                    self.connected = True
                    self.reconnect_attempts = 0
                    return True

                # Connection failed, log and retry
                final_status = self.wlan.status()
                logger.warning("Failed to connect, final status: {}".format(
                    self._status_name(final_status)))

            except Exception as e:
                logger.error("WiFi connection error: {}".format(e))

            # Increasing wait before next attempt
            if attempt < max_attempts:
                wait_time = min(2 * attempt, 10)
                logger.info("Retrying in {} seconds...".format(wait_time))
                time.sleep(wait_time)

        # All attempts failed
        logger.error("Failed to connect after {} attempts".format(max_attempts))
        self.connected = False
        return False

    def disconnect(self):
        """Disconnect from WiFi"""
        try:
            self.wlan.disconnect()
            self.wlan.active(False)
            self.connected = False
            logger.info("Disconnected from WiFi")
        except Exception as e:
            logger.error("Disconnect error: {}".format(e))

    def check_connection(self):
        """Non-blocking connection check - call frequently in main loop"""
        current_time = time.ticks_ms()

        if time.ticks_diff(current_time, self.last_check) < self.check_interval:
            return self.connected

        self.last_check = current_time

        try:
            # Check connection status
            was_connected = self.connected
            self.connected = self.wlan.isconnected()

            # Connection lost
            if was_connected and not self.connected:
                logger.warning("WiFi connection lost")
                self.reconnect_attempts = 0

            # Try to reconnect if disconnected
            if not self.connected and self.reconnect_attempts < self.max_reconnect_attempts:
                self.reconnect_attempts += 1
                logger.info("Reconnection attempt {}/{}".format(
                    self.reconnect_attempts, self.max_reconnect_attempts))

                try:
                    self.wlan.connect(self.ssid, self.password)

                    # Quick check
                    for _ in range(10):
                        if self.wlan.status() == 3:
                            self.connected = True
                            self.reconnect_attempts = 0
                            logger.info("Reconnected successfully")
                            return True
                        time.sleep(1)

                except Exception as e:
                    logger.error("Reconnection failed: {}".format(e))

            elif not self.connected:
                logger.error("Max reconnection attempts reached")

        except Exception as e:
            logger.error("Connection check error: {}".format(e))
            self.connected = False

        return self.connected

    def get_ip(self):
        """Get current IP address"""
        try:
            if self.connected:
                return self.wlan.ifconfig()[0]
            return None
        except Exception:
            return None

    def get_mac(self):
        """Get MAC address"""
        try:
            mac = self.wlan.config('mac')
            return ':'.join('{:02x}'.format(b) for b in mac)
        except Exception:
            return None

    def get_rssi(self):
        """Get WiFi signal strength in dBm"""
        try:
            if self.connected:
                return self.wlan.status('rssi')
            return None
        except Exception:
            return None

    def get_signal_percent(self):
        """Get WiFi signal strength as percentage (0-100)"""
        try:
            rssi = self.get_rssi()
            if rssi is None:
                return None
            # Convert dBm to percentage: -30dBm=100%, -90dBm=0%
            percent = max(0, min(100, int((rssi + 90) * 100 / 60)))
            return percent
        except Exception:
            return None

    def get_channel(self):
        """Get WiFi channel"""
        try:
            if self.connected:
                return self.wlan.config('channel')
            return None
        except Exception:
            return None
