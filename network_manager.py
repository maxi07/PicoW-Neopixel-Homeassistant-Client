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
        
    def connect(self, neopixel_controller=None):
        """Connect to WiFi with visual feedback"""
        try:
            logger.info("Connecting to WiFi: {}".format(self.ssid))
            
            # Activate interface
            self.wlan.active(True)
            
            # Show connecting animation if available
            if neopixel_controller:
                neopixel_controller.show_status("wifi_connecting")
            
            # Connect
            self.wlan.connect(self.ssid, self.password)
            
            # Wait for connection
            max_wait = 30
            while max_wait > 0:
                if self.wlan.status() < 0 or self.wlan.status() >= 3:
                    break
                max_wait -= 1
                time.sleep(1)
            
            # Check if connected
            if self.wlan.status() != 3:
                logger.error("Failed to connect to WiFi")
                self.connected = False
                if neopixel_controller:
                    neopixel_controller.show_status("error")
                return False
            
            # Connection successful
            status = self.wlan.ifconfig()
            logger.info("Connected! IP: {}".format(status[0]))
            self.connected = True
            self.reconnect_attempts = 0
            
            if neopixel_controller:
                neopixel_controller.show_status("ready")
            
            return True
            
        except Exception as e:
            logger.error("WiFi connection error: {}".format(e))
            self.connected = False
            if neopixel_controller:
                neopixel_controller.show_status("error")
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
        except:
            return None
    
    def get_mac(self):
        """Get MAC address"""
        try:
            mac = self.wlan.config('mac')
            return ':'.join('{:02x}'.format(b) for b in mac)
        except:
            return None
