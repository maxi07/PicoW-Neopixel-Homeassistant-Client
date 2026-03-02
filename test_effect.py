"""
Quick test script — runs a single NeoPixel effect once, then turns off.
Upload to the Pico W and run manually.

Usage (in REPL):
    import test_effect
"""
import time
from config_loader import Config
from neopixel_controller import NeoPixelController

# ── Settings ────────────────────────────────────────────
EFFECT = NeoPixelController.EFFECT_CHASE_MIDDLE_OUT   # Effect to test
BRIGHTNESS = 50                               # 0-100
SPEED = 80                                    # 1-100
COLOR = (255, 0, 0)                           # RGB color (used by chase, breathing, scanner, twinkle, strobe)
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

# Play the effect once
np.play_once(EFFECT, speed=SPEED, color=COLOR)

print("Effect '{}' playing once (brightness={}, speed={})...".format(EFFECT, BRIGHTNESS, SPEED))

# Wait until the one-shot finishes
while np._one_shot_active:
    time.sleep_ms(50)

print("Done. Turning off.")

# Clean up
np.turn_off()
time.sleep_ms(100)   # let Core 1 clear the strip
np.stop_animation_loop()
time.sleep_ms(100)
