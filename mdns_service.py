"""
Minimal mDNS / DNS-SD responder for MicroPython.

Advertises a single service (typically ``_picow-neopixel._tcp.local.``) so that
Home Assistant's Zeroconf component can discover the device without anyone
having to scan the LAN.

Designed to be polled non-blockingly from the main event loop:

    mdns = MDNSResponder(
        hostname="picow-neopixel-001",
        instance_name="Living Room Strip",
        service_type="_picow-neopixel._tcp.local.",
        port=80,
        ip="192.168.1.42",
        txt_records={"id": "picow_neopixel_001", "name": "Living Room Strip"},
    )
    mdns.start()
    while True:
        mdns.poll()

The implementation only handles the cases the integration needs (PTR/SRV/TXT/A
queries for our own records) but follows RFC 6762 / 6763 closely enough to
interoperate with Avahi and the Python ``zeroconf`` library used by HA.
"""
import socket
import struct
import time

from logger import Logger

logger = Logger("mDNS")

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
MDNS_TTL = 120
ANNOUNCE_INTERVAL_MS = 60_000  # Re-announce roughly every minute

# Linux/lwIP value for SO_REUSEPORT; not always exposed in MicroPython.
SO_REUSEPORT = 15

CLASS_IN = 0x0001
CLASS_FLUSH = 0x8000

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
TYPE_ANY = 255


def _encode_name(name):
    """Encode a dotted DNS name as length-prefixed labels terminated by 0x00."""
    out = b""
    for label in name.split("."):
        if not label:
            continue
        label_bytes = label.encode("utf-8")
        if len(label_bytes) > 63:
            label_bytes = label_bytes[:63]
        out += bytes([len(label_bytes)]) + label_bytes
    return out + b"\x00"


def _decode_name(data, offset):
    """Decode a (possibly compressed) DNS name. Returns (name, new_offset)."""
    labels = []
    jumped = False
    next_offset = offset
    safety = 0
    while True:
        safety += 1
        if safety > 64 or offset >= len(data):
            break
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                break
            ptr = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                next_offset = offset + 2
            offset = ptr
            jumped = True
            continue
        offset += 1
        labels.append(data[offset:offset + length].decode("utf-8", "replace"))
        offset += length
    if not jumped:
        next_offset = offset
    return ".".join(labels), next_offset


def _ip_to_bytes(ip):
    return bytes(int(x) for x in ip.split("."))


