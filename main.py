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

# Initialize logger
logger = Logger("Main")


def main():
    """Main application entry point"""
    logger.info("="*50)
    logger.info("PicoW NeoPixel Controller Starting")
    logger.info("Version: 1.0")
    logger.info("="*50)

    # Initialize components
    neopixel = None
    network_mgr = None
    api_server = None

    try:
        # Load configuration
        logger.info("Loading configuration...")
        config = Config()

        # Initialize NeoPixel controller
        logger.info("Initializing NeoPixel controller...")
        pin = int(config.get("neopixel", "pin") or 28)
        num_leds = int(config.get("neopixel", "num_leds") or 64)
        brightness = int(config.get("neopixel", "brightness") or 100)

        neopixel = NeoPixelController(pin, num_leds, brightness)

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

        logger.info("="*50)
        logger.info("System ready! Waiting for commands...")
        logger.info("="*50)

        # Main event loop (Core 0: API + Network)
        loop_counter = 0
        last_gc = time.ticks_ms()

        while True:
            try:
                # Handle API requests (Core 0)
                api_server.handle_requests()

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

        try:
            if neopixel:
                neopixel.stop_animation_loop()
        except Exception as e:
            logger.error("Error stopping animation loop: {}".format(e))

        try:
            if api_server:
                api_server.stop()
        except Exception as e:
            logger.error("Error stopping API server: {}".format(e))

        try:
            if network_mgr:
                network_mgr.disconnect()
        except Exception as e:
            logger.error("Error disconnecting network: {}".format(e))

        # Only turn off LEDs if not already showing an error status
        try:
            if neopixel and not neopixel.current_color == neopixel.COLOR_ERROR:
                neopixel.turn_off()
            else:
                logger.info("Keeping error status LEDs on")
        except Exception as e:
            logger.error("Error turning off LEDs: {}".format(e))

        logger.info("Shutdown complete")


# Entry point
if __name__ == "__main__":
    main()
