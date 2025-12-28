"""
Non-blocking NeoPixel Controller with Effects
Manages LED strip with various animations that don't block command reception
"""
import time
import math
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
            self.effect_color = (255, 0, 0)
            
            logger.info("NeoPixel initialized: {} LEDs on pin {}".format(num_leds, pin))
            
        except Exception as e:
            logger.error("Failed to initialize NeoPixel: {}".format(e))
            raise
    
    def _apply_brightness(self, color):
        """Apply brightness adjustment to color"""
        factor = self.brightness / 100.0
        return tuple(int(c * factor) for c in color)
    
    def _set_all(self, color):
        """Set all LEDs to same color"""
        color = self._apply_brightness(color)
        for i in range(self.num_leds):
            self.strip[i] = color
    
    def _show(self):
        """Update LED strip (with error handling)"""
        try:
            self.strip.write()
        except Exception as e:
            logger.error("Failed to update strip: {}".format(e))
    
    def update(self):
        """Non-blocking update method - call frequently in main loop"""
        if not self.is_on:
            return
        
        current_time = time.ticks_ms()
        if time.ticks_diff(current_time, self.last_update) < self.update_interval:
            return
        
        self.last_update = current_time
        
        try:
            if self.current_effect == self.EFFECT_STATIC:
                self._effect_static()
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
        except Exception as e:
            logger.error("Effect update failed: {}".format(e))
    
    def set_color(self, r, g, b):
        """Set static color"""
        try:
            self.is_on = True
            self.current_effect = self.EFFECT_STATIC
            self.current_color = (r, g, b)
            self.effect_color = (r, g, b)
            self._set_all(self.current_color)
            self._show()
            logger.info("Color set to RGB({}, {}, {})".format(r, g, b))
        except Exception as e:
            logger.error("Failed to set color: {}".format(e))
    
    def set_effect(self, effect_name, speed=50):
        """Start an effect"""
        try:
            self.is_on = True
            self.current_effect = effect_name
            self.effect_speed = max(1, min(100, speed))
            self.effect_step = 0
            self.update_interval = 200 - (self.effect_speed * 2)  # Faster speed = shorter interval
            logger.info("Effect started: {} (speed: {})".format(effect_name, speed))
        except Exception as e:
            logger.error("Failed to set effect: {}".format(e))
    
    def turn_off(self):
        """Turn off all LEDs"""
        try:
            self.is_on = False
            self._set_all(self.COLOR_OFF)
            self._show()
            logger.info("LEDs turned off")
        except Exception as e:
            logger.error("Failed to turn off: {}".format(e))
    
    def turn_on(self):
        """Turn on with last color/effect"""
        try:
            self.is_on = True
            logger.info("LEDs turned on")
        except Exception as e:
            logger.error("Failed to turn on: {}".format(e))
    
    def set_brightness(self, brightness):
        """Set brightness (0-100)"""
        try:
            self.brightness = max(0, min(100, brightness))
            logger.info("Brightness set to {}%".format(self.brightness))
        except Exception as e:
            logger.error("Failed to set brightness: {}".format(e))
    
    # Effect implementations
    
    def _effect_static(self):
        """Static color effect"""
        # Already set, no animation needed
        pass
    
    def _effect_rainbow(self):
        """Rainbow cycle effect"""
        for i in range(self.num_leds):
            hue = (i * 256 // self.num_leds + self.effect_step) % 256
            color = self._hsv_to_rgb(hue / 256.0, 1.0, 1.0)
            self.strip[i] = self._apply_brightness(color)
        self._show()
        self.effect_step = (self.effect_step + 1) % 256
    
    def _effect_fade(self):
        """Color fade effect"""
        hue = self.effect_step / 256.0
        color = self._hsv_to_rgb(hue, 1.0, 1.0)
        self._set_all(color)
        self._show()
        self.effect_step = (self.effect_step + 1) % 256
    
    def _effect_chase(self):
        """Chase/running light effect"""
        self._set_all(self.COLOR_OFF)
        pos = self.effect_step % self.num_leds
        for i in range(5):  # 5 LEDs in the chase
            led_pos = (pos + i) % self.num_leds
            self.strip[led_pos] = self._apply_brightness(self.effect_color)
        self._show()
        self.effect_step += 1
    
    def _effect_breathing(self):
        """Breathing effect (pulsing brightness)"""
        breath = (math.sin(self.effect_step / 20.0) + 1) / 2  # 0-1
        temp_brightness = self.brightness
        self.brightness = int(breath * temp_brightness)
        self._set_all(self.effect_color)
        self._show()
        self.brightness = temp_brightness
        self.effect_step += 1
    
    def _effect_twinkle(self):
        """Twinkle/sparkle effect"""
        import random
        if self.effect_step % 3 == 0:  # Update some LEDs
            for _ in range(self.num_leds // 10):  # 10% of LEDs
                led = random.randint(0, self.num_leds - 1)
                if random.random() > 0.5:
                    self.strip[led] = self._apply_brightness(self.effect_color)
                else:
                    self.strip[led] = self.COLOR_OFF
            self._show()
        self.effect_step += 1
    
    def _effect_scanner(self):
        """Scanner effect (Knight Rider style)"""
        self._set_all(self.COLOR_OFF)
        
        cycle_length = (self.num_leds - 1) * 2
        pos = self.effect_step % cycle_length
        
        if pos < self.num_leds:
            actual_pos = pos
        else:
            actual_pos = cycle_length - pos
        
        # Draw scanner with tail
        for i in range(5):
            led_pos = actual_pos - i
            if 0 <= led_pos < self.num_leds:
                brightness_factor = (5 - i) / 5.0
                color = tuple(int(c * brightness_factor) for c in self.effect_color)
                self.strip[led_pos] = self._apply_brightness(color)
        
        self._show()
        self.effect_step += 1
    
    def _effect_strobe(self):
        """Strobe effect"""
        if self.effect_step % 2 == 0:
            self._set_all(self.effect_color)
        else:
            self._set_all(self.COLOR_OFF)
        self._show()
        self.effect_step += 1
    
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
                    time.sleep(0.005)
                
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
                # Ready - solid white (brief)
                self.is_on = False
                self._set_all(self.COLOR_READY)
                self._show()
                time.sleep(1)
                self.turn_off()
                
        except Exception as e:
            logger.error("Failed to show status: {}".format(e))
    
    def get_state(self):
        """Get current state for reporting to Home Assistant"""
        return {
            "is_on": self.is_on,
            "effect": self.current_effect,
            "brightness": self.brightness,
            "color": {
                "r": self.current_color[0],
                "g": self.current_color[1],
                "b": self.current_color[2]
            }
        }
