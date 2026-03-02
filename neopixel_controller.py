"""
NeoPixel Controller with Effects
Manages LED strip with various animations on a dedicated core (Core 1)
"""
import time
import math
import gc
import random
import _thread
from machine import Pin
from neopixel import NeoPixel
from logger import Logger

logger = Logger("NeoPixel")


class NeoPixelController:
    # Effect types
    EFFECT_STATIC = "static"
    EFFECT_RAINBOW = "rainbow"
    EFFECT_FADE = "fade"
    EFFECT_CHASE = "chase"
    EFFECT_BREATHING = "breathing"
    EFFECT_TWINKLE = "twinkle"
    EFFECT_SCANNER = "scanner"
    EFFECT_STROBE = "strobe"
    EFFECT_COMET = "comet"

    # Status colors
    COLOR_BOOT = (255, 165, 0)      # Orange - Boot animation
    COLOR_WIFI_CONNECTING = (0, 0, 255)  # Blue - WiFi connecting
    COLOR_ERROR = (255, 0, 0)       # Red - Error
    COLOR_READY = (255, 255, 255)   # White - Ready
    COLOR_OFF = (0, 0, 0)           # Off

    def __init__(self, pin, num_leds, brightness=100):
        """Initialize NeoPixel controller"""
        try:
            self.pin = Pin(pin, Pin.OUT)
            self.num_leds = num_leds
            self.strip = NeoPixel(self.pin, num_leds)
            self.brightness = max(0, min(100, brightness))

            # Current state
            self.current_effect = self.EFFECT_STATIC
            self.current_color = self.COLOR_OFF
            self.is_on = False

            # Animation state
            self.effect_step = 0
            self.last_update = 0
            self.update_interval = 50  # milliseconds

            # Effect parameters
            self.effect_speed = 50  # Default speed
            self.effect_color = self.current_color  # Sync with current_color
            self.step_size = 1  # Steps per animation frame (increases with speed)

            # Core 1 animation state
            self._animation_running = False
            self._needs_refresh = False  # Flag: Core 1 should re-render immediately

            # One-shot animation state
            self._one_shot_active = False
            self._one_shot_steps_remaining = 0
            self._saved_state = None

            logger.info("NeoPixel initialized: {} LEDs on pin {} with brightness {}%".format(num_leds, pin, self.brightness))

        except Exception as e:
            logger.error("Failed to initialize NeoPixel: {}".format(e))
            raise

    def _apply_brightness(self, color):
        """Apply brightness adjustment to color (allocation-free)"""
        factor = self.brightness / 100.0
        return (int(color[0] * factor), int(color[1] * factor), int(color[2] * factor))

    def _set_all(self, color):
        """Set all LEDs to same color"""
        color = self._apply_brightness(color)
        for i in range(self.num_leds):
            self.strip[i] = color

    def _show(self):
        """Update LED strip — only called from Core 1"""
        try:
            self.strip.write()
        except Exception as e:
            logger.error("Failed to update strip: {}".format(e))

    def start_animation_loop(self):
        """Start the animation loop on Core 1"""
        if self._animation_running:
            logger.info("Animation loop already running")
            return

        self._animation_running = True
        _thread.start_new_thread(self._animation_loop, ())
        logger.info("Animation loop started on Core 1")

    def stop_animation_loop(self):
        """Stop the animation loop"""
        self._animation_running = False
        logger.info("Animation loop stopping...")

    def _animation_loop(self):
        """Dedicated animation loop running on Core 1 — sole owner of strip writes."""
        logger.info("Core 1: Animation loop running")
        gc_counter = 0
        try:
            while self._animation_running:
                try:
                    if not self.is_on:
                        # Device turned off — clear strip once, then idle
                        if self._needs_refresh:
                            self._set_all(self.COLOR_OFF)
                            self._show()
                            self._needs_refresh = False
                        time.sleep_ms(50)
                        continue

                    # Process immediate refresh (color/brightness change)
                    if self._needs_refresh and self.current_effect == self.EFFECT_STATIC:
                        self._set_all(self.current_color)
                        self._show()
                        self._needs_refresh = False

                    self.update()
                    # Sleep based on update_interval for precise timing
                    time.sleep_ms(max(1, self.update_interval))

                    # Periodic GC on Core 1 (every ~100 frames)
                    gc_counter += 1
                    if gc_counter >= 100:
                        gc.collect()
                        gc_counter = 0

                except Exception as e:
                    logger.error("Animation loop error: {}".format(e))
                    time.sleep_ms(50)
        except Exception as e:
            logger.error("Animation loop fatal error: {}".format(e))
        finally:
            self._animation_running = False
            logger.info("Core 1: Animation loop stopped")

    def update(self):
        """Update animation frame - called by animation loop on Core 1"""
        if not self.is_on:
            return

        try:
            if self.current_effect == self.EFFECT_STATIC:
                return  # Static needs no per-frame update
            elif self.current_effect == self.EFFECT_RAINBOW:
                self._effect_rainbow()
            elif self.current_effect == self.EFFECT_FADE:
                self._effect_fade()
            elif self.current_effect == self.EFFECT_CHASE:
                self._effect_chase()
            elif self.current_effect == self.EFFECT_BREATHING:
                self._effect_breathing()
            elif self.current_effect == self.EFFECT_TWINKLE:
                self._effect_twinkle()
            elif self.current_effect == self.EFFECT_SCANNER:
                self._effect_scanner()
            elif self.current_effect == self.EFFECT_STROBE:
                self._effect_strobe()
            elif self.current_effect == self.EFFECT_COMET:
                self._effect_comet()

            # Track one-shot progress and restore state when complete
            if self._one_shot_active:
                self._one_shot_steps_remaining -= self.step_size
                if self._one_shot_steps_remaining <= 0:
                    self._restore_state()
        except Exception as e:
            logger.error("Effect update failed: {}".format(e))

    def set_color(self, r, g, b):
        """Set static color (state only — Core 1 renders)"""
        try:
            self.is_on = True
            self.current_effect = self.EFFECT_STATIC
            self.current_color = (r, g, b)
            self.effect_color = (r, g, b)
            self._needs_refresh = True
            logger.info("Color set to RGB({}, {}, {})".format(r, g, b))
        except Exception as e:
            logger.error("Failed to set color: {}".format(e))

    def _calc_speed_params(self, speed):
        """Calculate update_interval and step_size from speed (1-100).

        Uses a quadratic curve for the interval so high speeds get very short
        intervals (= smooth) while low speeds stay perceptibly slow.
        step_size stays at 1 for most of the range to avoid visible jumps;
        only at extreme speeds (>80%) a mild multiplier kicks in.

        Examples:
            1%  → 490ms, step=1  (very slow)
           25%  → 281ms, step=1
           50%  → 125ms, step=1  (moderate, ~8 fps)
           75%  →  31ms, step=1  (fast, ~32 fps)
           80%  →  20ms, step=1  (smooth, ~50 fps)
           90%  →   5ms, step=2  (~400 effective steps/s)
          100%  →   1ms, step=3  (~3000 effective steps/s)
        """
        self.effect_speed = max(1, min(100, speed))
        # Quadratic curve: interval drops steeply at high speeds
        inv = (100 - self.effect_speed) / 100.0
        self.update_interval = max(1, int(500 * inv * inv))
        # Gentle step multiplier only at extreme speeds for extra boost
        if self.effect_speed <= 80:
            self.step_size = 1
        else:
            self.step_size = 1 + (self.effect_speed - 80) // 10  # 81-90→2, 91-100→3

    def set_effect(self, effect_name, speed=50):
        """Start an effect (state only — Core 1 renders)"""
        try:
            self.is_on = True
            self.current_effect = effect_name
            self.effect_step = 0
            self._calc_speed_params(speed)
            self._needs_refresh = True
            logger.info("Effect started: {} (speed: {}, step_size: {})".format(effect_name, speed, self.step_size))
        except Exception as e:
            logger.error("Failed to set effect: {}".format(e))

    def turn_off(self):
        """Turn off all LEDs (state only — Core 1 clears strip)"""
        try:
            self.is_on = False
            self._needs_refresh = True
            logger.info("LEDs turned off")
        except Exception as e:
            logger.error("Failed to turn off: {}".format(e))

    def turn_on(self):
        """Turn on with last color/effect (state only — Core 1 renders)"""
        try:
            self.is_on = True
            self._needs_refresh = True
            logger.info("LEDs turned on")
        except Exception as e:
            logger.error("Failed to turn on: {}".format(e))

    def set_brightness(self, brightness):
        """Set brightness (0-100, state only — Core 1 renders)"""
        try:
            self.brightness = max(0, min(100, brightness))
            self._needs_refresh = True
            logger.info("Brightness set to {}%".format(self.brightness))
        except Exception as e:
            logger.error("Failed to set brightness: {}".format(e))

    def set_speed(self, speed):
        """Set animation speed (1-100) without changing the current effect"""
        try:
            self._calc_speed_params(speed)
            logger.info("Animation speed set to {}% (step_size: {})".format(self.effect_speed, self.step_size))
        except Exception as e:
            logger.error("Failed to set speed: {}".format(e))

    # One-shot animation support

    def _get_one_shot_cycle_length(self, effect):
        """Get the number of steps for one complete cycle of an effect."""
        if effect == self.EFFECT_RAINBOW:
            return 256
        elif effect == self.EFFECT_FADE:
            return 256
        elif effect == self.EFFECT_CHASE:
            return self.num_leds
        elif effect == self.EFFECT_BREATHING:
            return int(2 * 3.14159 * 20)  # One full sine cycle
        elif effect == self.EFFECT_TWINKLE:
            return max(30, self.num_leds * 2)
        elif effect == self.EFFECT_SCANNER:
            return (self.num_leds - 1) * 2
        elif effect == self.EFFECT_STROBE:
            return 20  # 10 flashes
        elif effect == self.EFFECT_COMET:
            return self.num_leds * 2
        return 100  # Fallback

    def play_once(self, effect, speed=50, color=None):
        """Play an effect once, then restore the previous state."""
        try:
            # Only save state if not already in one-shot mode
            if not self._one_shot_active:
                self._saved_state = {
                    'is_on': self.is_on,
                    'effect': self.current_effect,
                    'color': self.current_color,
                    'effect_color': self.effect_color,
                    'brightness': self.brightness,
                    'effect_speed': self.effect_speed,
                    'update_interval': self.update_interval,
                    'step_size': self.step_size,
                }

            # Configure one-shot effect
            self.is_on = True
            self.current_effect = effect
            self.effect_step = 0
            if color:
                self.effect_color = color
                self.current_color = color
            self._calc_speed_params(speed)
            self._one_shot_steps_remaining = self._get_one_shot_cycle_length(effect)
            self._one_shot_active = True
            self._needs_refresh = True
            logger.info("One-shot started: {} ({} steps)".format(effect, self._one_shot_steps_remaining))
        except Exception as e:
            logger.error("Failed to start one-shot: {}".format(e))

    def _restore_state(self):
        """Restore state after one-shot animation completes."""
        try:
            if self._saved_state:
                self.is_on = self._saved_state['is_on']
                self.current_effect = self._saved_state['effect']
                self.current_color = self._saved_state['color']
                self.effect_color = self._saved_state['effect_color']
                self.brightness = self._saved_state['brightness']
                self.effect_speed = self._saved_state['effect_speed']
                self.update_interval = self._saved_state['update_interval']
                self.step_size = self._saved_state['step_size']
                self._saved_state = None

            self._one_shot_active = False
            self._one_shot_steps_remaining = 0
            self._needs_refresh = True
            logger.info("One-shot complete, state restored")
        except Exception as e:
            logger.error("Failed to restore state: {}".format(e))

    # Effect implementations

    def _effect_static(self):
        """Static color effect"""
        # Already set, no animation needed
        pass

    def _effect_rainbow(self):
        """Rainbow cycle effect"""
        br = self.brightness / 100.0
        for i in range(self.num_leds):
            hue = (i * 256 // self.num_leds + self.effect_step) % 256
            r, g, b = self._hsv_to_rgb(hue / 256.0, 1.0, 1.0)
            self.strip[i] = (int(r * br), int(g * br), int(b * br))
        self._show()
        self.effect_step = (self.effect_step + self.step_size) % 256

    def _effect_fade(self):
        """Color fade effect"""
        hue = self.effect_step / 256.0
        color = self._hsv_to_rgb(hue, 1.0, 1.0)
        self._set_all(color)
        self._show()
        self.effect_step = (self.effect_step + self.step_size) % 256

    def _effect_chase(self):
        """Chase/running light effect"""
        self._set_all(self.COLOR_OFF)
        pos = self.effect_step % self.num_leds
        for i in range(5):  # 5 LEDs in the chase
            led_pos = (pos + i) % self.num_leds
            self.strip[led_pos] = self._apply_brightness(self.effect_color)
        self._show()
        self.effect_step += self.step_size

    def _effect_breathing(self):
        """Breathing effect (pulsing brightness)"""
        breath = (math.sin(self.effect_step / 20.0) + 1) / 2  # 0-1
        temp_brightness = self.brightness
        self.brightness = int(breath * temp_brightness)
        self._set_all(self.effect_color)
        self._show()
        self.brightness = temp_brightness
        self.effect_step += self.step_size

    def _effect_twinkle(self):
        """Twinkle/sparkle effect"""
        if self.effect_step % 3 == 0:  # Update some LEDs
            color_on = self._apply_brightness(self.effect_color)
            for _ in range(self.num_leds // 10):  # 10% of LEDs
                led = random.randint(0, self.num_leds - 1)
                if random.random() > 0.5:
                    self.strip[led] = color_on
                else:
                    self.strip[led] = self.COLOR_OFF
            self._show()
        self.effect_step += self.step_size

    def _effect_scanner(self):
        """Scanner effect (Knight Rider style)"""
        self._set_all(self.COLOR_OFF)

        cycle_length = (self.num_leds - 1) * 2
        pos = self.effect_step % cycle_length

        if pos < self.num_leds:
            actual_pos = pos
        else:
            actual_pos = cycle_length - pos

        # Draw scanner with tail (avoid tuple allocation in loop)
        er, eg, eb = self.effect_color
        br = self.brightness / 100.0
        for i in range(5):
            led_pos = actual_pos - i
            if 0 <= led_pos < self.num_leds:
                f = (5 - i) / 5.0 * br
                self.strip[led_pos] = (int(er * f), int(eg * f), int(eb * f))

        self._show()
        self.effect_step += self.step_size

    def _effect_strobe(self):
        """Strobe effect"""
        if self.effect_step % 2 == 0:
            self._set_all(self.effect_color)
        else:
            self._set_all(self.COLOR_OFF)
        self._show()
        self.effect_step += self.step_size

    def _effect_comet(self):
        """Comet effect — multiple comets with fading tails racing across the strip."""
        # Dim the entire strip to create fading tails
        decay = 0.6  # Tail fade factor (lower = shorter tail)
        for i in range(self.num_leds):
            r, g, b = self.strip[i]
            self.strip[i] = (int(r * decay), int(g * decay), int(b * decay))

        # Comet parameters
        num_comets = max(1, self.num_leds // 30)  # ~1 comet per 30 LEDs
        spacing = self.num_leds // num_comets
        er, eg, eb = self.effect_color
        br = self.brightness / 100.0
        comet_len = 5  # Bright head length

        for c in range(num_comets):
            # Each comet is offset by spacing; they all advance with effect_step
            head = (self.effect_step + c * spacing) % self.num_leds
            for j in range(comet_len):
                pos = (head - j) % self.num_leds
                f = (comet_len - j) / comet_len * br
                pr, pg, pb = int(er * f), int(eg * f), int(eb * f)
                # Keep the brighter of existing vs new value (comets can overlap tails)
                old_r, old_g, old_b = self.strip[pos]
                self.strip[pos] = (max(old_r, pr), max(old_g, pg), max(old_b, pb))

        self._show()
        self.effect_step += self.step_size

    def _hsv_to_rgb(self, h, s, v):
        """Convert HSV to RGB color"""
        if s == 0.0:
            return (int(v * 255), int(v * 255), int(v * 255))

        i = int(h * 6.0)
        f = (h * 6.0) - i
        p = v * (1.0 - s)
        q = v * (1.0 - s * f)
        t = v * (1.0 - s * (1.0 - f))
        i = i % 6

        if i == 0:
            r, g, b = v, t, p
        elif i == 1:
            r, g, b = q, v, p
        elif i == 2:
            r, g, b = p, v, t
        elif i == 3:
            r, g, b = p, q, v
        elif i == 4:
            r, g, b = t, p, v
        else:
            r, g, b = v, p, q

        return (int(r * 255), int(g * 255), int(b * 255))

    def show_status(self, status):
        """Show status color (blocking animation for boot/error states)"""
        try:
            if status == "boot":
                # Boot animation - quick rainbow sweep
                for step in range(256):
                    for i in range(self.num_leds):
                        hue = (i * 256 // self.num_leds + step) % 256
                        color = self._hsv_to_rgb(hue / 256.0, 1.0, 1.0)
                        self.strip[i] = self._apply_brightness(color)
                    self._show()
                    time.sleep(0.001)

            elif status == "wifi_connecting":
                # WiFi connecting - pulsing blue
                self.is_on = False
                for _ in range(3):
                    for brightness in range(0, 100, 5):
                        temp_brightness = self.brightness
                        self.brightness = brightness
                        self._set_all(self.COLOR_WIFI_CONNECTING)
                        self._show()
                        self.brightness = temp_brightness
                        time.sleep(0.02)
                    for brightness in range(100, 0, -5):
                        temp_brightness = self.brightness
                        self.brightness = brightness
                        self._set_all(self.COLOR_WIFI_CONNECTING)
                        self._show()
                        self.brightness = temp_brightness
                        time.sleep(0.02)

            elif status == "error":
                # Error - solid red
                self.is_on = False
                self._set_all(self.COLOR_ERROR)
                self._show()

            elif status == "ready":
                # Ready - solid white
                self.is_on = True
                self.current_effect = self.EFFECT_STATIC
                self.current_color = self.COLOR_READY
                self.effect_color = self.COLOR_READY
                self._set_all(self.COLOR_READY)
                self._show()

        except Exception as e:
            logger.error("Failed to show status: {}".format(e))

    def get_state(self):
        """Get current state for reporting to Home Assistant"""
        # During one-shot, report the saved (persistent) state
        if self._one_shot_active and self._saved_state:
            return {
                "is_on": self._saved_state['is_on'],
                "effect": self._saved_state['effect'],
                "brightness": self._saved_state['brightness'],
                "speed": self._saved_state['effect_speed'],
                "color": {
                    "r": self._saved_state['color'][0],
                    "g": self._saved_state['color'][1],
                    "b": self._saved_state['color'][2]
                },
                "one_shot_active": True,
                "one_shot_effect": self.current_effect,
            }
        return {
            "is_on": self.is_on,
            "effect": self.current_effect,
            "brightness": self.brightness,
            "speed": self.effect_speed,
            "color": {
                "r": self.current_color[0],
                "g": self.current_color[1],
                "b": self.current_color[2]
            },
            "one_shot_active": False,
        }

    def get_estimated_current_ma(self):
        """Estimate current draw in mA based on LED state.

        WS2812B: ~60mA per LED at full white (20mA per channel).
        Scaled by brightness and actual color values.
        """
        if not self.is_on:
            return 0
        try:
            r, g, b = self.current_color
            brightness_factor = self.brightness / 100.0
            # Average color intensity (0-1)
            color_factor = (r + g + b) / (3 * 255.0)
            # Each LED: up to 60mA
            per_led_ma = 60.0 * brightness_factor * color_factor
            return int(per_led_ma * self.num_leds)
        except Exception:
            return 0