class MDNSResponder:
    """Advertise a single DNS-SD service over multicast DNS."""

    def __init__(self, hostname, instance_name, service_type, port, ip,
                 txt_records=None):
        # Strip trailing dots / .local suffix so we can normalize ourselves.
        hostname = hostname.strip(".")
        if hostname.lower().endswith(".local"):
            hostname = hostname[: -len(".local")]
        self.hostname = hostname
        self.fqdn = hostname + ".local"

        service_type = service_type.strip(".")
        self.service_type = service_type
        # Service instance name: "<friendly>.<service_type>".
        self.instance = "{}.{}".format(instance_name, service_type)

        self.port = int(port)
        self.ip = ip
        self.txt_records = txt_records or {}

        self.sock = None
        self._send_only = False
        self._last_announce = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self):
        """Open the mDNS socket and send the initial announcement.

        First tries to bind UDP 5353 so we can both send announcements and
        answer queries. On MicroPython for the Pico W this normally fails with
        ``EADDRINUSE`` because lwIP's built-in mDNS responder already owns the
        port (it handles ``<hostname>.local`` resolution). In that case we
        fall back to a send-only socket on an ephemeral port: HA's Zeroconf
        component still picks up our unsolicited multicast announcements.
        """
        # First try: full responder bound to 5353.
        sock = self._try_bind_5353()
        if sock is not None:
            self.sock = sock
            self._send_only = False
            logger.info(
                "mDNS responder ready (full): {} on {}:{} ({})".format(
                    self.instance, self.ip, self.port, self.fqdn))
        else:
            # Fallback: send-only socket on an ephemeral port.
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                self.sock = sock
                self._send_only = True
                logger.info(
                    "mDNS responder ready (send-only, port 5353 already in "
                    "use): {} on {}:{} ({})".format(
                        self.instance, self.ip, self.port, self.fqdn))
            except Exception as e:
                logger.error("Failed to open mDNS send socket: {}".format(e))
                self.sock = None
                return False

        # RFC 6762 §8.3: send two announcements ~1s apart at startup. The
        # second send happens on the first call to poll() once enough time has
        # passed; here we do the initial one and pre-arm the timer.
        self.announce()
        # Force the next poll() to re-announce quickly (~1s later).
        self._last_announce = time.ticks_add(
            time.ticks_ms(), -(ANNOUNCE_INTERVAL_MS - 1000))
        return True

    def _try_bind_5353(self):
        """Attempt to bind UDP 5353 with REUSEADDR/REUSEPORT. Returns sock or None."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, SO_REUSEPORT, 1)
            except OSError:
                pass  # Not supported on this port; SO_REUSEADDR may suffice.
            sock.bind(("0.0.0.0", MDNS_PORT))
            try:
                mreq = _ip_to_bytes(MDNS_ADDR) + _ip_to_bytes(self.ip)
                sock.setsockopt(socket.IPPROTO_IP,
                                socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError as e:
                # On the Pico W lwIP joins 224.0.0.251 implicitly anyway.
                logger.debug("IP_ADD_MEMBERSHIP not available: {}".format(e))
            sock.setblocking(False)
            return sock
        except OSError as e:
            logger.warning(
                "Could not bind UDP {} ({}); falling back to send-only mDNS"
                .format(MDNS_PORT, e))
            try:
                sock.close()
            except Exception:
                pass
            return None
        except Exception as e:
            logger.error("Unexpected error binding mDNS socket: {}".format(e))
            try:
                sock.close()
            except Exception:
                pass
            return None

    def stop(self):
        """Send a goodbye packet (TTL=0) and close the socket."""
        if not self.sock:
            return
        try:
            self._send_response(ttl=0)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None
        logger.info("mDNS responder stopped")

    # ------------------------------------------------------------------
    # Periodic work
    # ------------------------------------------------------------------
    def poll(self):
        """Service the socket and re-announce periodically. Non-blocking."""
        if not self.sock:
            return

        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_announce) > ANNOUNCE_INTERVAL_MS:
            self.announce()

        # Send-only mode has no receive socket bound to 5353.
        if self._send_only:
            return

        # Drain any pending packets.
        for _ in range(8):
            try:
                data, _addr = self.sock.recvfrom(1500)
            except OSError:
                return
            if not data:
                return
            try:
                self._handle_query(data)
            except Exception as e:
                logger.debug("mDNS parse error: {}".format(e))

    def announce(self):
        """Send an unsolicited announcement for all our records."""
        self._send_response()
        self._last_announce = time.ticks_ms()
        logger.debug("mDNS announcement sent")

    def update_ip(self, new_ip):
        """Update the advertised IP address and re-announce immediately.

        Call this whenever DHCP assigns a new address after a reconnect so
        that the A record stays accurate without restarting the responder.
        """
        if new_ip == self.ip:
            return
        self.ip = new_ip
        logger.info("mDNS IP updated to {}".format(new_ip))
        self.announce()

    # ------------------------------------------------------------------
    # Packet building
    # ------------------------------------------------------------------
    def _build_txt(self):
        body = b""
        for key, value in self.txt_records.items():
            entry = "{}={}".format(key, value).encode("utf-8")
            if len(entry) > 255:
                entry = entry[:255]
            body += bytes([len(entry)]) + entry
        return body or b"\x00"

    def _build_records(self, ttl, answers):
        records = []
        if "ptr" in answers:
            records.append((
                _encode_name(self.service_type), TYPE_PTR, CLASS_IN,
                ttl, _encode_name(self.instance),
            ))
        if "srv" in answers:
            target = _encode_name(self.fqdn)
            rdata = struct.pack("!HHH", 0, 0, self.port) + target
            records.append((
                _encode_name(self.instance), TYPE_SRV,
                CLASS_IN | CLASS_FLUSH, ttl, rdata,
            ))
        if "txt" in answers:
            records.append((
                _encode_name(self.instance), TYPE_TXT,
                CLASS_IN | CLASS_FLUSH, ttl, self._build_txt(),
            ))
        if "a" in answers:
            records.append((
                _encode_name(self.fqdn), TYPE_A,
                CLASS_IN | CLASS_FLUSH, ttl, _ip_to_bytes(self.ip),
            ))
        return records

    def _send_response(self, ttl=MDNS_TTL, answers=("ptr", "srv", "txt", "a")):
        if not self.sock:
            return
        records = self._build_records(ttl, answers)
        # transaction id=0, flags=0x8400 (response | authoritative answer)
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0)
        body = b""
        for name, rtype, rclass, rttl, rdata in records:
            body += name + struct.pack("!HHIH", rtype, rclass, rttl,
                                       len(rdata)) + rdata
        try:
            self.sock.sendto(header + body, (MDNS_ADDR, MDNS_PORT))
        except Exception as e:
            logger.debug("mDNS send failed: {}".format(e))

    # ------------------------------------------------------------------
    # Query handling
    # ------------------------------------------------------------------
    def _handle_query(self, data):
        if len(data) < 12:
            return
        _tid, flags, qd, _an, _ns, _ar = struct.unpack("!HHHHHH", data[:12])
        # Ignore responses (QR=1) and packets with no questions.
        if flags & 0x8000 or qd == 0:
            return

        offset = 12
        wants = []
        service_lc = self.service_type.lower()
        instance_lc = self.instance.lower()
        fqdn_lc = self.fqdn.lower()

        for _ in range(qd):
            name, offset = _decode_name(data, offset)
            if offset + 4 > len(data):
                return
            qtype, _qclass = struct.unpack("!HH", data[offset:offset + 4])
            offset += 4
            name_lc = name.lower()

            if name_lc == service_lc and qtype in (TYPE_PTR, TYPE_ANY):
                # Full DNS-SD answer: PTR + SRV + TXT + A.
                for kind in ("ptr", "srv", "txt", "a"):
                    if kind not in wants:
                        wants.append(kind)
            elif name_lc == instance_lc:
                if qtype in (TYPE_SRV, TYPE_ANY) and "srv" not in wants:
                    wants.append("srv")
                if qtype in (TYPE_TXT, TYPE_ANY) and "txt" not in wants:
                    wants.append("txt")
                if qtype in (TYPE_SRV, TYPE_ANY) and "a" not in wants:
                    wants.append("a")
            elif name_lc == fqdn_lc and qtype in (TYPE_A, TYPE_ANY):
                if "a" not in wants:
                    wants.append("a")

        if wants:
            self._send_response(answers=tuple(wants))
            logger.debug("mDNS replied with: {}".format(wants))
