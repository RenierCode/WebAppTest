"""
test_transmit.py -- isolates JUST the Pi's transmit path for debugging.

Run this instead of the full edge_engine.py while chasing the "ESP32 never
gets the ACK" problem. It sends a plain test string every 3 seconds and
prints exactly what happened at each step. On the ESP32 side, you don't
even need real sensor firmware running -- any sketch that does
rf95.recv() and prints what it got will do, including the real
wastewater_node.ino (it'll just log unexpected ACKs it wasn't waiting for,
harmless).

Usage:
    python test_transmit.py
"""
import os
import sys
import time

# The patched pySX127x in this repo (SX127x/SX127x), not a pip-installed pyLoRa -- see anomaly_engine.py
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "SX127x"))
from SX127x.LoRa import LoRa, MODE, BW, CODING_RATE
from SX127x.board_config import BOARD

RADIO_FREQ_MHZ = 434.0
RH_BROADCAST_ADDRESS = 0xFF
GATEWAY_ADDRESS = 0x01


class TestRadio(LoRa):
    def __init__(self, verbose=True):
        super(TestRadio, self).__init__(verbose)
        self._tx_id = 0
        self.set_mode(MODE.SLEEP)
        self.set_dio_mapping([0] * 6)

    def transmit(self, text: str):
        self._tx_id = (self._tx_id + 1) % 256
        header = [RH_BROADCAST_ADDRESS, GATEWAY_ADDRESS, self._tx_id, 0]
        body = list(text.encode("utf-8"))
        payload = header + body

        print(f"\n--- Sending: {text!r} (tx_id={self._tx_id}) ---")
        print(f"Raw bytes going into FIFO: {payload}")

        self.set_mode(MODE.STDBY)
        time.sleep(0.01)
        print("Mode after STDBY switch:", self.get_mode())

        if hasattr(self, "reset_ptr_tx"):
            self.reset_ptr_tx()
            print("Called reset_ptr_tx()")
        else:
            print("WARNING: this library has no reset_ptr_tx() method -- write_payload() must handle it internally, or this is the bug.")

        self.write_payload(payload)
        self.set_mode(MODE.TX)
        print("Mode after TX switch:", self.get_mode())

        deadline = time.time() + 2.0
        sent = False
        poll_count = 0
        while time.time() < deadline:
            flags = self.get_irq_flags()
            poll_count += 1
            if flags.get("tx_done"):
                sent = True
                break
            time.sleep(0.01)

        print(f"Polled irq_flags {poll_count} times over up to 2s")
        print(f"Final irq_flags snapshot: {self.get_irq_flags()}")
        print(f"tx_done observed: {sent}")

        self.clear_irq_flags(TxDone=1)
        self.set_mode(MODE.RXCONT)
        return sent


def main():
    BOARD.setup()
    BOARD.RST = 22
    BOARD.DIO0 = 4
    try:
        BOARD.SpiDev(spi_bus=0, spi_cs=0)
    except Exception as e:
        print("BOARD.SpiDev() error:", e)
    BOARD.reset_module(hold_high=True, pulse=True, pulse_ms=50)
    time.sleep(0.05)

    radio = TestRadio(verbose=False)
    radio.set_freq(RADIO_FREQ_MHZ)
    radio.set_spreading_factor(12)
    radio.set_bw(BW.BW125)
    radio.set_coding_rate(CODING_RATE.CR4_8)
    radio.set_low_data_rate_optim(True)              # required at SF12 / 125 kHz
    radio.set_sync_word(0x12)
    radio.set_preamble(12)
    radio.set_rx_crc(True)

    print(f"SX1278 version: {hex(radio.get_version())}")
    print(f"Radio configured at {RADIO_FREQ_MHZ} MHz. Sending a test packet every 3s. Ctrl+C to stop.")
    print("Watch your ESP32's Serial Monitor for ANYTHING arriving, even if it's not a packet type it expects.\n")

    i = 0
    try:
        while True:
            i += 1
            radio.transmit(f"TEST_PACKET_{i}")
            time.sleep(3)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        radio.set_mode(MODE.SLEEP)
        BOARD.teardown()


if __name__ == "__main__":
    main()
