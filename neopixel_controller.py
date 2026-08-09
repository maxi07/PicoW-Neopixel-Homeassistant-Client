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


class _RGBWStrip:
    """Expose an RGB interface while driving a four-channel RGBW strip."""

    def __init__(self, strip):
        self._strip = strip

    def __getitem__(self, index):
        red, green, blue, white = self._strip[index]
        return (
            min(255, red + white),
            min(255, green + white),
            min(255, blue + white),
        )

    def __setitem__(self, index, color):
        red = color[0]
        green = color[1]
        blue = color[2]
        white = min(red, green, blue)
        self._strip[index] = (
            red - white,
            green - white,
            blue - white,
            white,
        )

    def write(self):
        self._strip.write()


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
    EFFECT_CHASE_MIDDLE_OUT = "chase_middle_out"
    EFFECT_CHASE_OUTSIDE_IN = "chase_outside_in"
    EFFECT_FILL_MIDDLE_OUT = "fill_middle_out"      # Turn on from center outward
    EFFECT_FILL_OUTSIDE_IN = "fill_outside_in"      # Turn on from edges inward
    EFFECT_UNFILL_MIDDLE_OUT = "unfill_middle_out"  # Turn off from center outward
    EFFECT_UNFILL_OUTSIDE_IN = "unfill_outside_in"  # Turn off from edges inward
    EFFECT_TRANSITION_MIDDLE_OUT = "transition_middle_out"  # Color transition from center
    EFFECT_TRANSITION_OUTSIDE_IN = "transition_outside_in"  # Color transition from edges
    EFFECT_TRANSITION_FADE = "transition_fade"              # Smooth color crossfade
    EFFECT_PROGRESS_BAR = "progress_bar"                    # Progress bar visualization

    # Status colors
    COLOR_BOOT = (255, 165, 0)      # Orange - Boot animation
    COLOR_WIFI_CONNECTING = (0, 0, 255)  # Blue - WiFi connecting
    COLOR_ERROR = (255, 0, 0)       # Red - Error
    COLOR_READY = (255, 255, 255)   # White - Ready
    COLOR_OFF = (0, 0, 0)           # Off

    def __init__(self, pin, num_leds, brightness=100, rgbw=False):
        """Initialize NeoPixel controller"""
        try:
            self.pin = Pin(pin, Pin.OUT)
            self.num_leds = num_leds
            self.rgbw = bool(rgbw)
            raw_strip = NeoPixel(self.pin, num_leds, bpp=4 if self.rgbw else 3)
            self.strip = _RGBWStrip(raw_strip) if self.rgbw else raw_strip
            self.brightness = max(0, min(100, brightness))

            # Current state
            self.current_effect = self.EFFECT_STATIC
            self.current_color = self.COLOR_OFF
            self.is_on = False

            # Animation state
            self.effect_step = 0
            self._effect_pos_f = 0.0  # Float position for sub-pixel smooth rendering
            self.last_update = 0
            self.update_interval = 50  # milliseconds

            # Progress bar state
            self.progress_value = 0  # 0-100 percentage (target)
            self._progress_display = 0.0  # Currently displayed progress (animated float)
            self._progress_snap = False  # Flag: Core 1 should snap display to target

            # Effect parameters
            self.effect_speed = 50  # Default speed
            self.effect_color = self.current_color  # Sync with current_color
            self._speed_inc = 0.9   # Float increment per render frame
            self.step_size = 1      # Legacy (always 1 now)
            self.update_interval = 16  # Legacy

            # Core 1 animation state
            self._animation_running = False
            self._needs_refresh = False  # Flag: Core 1 should re-render immediately

            # One-shot animation state
            self._one_shot_active = False
            self._one_shot_steps_remaining = 0
            self._saved_state = None

            # Transition effect state
            self._transition_from_color = self.COLOR_OFF

            logger.info("NeoPixel initialized: {} {} LEDs on pin {} with brightness {}%".format(
                num_leds, "RGBW" if self.rgbw else "RGB", pin, self.brightness))

        except Exception as e:
            logger.error("Failed to initialize NeoPixel: {}".format(e))
            raise

    def _apply_brightness(self, color):
        """Apply brightness adjustment to color (allocation-free)"""
        factor = self.brightness / 100.0
        return (int(color[0] * factor), int(color[1] * factor), int(color[2] * factor))

    def _fade_pixel(self, i, tr, tg, tb, rate):
        """Smoothly fade LED i toward target color (tr, tg, tb).

        rate: fraction (0.0-1.0) to move toward target per frame.
        Guarantees convergence by ensuring a minimum step of 1.
        """
        cr, cg, cb = self.strip[i]
        dr = tr - cr
        dg = tg - cg
        db = tb - cb
        sr = int(dr * rate)
        sg = int(dg * rate)
        sb = int(db * rate)
        if dr and not sr:
            sr = 1 if dr > 0 else -1
        if dg and not sg:
            sg = 1 if dg > 0 else -1
        if db and not sb:
            sb = 1 if db > 0 else -1
        self.strip[i] = (cr + sr, cg + sg, cb + sb)

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

    # Fixed render interval — always ~60 FPS regardless of effect speed
    RENDER_MS = 16

    def _animation_loop(self):
        """Dedicated animation loop running on Core 1 — sole owner of strip writes.

        Always renders at ~60 FPS (RENDER_MS).  _effect_pos_f advances by a
        fractional amount every frame based on speed, giving sub-pixel smooth
        movement.  effect_step (int) is derived for one-shot tracking.
        """
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

                    # --- Advance float position every frame ---
                    old_step = self.effect_step
                    self._effect_pos_f += self._speed_inc
                    self.effect_step = int(self._effect_pos_f)

                    # One-shot tracking (count integer steps advanced)
                    steps_advanced = self.effect_step - old_step
                    if self._one_shot_active and steps_advanced > 0:
                        self._one_shot_steps_remaining -= steps_advanced
                        if self._one_shot_steps_remaining <= 0:
                            self._restore_state()

                    # --- Render every frame at ~60 FPS ---
                    self._render_effect()

                    time.sleep_ms(self.RENDER_MS)

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

    def _render_effect(self):
        """Render current effect frame — called at ~60 FPS by animation loop.

        effect_step is advanced by the animation loop, NOT by effects.
        Effects only read effect_step and render accordingly.
        """
        if not self.is_on:
            return

        try:
            if self.current_effect == self.EFFECT_STATIC:
                self._set_all(self.current_color)
                self._show()
            elif self.current_effect == self.EFFECT_PROGRESS_BAR:
                self._effect_progress_bar()
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
            elif self.current_effect == self.EFFECT_CHASE_MIDDLE_OUT:
                self._effect_chase_middle_out()
            elif self.current_effect == self.EFFECT_CHASE_OUTSIDE_IN:
                self._effect_chase_outside_in()
            elif self.current_effect == self.EFFECT_FILL_MIDDLE_OUT:
                self._effect_fill_middle_out()
            elif self.current_effect == self.EFFECT_FILL_OUTSIDE_IN:
                self._effect_fill_outside_in()
            elif self.current_effect == self.EFFECT_UNFILL_MIDDLE_OUT:
                self._effect_unfill_middle_out()
            elif self.current_effect == self.EFFECT_UNFILL_OUTSIDE_IN:
                self._effect_unfill_outside_in()
            elif self.current_effect == self.EFFECT_TRANSITION_MIDDLE_OUT:
                self._effect_transition_middle_out()
            elif self.current_effect == self.EFFECT_TRANSITION_OUTSIDE_IN:
                self._effect_transition_outside_in()
            elif self.current_effect == self.EFFECT_TRANSITION_FADE:
                self._effect_transition_fade()
        except Exception as e:
            logger.error("Effect render failed: {}".format(e))

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

    def set_color_only(self, color):
        """Set color without changing effect (for status animations)"""
        self.current_color = color
        self.effect_color = color
        self._needs_refresh = True

    def _calc_speed_params(self, speed):
        """Calculate _speed_inc from speed (1-100).

        Produces a float increment per 16ms render frame.
        Quadratic curve: slow crawl at low speed, fast but still
        coherent at high speed (max ~10.5 LEDs/frame).

        Examples:
            1%  → 0.02 inc  (~1.2 LEDs/s, very slow)
           25%  → 0.68 inc  (~42 LEDs/s)
           50%  → 2.65 inc  (~165 LEDs/s)
           75%  → 5.9  inc  (~370 LEDs/s)
          100%  → 10.5 inc  (~656 LEDs/s)
        """
        self.effect_speed = max(1, min(100, speed))
        sf = (self.effect_speed / 100.0) ** 2
        self._speed_inc = 0.02 + sf * 10.5
        # Legacy fields (kept for state save/restore)
        self.step_size = 1
        self.update_interval = max(1, int(self.RENDER_MS / self._speed_inc))

    def set_effect(self, effect_name, speed=50):
        """Start an effect (state only — Core 1 renders)"""
        try:
            self.is_on = True
            self.current_effect = effect_name
            self.effect_step = 0
            self._effect_pos_f = 0.0
            self._calc_speed_params(speed)

            # When switching to progress bar, snap display to current value
            if effect_name == self.EFFECT_PROGRESS_BAR:
                self._progress_display = float(self.progress_value)
                self._progress_snap = True

            self._needs_refresh = True
            logger.info("Effect started: {} (speed: {}, inc: {})".format(effect_name, speed, self._speed_inc))
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
            logger.info("Animation speed set to {}% (inc: {})".format(self.effect_speed, self._speed_inc))
        except Exception as e:
            logger.error("Failed to set speed: {}".format(e))

    # One-shot animation support

    def _get_effect_tail_length(self, effect):
        """Get the tail/head length for effects that have one.

        This is the ENTRY padding - how much the head starts outside the strip.
        """
        if effect == self.EFFECT_CHASE:
            return 8  # chase_len - head enters from outside
        elif effect == self.EFFECT_SCANNER:
            return 8  # head_len
        elif effect == self.EFFECT_COMET:
            return 15  # comet_len + tail fade
        elif effect == self.EFFECT_CHASE_OUTSIDE_IN:
            return 8  # Head starts outside, enters the strip
        elif effect == self.EFFECT_CHASE_MIDDLE_OUT:
            return 0  # Head starts IN the center, no entry padding needed
        return 0  # No padding needed for other effects

    def _get_one_shot_cycle_length(self, effect):
        """Get the number of steps for one complete cycle of an effect.

        Includes padding for tail effects so animation fully enters and exits.
        """
        padding = self._get_effect_tail_length(effect)

        if effect == self.EFFECT_RAINBOW:
            return 256
        elif effect == self.EFFECT_FADE:
            return 256
        elif effect == self.EFFECT_CHASE:
            # Full strip + 2x padding (enter + exit)
            return self.num_leds + 2 * padding
        elif effect == self.EFFECT_BREATHING:
            return int(2 * 3.14159 * 20)  # One full sine cycle
        elif effect == self.EFFECT_TWINKLE:
            return max(30, self.num_leds * 2)
        elif effect == self.EFFECT_SCANNER:
            # Full cycle + 2x padding (enter + exit)
            return (self.num_leds - 1) * 2 + 2 * padding
        elif effect == self.EFFECT_STROBE:
            return 20  # 10 flashes
        elif effect == self.EFFECT_COMET:
            # Full strip + 2x padding (enter + exit)
            return self.num_leds * 2 + 2 * padding
        elif effect == self.EFFECT_CHASE_MIDDLE_OUT:
            # Head starts at center, moves to edge, tail exits completely
            # Distance: half strip + chase_len for tail to exit
            return (self.num_leds + 1) // 2 + 8
        elif effect == self.EFFECT_CHASE_OUTSIDE_IN:
            # Head starts outside (-chase_len), moves to center, tail finishes at center
            # Total distance: chase_len (entry) + half (to center) + chase_len (tail to finish)
            return (self.num_leds + 1) // 2 + 2 * 8
        elif effect == self.EFFECT_FILL_MIDDLE_OUT:
            # Fill from center to edges - half strip distance
            return (self.num_leds + 1) // 2
        elif effect == self.EFFECT_FILL_OUTSIDE_IN:
            # Fill from edges to center - half strip distance
            return (self.num_leds + 1) // 2
        elif effect == self.EFFECT_UNFILL_MIDDLE_OUT:
            # Unfill from center to edges - half strip distance
            return (self.num_leds + 1) // 2
        elif effect == self.EFFECT_UNFILL_OUTSIDE_IN:
            # Unfill from edges to center - half strip distance
            return (self.num_leds + 1) // 2
        elif effect == self.EFFECT_TRANSITION_MIDDLE_OUT:
            # Transition from center to edges - half strip distance
            return (self.num_leds + 1) // 2
        elif effect == self.EFFECT_TRANSITION_OUTSIDE_IN:
            # Transition from edges to center - half strip distance
            return (self.num_leds + 1) // 2
        elif effect == self.EFFECT_TRANSITION_FADE:
            # Smooth crossfade — 64 steps for a nice blend
            return 64
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
                    '_speed_inc': self._speed_inc,
                    'update_interval': self.update_interval,
                    'step_size': self.step_size,
                }

            # For transition effects, save the current color as "from" color
            if effect in (self.EFFECT_TRANSITION_MIDDLE_OUT, self.EFFECT_TRANSITION_OUTSIDE_IN, self.EFFECT_TRANSITION_FADE):
                self._transition_from_color = self.current_color

            # Configure one-shot effect
            self.is_on = True
            self.current_effect = effect

            # Start with negative offset for effects with tails
            # This makes the animation "enter" from outside the visible strip
            padding = self._get_effect_tail_length(effect)
            self._effect_pos_f = -float(padding)
            self.effect_step = -padding
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

    # Effects whose end state is "all LEDs lit in the effect color"
    FILL_EFFECTS = {EFFECT_FILL_MIDDLE_OUT, EFFECT_FILL_OUTSIDE_IN,
                    EFFECT_TRANSITION_MIDDLE_OUT, EFFECT_TRANSITION_OUTSIDE_IN,
                    EFFECT_TRANSITION_FADE}
    # Effects whose end state is "all LEDs off"
    UNFILL_EFFECTS = {EFFECT_UNFILL_MIDDLE_OUT, EFFECT_UNFILL_OUTSIDE_IN}

    def _restore_state(self):
        """Determine end state after one-shot animation based on effect type.

        - Fill effects   → switch to static with the effect color (LEDs stay on)
        - Unfill effects → turn off (LEDs stay dark)
        - All others     → restore to the state before the one-shot started
        """
        try:
            finished_effect = self.current_effect

            if finished_effect in self.FILL_EFFECTS:
                # LEDs are fully lit → stay on as static in the effect color
                one_shot_color = self.effect_color
                if self._saved_state:
                    self.brightness = self._saved_state['brightness']
                self.is_on = True
                self.current_effect = self.EFFECT_STATIC
                self.current_color = one_shot_color
                self.effect_color = one_shot_color
                self._needs_refresh = True
                logger.info("One-shot complete (fill), static with color {}".format(one_shot_color))

            elif finished_effect in self.UNFILL_EFFECTS:
                # LEDs are all off → stay off
                if self._saved_state:
                    self.brightness = self._saved_state['brightness']
                self.is_on = False
                self.current_effect = self.EFFECT_STATIC
                self._needs_refresh = True
                logger.info("One-shot complete (unfill), LEDs off")

            else:
                # All other effects → restore previous state completely
                if self._saved_state:
                    self.is_on = self._saved_state['is_on']
                    self.current_effect = self._saved_state['effect']
                    self.current_color = self._saved_state['color']
                    self.effect_color = self._saved_state['effect_color']
                    self.brightness = self._saved_state['brightness']
                    self.effect_speed = self._saved_state['effect_speed']
                    self._speed_inc = self._saved_state['_speed_inc']
                    self.update_interval = self._saved_state['update_interval']
                    self.step_size = self._saved_state['step_size']
                self._needs_refresh = True
                logger.info("One-shot complete, state restored")

            self._saved_state = None
            self._one_shot_active = False
            self._one_shot_steps_remaining = 0
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
        pos_f = self._effect_pos_f
        for i in range(self.num_leds):
            hue = ((i * 256.0 / self.num_leds + pos_f) % 256) / 256.0
            r, g, b = self._hsv_to_rgb(hue, 1.0, 1.0)
            self.strip[i] = (int(r * br), int(g * br), int(b * br))
        self._show()

    def _effect_fade(self):
        """Color fade effect"""
        hue = (self._effect_pos_f % 256.0) / 256.0
        color = self._hsv_to_rgb(hue, 1.0, 1.0)
        self._set_all(color)
        self._show()

    def _effect_chase(self):
        """Chase/running light effect — sub-pixel smooth"""
        # Use modulo for looping, raw position for one-shot
        if self._one_shot_active:
            pos_f = self._effect_pos_f
        else:
            pos_f = self._effect_pos_f % self.num_leds
        er, eg, eb = self.effect_color
        br = self.brightness / 100.0
        # Dynamic tail length: base 8 + speed increment to stay smooth at high speeds
        chase_len = 8 + self._speed_inc

        for i in range(self.num_leds):
            # For one-shot: use direct distance, for loop: use modulo distance
            if self._one_shot_active:
                dist = pos_f - i
                if dist < 0 or dist >= chase_len:
                    self.strip[i] = (0, 0, 0)
                else:
                    f = (chase_len - dist) / chase_len * br
                    self.strip[i] = (int(er * f), int(eg * f), int(eb * f))
            else:
                dist = (pos_f - i) % self.num_leds
                if dist < chase_len:
                    f = (chase_len - dist) / chase_len * br
                    self.strip[i] = (int(er * f), int(eg * f), int(eb * f))
                else:
                    self.strip[i] = (0, 0, 0)

        self._show()

    def _effect_breathing(self):
        """Breathing effect (pulsing brightness)"""
        breath = (math.sin(self._effect_pos_f / 20.0) + 1) / 2  # 0-1
        temp_brightness = self.brightness
        self.brightness = int(breath * temp_brightness)
        self._set_all(self.effect_color)
        self._show()
        self.brightness = temp_brightness

    def _effect_twinkle(self):
        """Twinkle/sparkle effect with smooth LED fading"""
        color_on = self._apply_brightness(self.effect_color)
        tr, tg, tb = color_on

        # Decay all LEDs toward off (~0.93^60 ≈ 0.01/s → fade in ~0.5s)
        decay = 0.93
        for i in range(self.num_leds):
            r, g, b = self.strip[i]
            self.strip[i] = (int(r * decay), int(g * decay), int(b * decay))

        # Randomly spark a few LEDs to full brightness instantly
        num_sparkles = max(1, self.num_leds // 20)
        for _ in range(num_sparkles):
            if random.random() > 0.7:
                led = random.randint(0, self.num_leds - 1)
                self.strip[led] = (tr, tg, tb)

        self._show()

    def _effect_scanner(self):
        """Scanner effect (Knight Rider style) — sub-pixel smooth"""
        cycle_length = (self.num_leds - 1) * 2
        # Use modulo for looping, raw position for one-shot
        if self._one_shot_active:
            pos_f = self._effect_pos_f
            # Still compute bounce direction but allow enter/exit
            if pos_f < 0:
                actual_pos = pos_f  # Head entering from left
            elif pos_f < self.num_leds:
                actual_pos = pos_f
            elif pos_f < cycle_length:
                actual_pos = cycle_length - pos_f
            else:
                actual_pos = pos_f - cycle_length  # Head exiting
        else:
            pos_f = self._effect_pos_f % cycle_length
            actual_pos = pos_f if pos_f < self.num_leds else cycle_length - pos_f

        er, eg, eb = self.effect_color
        br = self.brightness / 100.0
        # Dynamic head length: base 8 + speed increment to stay smooth at high speeds
        head_len = 8 + self._speed_inc

        for i in range(self.num_leds):
            dist = abs(actual_pos - i)
            if dist < head_len:
                f = (head_len - dist) / head_len * br
                self.strip[i] = (int(er * f), int(eg * f), int(eb * f))
            else:
                self.strip[i] = (0, 0, 0)

        self._show()

    def _effect_strobe(self):
        """Strobe effect"""
        if self.effect_step % 2 == 0:
            self._set_all(self.effect_color)
        else:
            self._set_all(self.COLOR_OFF)
        self._show()

    def _effect_comet(self):
        """Comet effect — multiple comets with fading tails racing across the strip."""
        # Speed-dependent decay: tail stays ~15 LEDs visually regardless of speed
        # 0.85^speed_inc: slow speed → high decay (long tail ok), fast → aggressive decay
        decay = 0.85 ** self._speed_inc
        for i in range(self.num_leds):
            r, g, b = self.strip[i]
            self.strip[i] = (int(r * decay), int(g * decay), int(b * decay))

        # Comet parameters
        pos_f = self._effect_pos_f
        er, eg, eb = self.effect_color
        br = self.brightness / 100.0
        comet_len = 5  # Bright head length

        if self._one_shot_active:
            # Single comet, no wrapping
            head = pos_f
            for j in range(comet_len):
                pos = int(head - j)
                if 0 <= pos < self.num_leds:
                    f = (comet_len - j) / comet_len * br
                    pr, pg, pb = int(er * f), int(eg * f), int(eb * f)
                    old_r, old_g, old_b = self.strip[pos]
                    self.strip[pos] = (max(old_r, pr), max(old_g, pg), max(old_b, pb))
        else:
            # Multiple comets with wrapping for loop mode
            num_comets = max(1, self.num_leds // 30)  # ~1 comet per 30 LEDs
            spacing = self.num_leds // num_comets
            for c in range(num_comets):
                # Each comet is offset by spacing; they all advance with pos_f
                head = (pos_f + c * spacing) % self.num_leds
                for j in range(comet_len):
                    pos = int(head - j) % self.num_leds
                    f = (comet_len - j) / comet_len * br
                    pr, pg, pb = int(er * f), int(eg * f), int(eb * f)
                    # Keep the brighter of existing vs new value (comets can overlap tails)
                    old_r, old_g, old_b = self.strip[pos]
                    self.strip[pos] = (max(old_r, pr), max(old_g, pg), max(old_b, pb))

        self._show()

    def _effect_chase_middle_out(self):
        """Chase effect starting from the center going outward to both ends — sub-pixel smooth.

        pos_f = 0: Heads exactly at center
        pos_f = half: Heads at edges
        pos_f = half + chase_len: Tails have exited edges
        """
        half = self.num_leds / 2.0
        # Dynamic tail length: base 8 + speed increment to stay smooth at high speeds
        chase_len = 8 + self._speed_inc

        # In loop mode, wrap position
        if self._one_shot_active:
            pos_f = self._effect_pos_f
        else:
            cycle = half + chase_len
            pos_f = self._effect_pos_f % cycle

        er, eg, eb = self.effect_color
        br = self.brightness / 100.0
        center = (self.num_leds - 1) / 2.0  # Exact center point

        for i in range(self.num_leds):
            # Right head position: center + pos_f (moves right)
            # Left head position: center - pos_f (moves left)
            head_right = center + pos_f
            head_left = center - pos_f

            # Distance from each head (tail trails behind)
            # For right head: tail is to the left (smaller indices)
            # For left head: tail is to the right (larger indices)
            if i >= center:
                # Right half - check right head
                dist = head_right - i
                if 0 <= dist < chase_len:
                    f = (chase_len - dist) / chase_len * br
                    self.strip[i] = (int(er * f), int(eg * f), int(eb * f))
                else:
                    self.strip[i] = (0, 0, 0)
            else:
                # Left half - check left head
                dist = i - head_left
                if 0 <= dist < chase_len:
                    f = (chase_len - dist) / chase_len * br
                    self.strip[i] = (int(er * f), int(eg * f), int(eb * f))
                else:
                    self.strip[i] = (0, 0, 0)

        self._show()

    def _effect_chase_outside_in(self):
        """Chase effect starting from both ends going inward to the center — sub-pixel smooth.

        pos_f = -chase_len: Heads outside the strip (one-shot entry)
        pos_f = 0: Heads at the edges (LED 0 and LED num_leds-1)
        pos_f = half: Heads meet at center
        pos_f = half + chase_len: Tails have finished at center
        """
        half = self.num_leds / 2.0
        # Dynamic tail length: base 8 + speed increment to stay smooth at high speeds
        chase_len = 8 + self._speed_inc

        # In loop mode, wrap position
        if self._one_shot_active:
            pos_f = self._effect_pos_f
        else:
            cycle = half + chase_len
            pos_f = self._effect_pos_f % cycle

        er, eg, eb = self.effect_color
        br = self.brightness / 100.0
        center = (self.num_leds - 1) / 2.0

        for i in range(self.num_leds):
            # Left head position: pos_f (starts at 0/negative, moves right toward center)
            # Right head position: (num_leds - 1) - pos_f (starts at end, moves left toward center)
            head_left = pos_f
            head_right = (self.num_leds - 1) - pos_f

            # Distance from each head (tail trails behind)
            if i <= center:
                # Left half - check left head
                dist = i - head_left
                if 0 <= dist < chase_len:
                    f = (chase_len - dist) / chase_len * br
                    self.strip[i] = (int(er * f), int(eg * f), int(eb * f))
                else:
                    self.strip[i] = (0, 0, 0)
            else:
                # Right half - check right head
                dist = head_right - i
                if 0 <= dist < chase_len:
                    f = (chase_len - dist) / chase_len * br
                    self.strip[i] = (int(er * f), int(eg * f), int(eb * f))
                else:
                    self.strip[i] = (0, 0, 0)

        self._show()

    def _effect_fill_middle_out(self):
        """Fill effect: LEDs turn on from center outward.

        pos_f = 0: No LEDs on
        pos_f = half: All LEDs on
        Used as transition to turn ON the strip.
        """
        pos_f = self._effect_pos_f
        center = (self.num_leds - 1) / 2.0
        color = self._apply_brightness(self.effect_color)

        for i in range(self.num_leds):
            # Distance from center
            dist_from_center = abs(i - center)
            # LED is on if the "fill line" has passed it
            if dist_from_center <= pos_f:
                self.strip[i] = color
            else:
                self.strip[i] = (0, 0, 0)

        self._show()

    def _effect_fill_outside_in(self):
        """Fill effect: LEDs turn on from edges inward to center.

        pos_f = 0: No LEDs on
        pos_f = half: All LEDs on
        Used as transition to turn ON the strip.
        """
        pos_f = self._effect_pos_f
        color = self._apply_brightness(self.effect_color)

        for i in range(self.num_leds):
            # Distance from nearest edge
            dist_from_edge = min(i, self.num_leds - 1 - i)
            # LED is on if the "fill line" has passed it (from edge)
            if dist_from_edge <= pos_f:
                self.strip[i] = color
            else:
                self.strip[i] = (0, 0, 0)

        self._show()

    def _effect_unfill_middle_out(self):
        """Unfill effect: LEDs turn off from center outward.

        pos_f = 0: All LEDs on
        pos_f = half: All LEDs off
        Used as transition to turn OFF the strip.
        """
        pos_f = self._effect_pos_f
        center = (self.num_leds - 1) / 2.0
        color = self._apply_brightness(self.effect_color)

        for i in range(self.num_leds):
            # Distance from center
            dist_from_center = abs(i - center)
            # LED is OFF if the "unfill line" has passed it (from center)
            if dist_from_center < pos_f:
                self.strip[i] = (0, 0, 0)
            else:
                self.strip[i] = color

        self._show()

    def _effect_unfill_outside_in(self):
        """Unfill effect: LEDs turn off from edges inward to center.

        pos_f = 0: All LEDs on
        pos_f = half: All LEDs off
        Used as transition to turn OFF the strip.
        """
        pos_f = self._effect_pos_f
        color = self._apply_brightness(self.effect_color)

        for i in range(self.num_leds):
            # Distance from nearest edge
            dist_from_edge = min(i, self.num_leds - 1 - i)
            # LED is OFF if the "unfill line" has passed it (from edge)
            if dist_from_edge < pos_f:
                self.strip[i] = (0, 0, 0)
            else:
                self.strip[i] = color

        self._show()

    def _effect_transition_middle_out(self):
        """Transition effect: Color changes from center outward.

        LEDs near the center switch to the new color first,
        spreading outward to the edges.
        """
        pos_f = self._effect_pos_f
        center = (self.num_leds - 1) / 2.0
        new_color = self._apply_brightness(self.effect_color)
        old_color = self._apply_brightness(self._transition_from_color)

        for i in range(self.num_leds):
            dist_from_center = abs(i - center)
            if dist_from_center <= pos_f:
                self.strip[i] = new_color
            else:
                self.strip[i] = old_color

        self._show()

    def _effect_transition_outside_in(self):
        """Transition effect: Color changes from edges inward to center.

        LEDs at the edges switch to the new color first,
        spreading inward to the center.
        """
        pos_f = self._effect_pos_f
        new_color = self._apply_brightness(self.effect_color)
        old_color = self._apply_brightness(self._transition_from_color)

        for i in range(self.num_leds):
            dist_from_edge = min(i, self.num_leds - 1 - i)
            if dist_from_edge <= pos_f:
                self.strip[i] = new_color
            else:
                self.strip[i] = old_color

        self._show()

    def _effect_transition_fade(self):
        """Transition effect: Smooth crossfade from old to new color.

        All LEDs blend simultaneously. pos_f goes from 0 to 64.
        At 0: fully old color. At 64: fully new color.
        """
        pos_f = self._effect_pos_f
        total = 64.0
        t = max(0.0, min(1.0, pos_f / total))  # blend factor 0.0 → 1.0

        br = self.brightness / 100.0
        or_, og, ob = self._transition_from_color
        nr, ng, nb = self.effect_color

        # Interpolate between old and new color
        r = int((or_ + (nr - or_) * t) * br)
        g = int((og + (ng - og) * t) * br)
        b = int((ob + (nb - ob) * t) * br)

        for i in range(self.num_leds):
            self.strip[i] = (r, g, b)
        self._show()

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

    def _effect_progress_bar(self):
        """Progress bar effect with smooth LED-by-LED transitions.

        Animates _progress_display toward progress_value each frame.
        Uses sub-pixel rendering at the boundary for a smooth fill.
        Transition speed: ~0.5% per frame @ 60 FPS → full bar in ~3.3s.
        """
        target = float(self.progress_value)

        # Snap display to target immediately (cross-core safe via flag)
        snapped = False
        if self._progress_snap:
            self._progress_display = target
            self._progress_snap = False
            snapped = True

        diff = target - self._progress_display

        # Skip render when display matches target and no refresh pending
        # Always render after a snap to guarantee the first frame is drawn
        if abs(diff) < 0.01 and not self._needs_refresh and not snapped:
            return

        # Smoothly move display toward target (~30%/s at 60 FPS)
        if abs(diff) < 0.05:
            self._progress_display = target
        else:
            rate = 0.5  # percent per frame
            if diff > 0:
                self._progress_display = min(target, self._progress_display + rate)
            else:
                self._progress_display = max(target, self._progress_display - rate)

        self._needs_refresh = False

        er, eg, eb = self.effect_color
        br = self.brightness / 100.0

        # Calculate how many LEDs should be lit (float for sub-pixel)
        fill_f = (self._progress_display / 100.0) * self.num_leds

        for i in range(self.num_leds):
            if i < int(fill_f):
                # Fully lit LED
                self.strip[i] = (int(er * br), int(eg * br), int(eb * br))
            elif i < fill_f:
                # Boundary LED — partial brightness for smooth transition
                frac = fill_f - int(fill_f)
                self.strip[i] = (int(er * br * frac), int(eg * br * frac), int(eb * br * frac))
            else:
                # Off
                self.strip[i] = (0, 0, 0)
        self._show()

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

    def set_progress(self, value):
        """Set progress bar value (0-100)"""
        try:
            self.progress_value = max(0, min(100, value))
            self._needs_refresh = True
            logger.info("Progress set to {}%".format(self.progress_value))
        except Exception as e:
            logger.error("Failed to set progress: {}".format(e))

    def get_state(self):
        """Get current state for reporting to Home Assistant"""
        import gc

        diagnostics = {
            "estimated_current_ma": self.get_estimated_current_ma(),
            "free_memory": gc.mem_free(),
        }

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
                "progress": self.progress_value,
                "one_shot_active": True,
                "one_shot_effect": self.current_effect,
                "diagnostics": diagnostics,
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
            "progress": self.progress_value,
            "one_shot_active": False,
            "diagnostics": diagnostics,
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
