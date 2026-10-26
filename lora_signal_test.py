"""
lora_signal_test.py -- measures the LoRa link: RSSI and SNR of every packet the Pi hears.

Run this instead of anomaly_engine.py (both can't own the radio at once) while walking the node
around, moving the antenna, or checking a site before installing. The radio is set up exactly as
anomaly_engine.py's init_radio() does (434 MHz, SF12, 125 kHz, CR 4/8, preamble 12, sync word 0x12, CRC on,
PA_BOOST), and by default it answers each packet with the same "ACK,HH:MM:SS" the engine sends,
so the ESP32 keeps working normally (and its own screen shows TX OK) during the test.

Per packet it prints:
  RSSI   received signal strength in dBm (closer to 0 = stronger). When SNR is negative the
         datasheet's correction (RSSI + SNR) is applied, since the raw value then overstates it.
  SNR    signal-to-noise ratio in dB. LoRa decodes below 0 dB; at SF12 the limit is about -20 dB.
  noise  the channel's background RSSI, sampled while no packet is arriving.
  lost   live packets missing from the node's ID sequence since the previous one.
LOG packets (readings the node stored while the Pi was unreachable, sent later with an Age) are
ACKed like the engine does, labelled with their age, and kept out of the loss count; one that fills
a gap left by a missed live packet is counted as recovered.
Ctrl+C prints min / average / max and packet loss per node.

Usage:
    python lora_signal_test.py                   # listen, reply with ACKs
    python lora_signal_test.py --no-ack          # listen only (the ESP32 will show NO ACK)
    python lora_signal_test.py --csv walk1.csv   # also log every packet to a CSV file
"""
import argparse
import csv
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime

# The patched pySX127x in this repo (SX127x/SX127x), not a pip-installed pyLoRa -- see anomaly_engine.py
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "SX127x"))
from SX127x.LoRa import LoRa, MODE, BW, CODING_RATE
from SX127x.board_config import BOARD

# Same values as anomaly_engine.py
RADIO_FREQ_MHZ = 434.0
RH_BROADCAST_ADDRESS = 0xFF
GATEWAY_ADDRESS = 0x01
TX_TURNAROUND_S = 0.15
TX_DONE_TIMEOUT_S = 4.0          # an ACK takes ~1.8 s on air at SF12 / CR 4/8, preamble 12
REG_PA_CONFIG = 0x09
REG_FIFO_ADDR_PTR = 0x0D
REG_FIFO_TX_BASE_ADDR = 0x0E
REG_MODEM_CONFIG_2 = 0x1E
REG_PAYLOAD_LENGTH = 0x22

SNR_LIMIT_DB = -20.0             # below this an SF12 packet can't be demodulated
NOISE_SAMPLE_S = 1.0             # how often to sample the idle channel's RSSI

NODE_RE = re.compile(r"NODE:(\d+)")
ID_RE = re.compile(r"\|ID:(\d+)")
AGE_RE = re.compile(r"\|Age:(\d+)")


class SignalRadio(LoRa):
    """Receive + ACK like anomaly_engine.py's LoRaGateway, keeping the per-packet RSSI/SNR."""

    def __init__(self):
        super().__init__(verbose=False)
        self._tx_id = 0
        self.set_mode(MODE.SLEEP)
        self.set_dio_mapping([0] * 6)

    def receive(self):
        """A packet if one arrived: (text, rssi_dbm, snr_db, crc_ok); else None."""
        flags = self.get_irq_flags()
        if not flags.get("rx_done"):
            return None
        crc_ok = not flags.get("crc_error")
        # Read the packet's signal values before anything else touches the radio
        snr = self.get_pkt_snr_value()
        rssi = self.get_pkt_rssi_value()
        if snr < 0:
            rssi += snr               # SX1276 datasheet 5.5.5: PacketRssi overstates below the noise floor
        payload = self.read_payload(nocheck=True)
        self.clear_irq_flags(RxDone=1, PayloadCrcError=1, ValidHeader=1)
        self.set_mode(MODE.RXCONT)
        text = bytes(payload[4:]).decode("utf-8", "ignore").strip() if payload else ""
        return text, rssi, snr, crc_ok

    def transmit(self, text: str):
        """Copied from anomaly_engine.py's LoRaGateway.transmit (RadioHead header + TxDone wait)."""
        self._tx_id = (self._tx_id + 1) % 256
        payload = [RH_BROADCAST_ADDRESS, GATEWAY_ADDRESS, self._tx_id, 0]
        payload.extend(text.encode("utf-8"))
        self.set_mode(MODE.STDBY)
        time.sleep(0.01)
        self.set_dio_mapping([1, 0, 0, 0, 0, 0])      # DIO0 -> TxDone
        self.set_register(REG_FIFO_ADDR_PTR, self.get_register(REG_FIFO_TX_BASE_ADDR))
        self.set_register(REG_PAYLOAD_LENGTH, len(payload))
        self.write_payload(payload)
        self.set_mode(MODE.TX)
        sent = False
        deadline = time.time() + TX_DONE_TIMEOUT_S
        while time.time() < deadline:
            if self.get_irq_flags().get("tx_done"):
                sent = True
                break
            time.sleep(0.005)
        self.clear_irq_flags(TxDone=1)
        time.sleep(0.05)
        self.set_dio_mapping([0] * 6)                 # DIO0 -> RxDone
        self.reset_ptr_rx()
        self.set_mode(MODE.RXCONT)
        return sent


