"""
Quick test script — loops a NeoPixel effect for 10 seconds, then turns off.
Upload to the Pico W and run manually.

Usage (in REPL):
    import test_effect_loop
"""
import time
from config_loader import Config
from neopixel_controller import NeoPixelController

# ── Settings ────────────────────────────────────────────
EFFECT = NeoPixelController.EFFECT_CHASE_MIDDLE_OUT    # Effect to test
BRIGHTNESS = 50                               # 0-100
SPEED = 50                                    # 1-100
COLOR = (255, 0, 0)                           # RGB color (used by chase, breathing, scanner, twinkle, strobe)
DURATION_SECONDS = 10                         # How long to loop the animation
# ────────────────────────────────────────────────────────

# Load config for pin / num_leds
config = Config()
pin = int(config.get("neopixel", "pin") or 28)
num_leds = int(config.get("neopixel", "num_leds") or 121)

# Init controller
np = NeoPixelController(pin, num_leds, brightness=BRIGHTNESS)

# Start the animation loop on Core 1
np.start_animation_loop()

# Give Core 1 a moment to spin up
time.sleep_ms(100)

# Set the color and effect (loops continuously)
np.set_color(COLOR[0], COLOR[1], COLOR[2])
np.set_effect(EFFECT, speed=SPEED)

print("Effect '{}' looping for {} seconds (brightness={}, speed={})...".format(
    EFFECT, DURATION_SECONDS, BRIGHTNESS, SPEED))

# Wait for the specified duration
time.sleep(DURATION_SECONDS)

print("Done. Turning off.")

# Clean up
np.turn_off()
time.sleep_ms(100)   # let Core 1 clear the strip
np.stop_animation_loop()
time.sleep_ms(100)
