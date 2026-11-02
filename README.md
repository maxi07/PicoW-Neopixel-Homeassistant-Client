# PicoW NeoPixel Controller

MicroPython-based NeoPixel LED controller for Raspberry Pi Pico W with REST API and Home Assistant integration support.

## 🧩 Part of a two-repository project

This firmware is the **device side** of the project. It runs on the Pico W, drives the LEDs and serves a REST API that the Home Assistant integration talks to:

| Repository | Role | Language |
|---|---|---|
| **PicoW-Neopixel-Homeassistant-Client** (this repo) | MicroPython firmware for the Pico W — drives the LEDs and serves the REST API | MicroPython |
| [**PicoW-Neopixel-Homeassistant-Integration**](https://github.com/maxi07/PicoW-Neopixel-Homeassistant-Integration) | Home Assistant custom integration — discovers the device and exposes it as a light entity | Python |

👉 **You need both:** flash this firmware onto your Pico W, then install the [Home Assistant integration](https://github.com/maxi07/PicoW-Neopixel-Homeassistant-Integration) — now available directly in [HACS](https://hacs.xyz/).

## Features

- **Non-blocking Animation Engine**: All effects run without blocking command reception
- **REST API**: Full HTTP REST API for remote control
- **mDNS / Zeroconf Discovery**: Advertises `_picow-neopixel._tcp.local.` so Home Assistant discovers the device automatically — no LAN scanning needed
- **Auto-reconnect**: Automatic WiFi reconnection on connection loss
- **Status Indicators**: Visual feedback for boot, WiFi connecting, errors, and ready states
- **Multiple Effects**: 8 built-in LED effects (rainbow, fade, chase, breathing, twinkle, scanner, strobe, static)
- **Logging**: Comprehensive logging system with file output for warnings and errors
- **Easy Configuration**: JSON-based configuration for WiFi and hardware settings

## Hardware Requirements

- Raspberry Pi Pico W
- WS2812B NeoPixel LED strip (or compatible)
- 5V power supply (appropriate for your LED strip)
- Connecting wires

## Wiring

Connect your NeoPixel strip to the Pico W:

- **LED Data**: GPIO 28 (configurable in config.json)
- **LED 5V**: External 5V power supply
- **LED GND**: Common ground with Pico W
- **Pico W**: USB power or 5V via VSYS

**Important**: For strips with more than ~10 LEDs, use an external 5V power supply. Connect grounds together.

## Installation

### 1. Flash MicroPython

1. Download the latest MicroPython firmware for Pico W from [micropython.org](https://micropython.org/download/rp2-pico-w/)
2. Hold the BOOTSEL button on your Pico W and connect it via USB
3. Copy the .uf2 file to the RPI-RP2 drive that appears
4. The Pico W will reboot with MicroPython installed

### 2. Upload Files

Upload all Python files to your Pico W using one of these methods:

**Option A: Using Thonny IDE**
1. Install [Thonny](https://thonny.org/)
2. Configure Thonny to use your Pico W (Tools → Options → Interpreter)
3. Open each .py file and save it to the Pico W
4. Upload config.json and edit it with your settings

**Option B: Using mpremote**
```bash
pip install mpremote
mpremote connect /dev/ttyACM0 fs cp *.py :
mpremote connect /dev/ttyACM0 fs cp config.json :
```

**Option C: Using ampy**
```bash
pip install adafruit-ampy
ampy --port /dev/ttyACM0 put main.py
ampy --port /dev/ttyACM0 put config_loader.py
ampy --port /dev/ttyACM0 put logger.py
ampy --port /dev/ttyACM0 put network_manager.py
ampy --port /dev/ttyACM0 put neopixel_controller.py
ampy --port /dev/ttyACM0 put api_server.py
ampy --port /dev/ttyACM0 put mdns_service.py
ampy --port /dev/ttyACM0 put config.json
```

### 3. Configure

Edit `config.json` on the Pico W with your settings:

```json
{
    "wifi": {
        "ssid": "YOUR_WIFI_SSID",
        "password": "YOUR_WIFI_PASSWORD"
    },
    "device": {
        "name": "PicoW NeoPixel",
        "id": "picow_neopixel_001"
    },
    "neopixel": {
        "pin": 28,
        "num_leds": 64,
        "brightness": 100,
        "rgbw": false
    },
    "api": {
        "port": 80
    }
}
```

**Configuration Options:**
- `wifi.ssid`: Your WiFi network name
- `wifi.password`: Your WiFi password
- `device.name`: Friendly name for the device
- `device.id`: Unique identifier (use different IDs for multiple devices)
- `neopixel.pin`: GPIO pin number for LED data (default: 28)
- `neopixel.num_leds`: Number of LEDs in your strip (default: 64)
- `neopixel.brightness`: Default brightness 0-100 (default: 100)
- `neopixel.rgbw`: Set to `true` for RGBW strips; shared RGB intensity is routed through the white channel (default: `false`)
- `api.port`: HTTP API port (default: 80)

### 4. Run

The controller starts automatically on boot. To test manually:

```python
import main
```

## Status LED Indicators

The LED strip shows different patterns during startup:

- **Rainbow Sweep**: Boot animation
- **Pulsing Blue**: Connecting to WiFi
- **Solid Red**: Error (check logs.txt)
- **Solid White**: Successfully connected and ready
- **Off**: Waiting for commands

## API Endpoints

### GET /
Returns device information and available endpoints.

### GET /state
Returns current LED state:
```json
{
    "is_on": true,
    "effect": "rainbow",
    "brightness": 100,
    "color": {"r": 255, "g": 0, "b": 0}
}
```

### GET /info
Returns device capabilities and configuration:
```json
{
    "device": {
        "name": "PicoW NeoPixel",
        "id": "picow_neopixel_001",
        "ip": "192.168.1.100",
        "mac": "aa:bb:cc:dd:ee:ff"
    },
    "capabilities": {
        "effects": ["static", "rainbow", "fade", "chase", "breathing", "twinkle", "scanner", "strobe"],
        "num_leds": 64,
        "supports_rgb": true,
        "supports_brightness": true
    }
}
```

### POST /control
Control the LED strip. Request body examples:

**Turn on/off:**
```json
{"power": "on"}
{"power": "off"}
```

**Set static color:**
```json
{
    "power": "on",
    "color": {"r": 255, "g": 0, "b": 0}
}
```

**Set brightness:**
```json
{
    "brightness": 50
}
```

**Start effect:**
```json
{
    "power": "on",
    "effect": "rainbow",
    "speed": 50
}
```

**Effect with color:**
```json
{
    "power": "on",
    "effect": "chase",
    "color": {"r": 0, "g": 255, "b": 0},
    "speed": 75
}
```

### GET /logs
Returns the existing `logs.txt` as `text/plain; charset=utf-8` (WARNING and
ERROR messages by default). If no log file exists yet, returns `200 OK`
with an empty body. File access errors return `500`.

```bash
curl http://DEVICE_IP/logs
```

The file is streamed in blocks of at most 512 bytes, not loaded into RAM
or converted to JSON. `Content-Length` is the file size at the start of the
request; entries appended during the download are included in the next request.
No additional log buffer or flash writes are introduced. A transfer error
after the headers have been sent closes the connection with an incomplete body.
Like the other endpoints, `/logs` has no authentication; expose it only on a
trusted network. Log files can contain diagnostic information.

## Available Effects

- `static`: Solid color
- `rainbow`: Rainbow cycle animation
- `fade`: Smooth color fade through spectrum
- `chase`: Running light effect
- `breathing`: Pulsing brightness effect
- `twinkle`: Random sparkle effect
- `scanner`: Knight Rider style scanner
- `strobe`: Strobe light effect

Effect speed: 1-100 (higher = faster)

## Logging

Logs are written to both console and `logs.txt` file on the Pico W.

- Console: All messages (DEBUG, INFO, WARNING, ERROR)
- File: Only WARNING and ERROR messages

View logs via serial connection, read logs.txt from the device, or download
the file using `GET /logs`. DEBUG and INFO messages remain console-only.

## Troubleshooting

### LEDs show solid red
- Error during startup
- Check `logs.txt` for details
- Common issues: WiFi credentials incorrect, hardware not connected

### Can't connect to WiFi
- Verify SSID and password in config.json
- Ensure 2.4GHz WiFi (Pico W doesn't support 5GHz)
- Check WiFi signal strength

### LEDs not responding
- Verify GPIO pin in config.json matches your wiring
- Check power supply is adequate
- Ensure data line is connected properly

### API not accessible
- Check device IP address from logs or router
- Verify firewall settings
- Try accessing http://DEVICE_IP/ from browser

### Memory errors
- Reduce number of LEDs if experiencing crashes
- The controller performs periodic garbage collection

## Home Assistant Integration

This controller is designed to work hand-in-hand with the **[PicoW NeoPixel Home Assistant Integration](https://github.com/maxi07/PicoW-Neopixel-Homeassistant-Integration)**.

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)

The integration is available in the **[HACS](https://hacs.xyz/) default repositories** — just search for **PicoW NeoPixel** in HACS, install it, and restart Home Assistant. Thanks to the mDNS advertisement (`_picow-neopixel._tcp.local.`) built into this firmware, Home Assistant discovers the device automatically and exposes it as a light entity — no LAN scanning required.

👉 Repository: [maxi07/PicoW-Neopixel-Homeassistant-Integration](https://github.com/maxi07/PicoW-Neopixel-Homeassistant-Integration)

### More about this project

- 🗂️ **Project page:** [maexbert.de/projekte/homeassistant-led](https://maexbert.de/projekte/homeassistant-led)
- 📖 **Blog article:** [PicoW NeoPixel is now available in HACS](https://maexbert.de/blog/2026-07-06-picow-neopixel-hacs-verfuegbar) — the story behind the HACS default listing.

## License

MIT License - feel free to modify and use for your projects.

## Author

Maximilian Krause

## Version

1.0