def init_radio():
    """Same reset, pins and modem settings as anomaly_engine.py's init_radio()."""
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(22, GPIO.OUT)
    GPIO.output(22, GPIO.LOW)
    time.sleep(0.05)
    GPIO.output(22, GPIO.HIGH)
    time.sleep(0.05)

    BOARD.setup()
    BOARD.DIO0 = 4
    BOARD.SpiDev(spi_bus=0, spi_cs=0)

    radio = SignalRadio()
    radio.set_freq(RADIO_FREQ_MHZ)
    radio.set_spreading_factor(12)
    radio.set_bw(BW.BW125)
    radio.set_coding_rate(CODING_RATE.CR4_8)
    radio.set_low_data_rate_optim(True)              # required at SF12 / 125 kHz
    radio.set_sync_word(0x12)
    radio.set_preamble(12)
    radio.set_rx_crc(True)
    radio.set_register(REG_PA_CONFIG, 0x8F)          # PA_BOOST
    radio.set_register(REG_MODEM_CONFIG_2, radio.get_register(REG_MODEM_CONFIG_2) | 0x04)   # TX CRC
    radio.reset_ptr_rx()
    radio.set_mode(MODE.RXCONT)
    return radio


def rating(rssi: float, snr: float) -> str:
    """A rough label for one packet, for reading the output at a glance."""
    if snr < SNR_LIMIT_DB + 2.5:
        return "AT LIMIT"            # within 2.5 dB of failing to decode
    if rssi > -90 and snr > 5:
        return "excellent"
    if rssi > -105 and snr > 0:
        return "good"
    return "fair"


def engine_running() -> bool:
    result = subprocess.run(["pgrep", "-f", "anomaly_engine.py"], capture_output=True, text=True)
    return any(pid.strip() and int(pid) != os.getpid() for pid in result.stdout.split())


