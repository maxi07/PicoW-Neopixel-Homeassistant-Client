"""
Configuration loader for PicoW NeoPixel Controller
Loads and validates configuration from config.json
"""
import json
from logger import Logger

logger = Logger("Config")


class Config:
    def __init__(self, config_file="config.json"):
        self.config_file = config_file
        self.config = {}
        self.load()

    def load(self):
        """Load configuration from JSON file"""
        try:
            with open(self.config_file, "r") as f:
                self.config = json.load(f)
            logger.info("Configuration loaded successfully")
            self._validate()
            return True
        except Exception as e:
            logger.error("Failed to load config: {}".format(e))
            self._set_defaults()
            return False

    def _validate(self):
        """Validate required configuration fields"""
        required_fields = {
            "wifi": ["ssid", "password"],
            "device": ["name", "id"],
            "neopixel": ["pin", "num_leds"],
            "api": ["port"]
        }

        for section, fields in required_fields.items():
            if section not in self.config:
                logger.warning("Missing config section: {}".format(section))
                return False
            for field in fields:
                if field not in self.config[section]:
                    logger.warning("Missing config field: {}.{}".format(section, field))
                    return False

        return True

    def _set_defaults(self):
        """Set default configuration values"""
        logger.warning("Using default configuration")
        self.config = {
            "wifi": {"ssid": "", "password": ""},
            "device": {"name": "PicoW NeoPixel", "id": "picow_neopixel_001"},
            "neopixel": {"pin": 28, "num_leds": 64, "brightness": 100},
            "api": {"port": 80}
        }

    def get(self, section: str, key: str, default=None):
        """Get configuration value"""
        try:
            return self.config.get(section, {}).get(key, default)
        except Exception:
            return default

    def get_section(self, section):
        """Get entire configuration section"""
        return self.config.get(section, {})
