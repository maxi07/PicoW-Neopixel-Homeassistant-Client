"""
Main Application for PicoW NeoPixel Controller
Coordinates all components in a non-blocking event loop
"""
import time
import gc
from logger import Logger
from config_loader import Config
from neopixel_controller import NeoPixelController
from network_manager import NetworkManager
from api_server import APIServer
from mdns_service import MDNSResponder
from version import VERSION

# Initialize logger
logger = Logger("Main")

_DEFAULT_MDNS_HOSTNAME = "picow-neopixel"


def _sanitize_mdns_hostname(device_id):
    """Convert a device ID to a valid DNS hostname label (RFC 1123).

    Replaces underscores with hyphens, strips any character that is not
    [a-z0-9-], strips leading/trailing hyphens, and trims to 63 characters.
    The original device ID is preserved unchanged in the TXT record so
    discovery still maps back to the configured device.
    """
    hostname = device_id.replace("_", "-").lower()
    hostname = "".join(c for c in hostname if c in "abcdefghijklmnopqrstuvwxyz0123456789-")
    hostname = hostname.strip("-")
    return hostname[:63] or _DEFAULT_MDNS_HOSTNAME


def main():
    """Main application entry point"""
    logger.info("="*50)
    logger.info("PicoW NeoPixel Controller Starting")
    logger.info("Version: {}".format(VERSION))
    logger.info("="*50)

    # Initialize components
    neopixel = None
    network_mgr = None
    api_server = None
    mdns = None

    try:
        # Load configuration
        logger.info("Loading configuration...")
        config = Config()

        # Initialize NeoPixel controller
        logger.info("Initializing NeoPixel controller...")
        pin = int(config.get("neopixel", "pin") or 28)
        num_leds = int(config.get("neopixel", "num_leds") or 64)
        brightness = int(config.get("neopixel", "brightness") or 100)
        rgbw = config.get("neopixel", "rgbw", False) is True

        neopixel = NeoPixelController(pin, num_leds, brightness, rgbw=rgbw)

        # Show boot animation
        logger.info("Running boot animation...")
        # neopixel.show_status("boot")

        # Initialize network manager
        logger.info("Initializing network...")
        ssid = config.get("wifi", "ssid", "")
        password = config.get("wifi", "password", "")

        if not ssid:
            logger.error("WiFi SSID not configured!")
            neopixel.show_status("error")
            return

        network_mgr = NetworkManager(ssid, password)

        # Start animation loop on Core 1 and show WiFi connecting animation
        neopixel.set_effect(neopixel.EFFECT_BREATHING, speed=50)
        neopixel.set_color_only(neopixel.COLOR_WIFI_CONNECTING)
        neopixel.start_animation_loop()

        # Connect to WiFi (breathing blue runs on Core 1)
        if not network_mgr.connect():
            logger.error("Failed to connect to WiFi")
            neopixel.stop_animation_loop()
            time.sleep(0.5)
            neopixel.show_status("error")
            time.sleep(10)  # Show error for 10 seconds
            return

        # WiFi connected - show ready
        neopixel.show_status("ready")

        # Get device info
        device_info = {
            "name": config.get("device", "name", "PicoW NeoPixel"),
            "id": config.get("device", "id", "picow_neopixel_001"),
            "ip": network_mgr.get_ip(),
            "mac": network_mgr.get_mac()
        }

        logger.info("Device: {} ({})".format(device_info["name"], device_info["id"]))
        logger.info("IP Address: {}".format(device_info["ip"]))
        logger.info("MAC Address: {}".format(device_info["mac"]))

        # Initialize API server
        logger.info("Starting API server...")
        api_port = int(config.get("api", "port") or 80)
        api_server = APIServer(neopixel, device_info, api_port, network_mgr)

        if not api_server.start():
            logger.error("Failed to start API server")
            neopixel.show_status("error")
            return

        # Advertise the device over mDNS / DNS-SD so the Home Assistant
        # integration can discover it without scanning the LAN.
        try:
            mdns_hostname = _sanitize_mdns_hostname(device_info["id"])
            mdns = MDNSResponder(
                hostname=mdns_hostname,
                instance_name=device_info["name"],
                service_type="_picow-neopixel._tcp.local.",
                port=api_port,
                ip=device_info["ip"],
                txt_records={
                    "id": device_info["id"],
                    "name": device_info["name"],
                    "version": VERSION,
                    "path": "/info",
                },
            )
            if not mdns.start():
                mdns = None
        except Exception as e:
            logger.error("Failed to initialize mDNS responder: {}".format(e))
            mdns = None

        logger.info("="*50)
        logger.info("System ready! Waiting for commands...")
        logger.info("="*50)

        # Main event loop (Core 0: API + Network)
        loop_counter = 0
        last_gc = time.ticks_ms()
        last_mdns_ip = device_info["ip"]

        while True:
            try:
                # Handle API requests (Core 0)
                api_server.handle_requests()

                # Service the mDNS responder (non-blocking)
                if mdns:
                    mdns.poll()
                    # Keep mDNS A record in sync if DHCP assigned a new IP
                    current_ip = network_mgr.get_ip()
                    if current_ip and current_ip != last_mdns_ip:
                        logger.info("IP changed {} -> {}, updating mDNS".format(
                            last_mdns_ip, current_ip))
                        mdns.update_ip(current_ip)
                        last_mdns_ip = current_ip

                # Check network connection (periodic)
                network_mgr.check_connection()

                # Periodic garbage collection (every 10 seconds)
                current_time = time.ticks_ms()
                if time.ticks_diff(current_time, last_gc) > 10000:
                    gc.collect()
                    last_gc = current_time
                    if loop_counter % 100 == 0:
                        logger.debug("Free memory: {} bytes".format(gc.mem_free()))

                loop_counter += 1

                # Yield CPU — animations run independently on Core 1
                time.sleep(0.01)

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                break
            except Exception as e:
                logger.error("Main loop error: {}".format(e))
                time.sleep(1)  # Prevent rapid error loops

    except Exception as e:
        logger.error("Fatal error: {}".format(e))
        if neopixel:
            try:
                neopixel.show_status("error")
            except Exception:
                pass

    finally:
        # Cleanup
        logger.info("Shutting down...")

        # Turn off LEDs first while animation loop is still running
        try:
            if neopixel and not neopixel.current_color == neopixel.COLOR_ERROR:
                neopixel.turn_off()
                time.sleep_ms(100)  # Let Core 1 clear the strip
            else:
                logger.info("Keeping error status LEDs on")
        except Exception as e:
            logger.error("Error turning off LEDs: {}".format(e))

        try:
            if neopixel:
                neopixel.stop_animation_loop()
                time.sleep_ms(100)
        except Exception as e:
            logger.error("Error stopping animation loop: {}".format(e))

        try:
            if api_server:
                api_server.stop()
        except Exception as e:
            logger.error("Error stopping API server: {}".format(e))

        try:
            if mdns:
                mdns.stop()
        except Exception as e:
            logger.error("Error stopping mDNS responder: {}".format(e))

        try:
            if network_mgr:
                network_mgr.disconnect()
        except Exception as e:
            logger.error("Error disconnecting network: {}".format(e))

        logger.info("Shutdown complete")


# Entry point
if __name__ == "__main__":
    main()