def summarize(stats: dict, crc_errors: int, noise: list):
    print("\n" + "=" * 72)
    if noise:
        print(f"Noise floor: avg {statistics.mean(noise):.0f} dBm "
              f"(min {min(noise):.0f}, max {max(noise):.0f}) over {len(noise)} samples")
    if crc_errors:
        print(f"Packets with CRC errors (corrupted, not counted below): {crc_errors}")
    if not stats:
        print("No packets received. Check the node is powered, on 434 MHz, and the antenna is attached.")
        return
    for node, s in sorted(stats.items()):
        lost = len(s["missing"])
        expected = s["received"] + lost
        loss = 100 * lost / expected if expected else 0
        print(f"Node {node}: {s['received']} live packets, {lost} lost ({loss:.1f}% loss)")
        if s["logs"]:
            print(f"  {s['logs']} stored (LOG) readings received, {s['recovered']} of them filling a gap "
                  f"from a missed live packet")
        print(f"  RSSI dBm  min {min(s['rssi']):6.1f}   avg {statistics.mean(s['rssi']):6.1f}   max {max(s['rssi']):6.1f}")
        print(f"  SNR  dB   min {min(s['snr']):6.1f}   avg {statistics.mean(s['snr']):6.1f}   max {max(s['snr']):6.1f}")
        margin = min(s["snr"]) - SNR_LIMIT_DB
        print(f"  Worst-case margin above the SF12 decode limit: {margin:.1f} dB")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-ack", action="store_true", help="only listen; don't answer packets")
    parser.add_argument("--csv", metavar="FILE", help="also append every packet to this CSV file")
    parser.add_argument("--force", action="store_true", help="run even if anomaly_engine.py is running")
    args = parser.parse_args()

    if engine_running() and not args.force:
        sys.exit("anomaly_engine.py is running and owns the radio. Stop it first:\n"
                 "    sudo systemctl stop anomaly_engine     (or Ctrl+C where you started it)\n"
                 "then run this again. Start it afterwards with: sudo systemctl start anomaly_engine")

    radio = init_radio()
    csv_file = None
    writer = None
    if args.csv:
        new_file = not os.path.exists(args.csv)
        csv_file = open(args.csv, "a", newline="", encoding="utf-8")
        writer = csv.writer(csv_file)
        if new_file:
            writer.writerow(["time", "node", "packet_id", "rssi_dbm", "snr_db", "noise_dbm", "crc_ok", "payload"])

    stats = {}                    # node -> {"rssi", "snr", "received", "missing" (live IDs not heard), "last_id", "logs", "recovered"}
    crc_errors = 0
    noise = []
    last_noise_at = 0.0
    current_noise = None

    print(f"Listening on {RADIO_FREQ_MHZ} MHz, SF12, BW125, CR4/8 "
          f"({'listen only' if args.no_ack else 'replying with ACKs'}). Ctrl+C to stop.\n")
    print(f"{'time':8}  {'node':>4}  {'pkt':>6}  {'RSSI':>7}  {'SNR':>6}  {'noise':>6}  {'lost':>4}  quality")
    try:
        while True:
            packet = radio.receive()
            now = time.time()
            if packet is None:
                if now - last_noise_at >= NOISE_SAMPLE_S:
                    current_noise = radio.get_rssi_value()
                    noise.append(current_noise)
                    last_noise_at = now
                time.sleep(0.01)
                continue

            text, rssi, snr, crc_ok = packet
            stamp = datetime.now().strftime("%H:%M:%S")
            if not crc_ok:
                crc_errors += 1
                print(f"{stamp:8}  {'?':>4}  {'?':>6}  {rssi:7.1f}  {snr:6.1f}  "
                      f"{current_noise if current_noise is not None else '':>6}  {'':>4}  CRC ERROR (corrupted)")
                if writer:
                    writer.writerow([stamp, "", "", rssi, snr, current_noise, 0, text])
                continue

            node_match = NODE_RE.search(text)
            node = int(node_match.group(1)) if node_match else None
            id_match = ID_RE.search(text)
            packet_id = int(id_match.group(1)) if id_match else None

            is_log = text.startswith("LOG|")
            lost = 0
            if node is not None:
                s = stats.setdefault(node, {"rssi": [], "snr": [], "received": 0, "missing": set(),
                                            "last_id": None, "logs": 0, "recovered": 0})
                # Signal strength counts for every packet type: it's the same radio link
                s["rssi"].append(rssi)
                s["snr"].append(snr)
                if is_log:
                    # An old ID: never part of the live sequence. Fills a gap if that live packet was missed.
                    s["logs"] += 1
                    if packet_id in s["missing"]:
                        s["missing"].discard(packet_id)
                        s["recovered"] += 1
                elif text.startswith("DATA|"):
                    s["received"] += 1
                    if packet_id is not None:
                        # A smaller ID means the node restarted; don't count that as loss
                        if s["last_id"] is not None and packet_id > s["last_id"]:
                            gap = range(s["last_id"] + 1, packet_id)
                            lost = len(gap)
                            s["missing"].update(gap)
                        s["last_id"] = packet_id

            label = rating(rssi, snr)
            if text.startswith("SYNC_REQ"):
                label += " (sync request)"
            elif is_log:
                age_match = AGE_RE.search(text)
                age = f", measured {int(age_match.group(1)) // 60} min {int(age_match.group(1)) % 60} s ago" if age_match else ""
                label += f" (stored reading{age})"
            print(f"{stamp:8}  {node if node is not None else '?':>4}  {packet_id if packet_id is not None else '-':>6}  "
                  f"{rssi:7.1f}  {snr:6.1f}  {current_noise if current_noise is not None else '':>6}  "
                  f"{lost if lost else '':>4}  {label}")
            if writer:
                writer.writerow([stamp, node, packet_id, rssi, snr, current_noise, 1, text])
                csv_file.flush()

            if not args.no_ack and text.startswith(("DATA|", "LOG|", "SYNC_REQ")):
                time.sleep(TX_TURNAROUND_S)   # let the ESP32 switch from TX back to RX
                radio.transmit(f"ACK,{datetime.now().strftime('%H:%M:%S')}")
    except KeyboardInterrupt:
        pass
    finally:
        summarize(stats, crc_errors, noise)
        if csv_file:
            csv_file.close()
        radio.set_mode(MODE.SLEEP)
        BOARD.teardown()


if __name__ == "__main__":
    main()
