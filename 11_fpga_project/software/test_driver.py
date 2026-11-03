"""Offline protocol/recovery tests. No PYNQ package or FPGA is required."""
import unittest
import weakref
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from laser_stripe import *
from static_image_demo import compare, load_sample, PROJECT_ROOT


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.001)


class Buffer(np.ndarray):
    def __new__(cls, shape, dtype, address, log):
        obj = np.empty(shape, dtype).view(cls)
        obj.device_address, obj.log, obj.freed = address, log, False
        return obj

    def flush(self):
        self.log.append(("flush", self.device_address))

    def invalidate(self):
        self.log.append(("invalidate", self.device_address))

    def freebuffer(self):
        if self.freed:
            raise AssertionError("double free")
        self.log.append(("free", self.device_address))
        self.freed = True


class FakeBoard:
    def __init__(self):
        self.log, self.buffers, self.buffer_refs = [], {}, []
        self.ip = FakeIP(self)
        self.dma = FakeDmaMMIO(self)
        self.clock = FakeClock()
        self.output = np.zeros(WIDTH, np.uint32)
        self.output[100] = 0x80000000 | int(200.5 * 256)
        self.hang, self.reset_hang, self.core_hang = False, False, False
        self.core_error, self.dma_error, self.short_receive = 0, False, False
        self.skip_output = False
        self.start_failure = False
        self.alloc_count = 0

    def allocate(self, shape, dtype):
        self.alloc_count += 1
        addr = 0x100000 + self.alloc_count * 0x200000
        buf = Buffer(shape, dtype, addr, self.log)
        self.buffers[addr] = weakref.ref(buf)
        self.buffer_refs.append(weakref.ref(buf))
        return buf

    def driver(self):
        return LaserStripe(self.ip, SimpleAxiDma(self.dma), self.allocate,
                           timeout=0.005, recovery_timeout=0.004,
                           poll_interval=0.001, clock=self.clock, sleep=self.clock.sleep)

    def finish(self):
        if self.hang:
            return
        if self.core_error:
            self.ip.values[STATUS] = ERROR | (self.core_error << 8)
            return
        if self.dma_error:
            self.dma.values[SimpleAxiDma.S2MM_SR] = 0x20
            return
        if not self.skip_output:
            self.buffers[self.dma.values[SimpleAxiDma.S2MM_ADDR]]()[:] = self.output
        self.ip.values[STATUS] = DONE
        self.ip.values[COMPLETED_FRAMES] = (self.ip.values[COMPLETED_FRAMES] + 1) & 0xFFFFFFFF
        self.ip.values[CYCLES_LOW] = 12345
        self.dma.values[SimpleAxiDma.MM2S_SR] = 0x1002
        self.dma.values[SimpleAxiDma.S2MM_SR] = 0x1002
        if self.short_receive:
            self.dma.values[SimpleAxiDma.S2MM_LENGTH] = OUTPUT_BYTES - 4


class FakeIP:
    def __init__(self, board):
        self.board = board
        self.values = {ID: IP_ID, VERSION: IP_VERSION, REG_INPUT_BYTES: INPUT_BYTES,
                       REG_OUTPUT_BYTES: OUTPUT_BYTES, REG_WIDTH: WIDTH, REG_HEIGHT: HEIGHT,
                       STATUS: 0, COMPLETED_FRAMES: 0, CYCLES_LOW: 0, CYCLES_HIGH: 0}

    def read(self, offset):
        return self.values.get(offset, 0)

    def write(self, offset, value):
        self.board.log.append(("ip", offset, value))
        if offset != CONTROL:
            self.values[offset] = value
            return
        if value == CLEAR:
            self.values[STATUS] &= BUSY | RESETTING
        elif value == ABORT:
            self.values[STATUS] = BUSY | RESETTING if self.board.core_hang else 0
        elif value == START:
            if self.board.start_failure:
                raise OSError("AXI SLVERR at START")
            if self.values[STATUS] & BUSY:
                raise OSError("START busy")
            self.values[STATUS] = BUSY


class FakeDmaMMIO:
    def __init__(self, board):
        self.board = board
        self.values = {0: 0, 4: 1, 0x30: 0, 0x34: 1, 0x58: 0}

    def read(self, offset):
        return self.values.get(offset, 0)

    def write(self, offset, value):
        self.board.log.append(("dma", offset, value))
        if offset in (4, 0x34):
            self.values[offset] &= ~(value & 0x7000)
            return
        self.values[offset] = value
        if offset in (0, 0x30):
            if value & 4:
                if not self.board.reset_hang:
                    self.values.update({0: 0, 4: 1, 0x30: 0, 0x34: 1})
            elif value & 1:
                self.values[offset + 4] = 2  # Running, no transaction yet.
        elif offset == 0x58:
            self.values[0x34] = 0
        elif offset == 0x28:
            self.values[4] = 0
            self.board.finish()


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.board = FakeBoard()
        self.pixels = np.zeros((HEIGHT, WIDTH), np.uint32)

    def test_receive_before_start_before_send_and_cache_order(self):
        driver = self.board.driver()
        result = driver.run_words(self.pixels)
        log = self.board.log
        recv = next(i for i, e in enumerate(log) if e == ("dma", 0x58, OUTPUT_BYTES))
        start = log.index(("ip", CONTROL, START))
        send = log.index(("dma", 0x28, INPUT_BYTES))
        self.assertLess(recv, start)
        self.assertLess(start, send)
        self.assertTrue(all(e[0] != "free" for e in log[:send]))
        self.assertEqual(sum(e[0] == "flush" for e in log[:recv]), 2)
        invalidate = next(i for i, e in enumerate(log) if e[0] == "invalidate")
        self.assertGreater(invalidate, send)
        self.assertTrue(all(e[0] != "free" for e in log[:invalidate]))
        self.assertEqual(result.y[100], 200.5)
        self.assertTrue(np.isnan(result.y[0]))
        self.assertEqual(result.completed_frames, 1)
        self.assertEqual(result.cycles, 12345)
        self.assertEqual(sum(e[0] == "free" for e in log), 2)

    def test_repeated_frames_clear_stale_output_and_framecount_wrap(self):
        driver = self.board.driver()
        self.board.ip.values[COMPLETED_FRAMES] = 0xFFFFFFFE
        a = driver.run_words(self.pixels)
        self.board.output[:] = 0
        b = driver.run_words(self.pixels)
        self.assertEqual(a.words[100], 0x8000C880)
        self.assertEqual(b.valid.sum(), 0)
        self.assertEqual(b.completed_frames, 0)
        self.assertEqual(a.words[100], 0x8000C880)  # Independent result copy.

    def test_timeout_abort_and_reset_both_before_free_then_reuse(self):
        driver = self.board.driver()
        self.board.hang = True
        with self.assertRaises(LaserStripeTimeout) as caught:
            driver.run_words(self.pixels)
        log = self.board.log
        abort = log.index(("ip", CONTROL, ABORT))
        reset_tx, reset_rx = log.index(("dma", 0, 4)), log.index(("dma", 0x30, 4))
        first_free = next(i for i, e in enumerate(log) if e[0] == "free")
        self.assertLess(abort, reset_tx)
        self.assertLess(reset_tx, first_free)
        self.assertLess(reset_rx, first_free)
        self.assertTrue(caught.exception.diagnostics["recovered"])
        self.assertTrue(driver.healthy)
        self.assertLess(self.board.clock.now, 0.020)
        self.board.hang = False
        self.assertEqual(driver.run_words(self.pixels).valid.sum(), 1)

    def test_failed_dma_reset_retains_buffers_and_blocks_reuse(self):
        driver = self.board.driver()
        self.board.hang = self.board.reset_hang = True
        with self.assertRaises(RecoveryError):
            driver.run_words(self.pixels)
        self.assertFalse(driver.healthy)
        self.assertEqual(driver.quarantined_buffer_count, 2)
        self.assertFalse(any(e[0] == "free" for e in self.board.log))
        self.assertTrue(all(r() is not None for r in self.board.buffer_refs))
        count = len(self.board.log)
        with self.assertRaises(RecoveryError):
            driver.run_words(self.pixels)
        self.assertEqual(len(self.board.log), count)
        self.board.reset_hang = False
        driver.recover()
        self.assertTrue(driver.healthy)
        self.assertEqual(driver.quarantined_buffer_count, 0)
        self.assertEqual(sum(e[0] == "free" for e in self.board.log), 2)

    def test_core_reset_failure_still_resets_both_dma(self):
        driver = self.board.driver()
        self.board.hang = self.board.core_hang = True
        with self.assertRaises(RecoveryError):
            driver.run_words(self.pixels)
        self.assertIn(("dma", 0, 4), self.board.log)
        self.assertIn(("dma", 0x30, 4), self.board.log)
        self.assertEqual(driver.quarantined_buffer_count, 2)
        self.board.core_hang = False
        driver.recover()

    def test_start_bus_error_recovers_armed_receive(self):
        driver = self.board.driver()
        self.board.start_failure = True
        with self.assertRaises(LaserStripeError) as caught:
            driver.run_words(self.pixels)
        self.assertTrue(caught.exception.diagnostics["recovered"])
        self.assertIn(("dma", 0x30, 4), self.board.log)

    def test_hardware_core_error_diagnostics_and_recovery(self):
        driver = self.board.driver()
        self.board.core_error = 3
        with self.assertRaisesRegex(LaserStripeError, "error_code=3") as caught:
            driver.run_words(self.pixels)
        self.assertEqual(caught.exception.diagnostics["before_recovery"]["status"], 0x304)
        self.assertTrue(driver.healthy)

    def test_dma_bus_error_recovery(self):
        driver = self.board.driver()
        self.board.dma_error = True
        with self.assertRaisesRegex(LaserStripeError, "DMA hardware error"):
            driver.run_words(self.pixels)
        self.assertIn(("ip", CONTROL, ABORT), self.board.log)

    def test_short_dma_receive_and_missing_writes_rejected(self):
        driver = self.board.driver()
        self.board.short_receive = True
        with self.assertRaisesRegex(LaserStripeError, "received 2556"):
            driver.run_words(self.pixels)
        self.board.short_receive = False
        self.board.skip_output = True
        with self.assertRaisesRegex(LaserStripeError, "reserved bits"):
            driver.run_words(self.pixels)

    def test_busy_or_resetting_rejected_without_writes(self):
        driver = self.board.driver()
        for state in (BUSY, RESETTING):
            self.board.ip.values[STATUS] = state
            with self.assertRaisesRegex(LaserStripeError, "busy or resetting"):
                driver.run_words(self.pixels)
            self.assertEqual(self.board.log, [])
            self.assertEqual(self.board.alloc_count, 0)

    def test_other_dma_transfer_rejected_without_mutation(self):
        driver = self.board.driver()
        self.board.dma.values[4] = 0
        with self.assertRaisesRegex(LaserStripeError, "already transferring"):
            driver.run_words(self.pixels)
        self.assertEqual(self.board.log, [])

    def test_identity_and_length_mismatches_rejected(self):
        for register in (ID, VERSION, REG_WIDTH, REG_HEIGHT, REG_INPUT_BYTES, REG_OUTPUT_BYTES):
            with self.subTest(register=register):
                board = FakeBoard()
                board.ip.values[register] ^= 1
                with self.assertRaisesRegex(LaserStripeError, "IP register"):
                    board.driver()
                self.assertEqual(board.log, [])

    def test_sg_and_short_length_width_rejected(self):
        self.board.dma.values[4] = 9
        with self.assertRaisesRegex(LaserStripeError, "scatter/gather"):
            self.board.driver().run_words(self.pixels)
        with self.assertRaises(ValueError):
            SimpleAxiDma(self.board.dma, length_bits=20)

    def test_config_types_bounds_no_silent_truncation(self):
        bad = ({"x0": -1}, {"x1": 641}, {"y1": 481}, {"x0": 640}, {"x1": 0},
               {"x0": 4, "x1": 4}, {"y0": 4, "y1": 4}, {"min_run": 7},
               {"min_run": 9}, {"threshold_q8": 65536}, {"threshold_q8": -1},
               {"x0": 0.0}, {"y0": True}, {"min_run": np.bool_(True)},
               {"threshold_q8": "3072"})
        for params in bad:
            with self.subTest(params=params), self.assertRaises((TypeError, ValueError)):
                StripeConfig(**params)
        self.assertEqual(StripeConfig(threshold_q8=65535, min_run=0).threshold_q8, 65535)
        self.assertEqual(StripeConfig(x0=np.int64(3)).x0, 3)
        with self.assertRaises(TypeError):
            self.board.driver().run_words(self.pixels, {"x0": 1})
        self.assertEqual(self.board.log, [])

    def test_pixel_validation_and_bgr_pack(self):
        bgr = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        bgr[0, 0] = [0x12, 0x34, 0x56]
        packed = pack_bgr(bgr)
        self.assertEqual(packed[0, 0], 0x00563412)
        driver = self.board.driver()
        driver.run_bgr(bgr, StripeConfig(min_run=0))
        for pixels in (self.pixels.astype(np.int32), self.pixels.astype(np.uint64),
                       self.pixels[:10], [0] * (WIDTH * HEIGHT)):
            with self.assertRaises((TypeError, ValueError)):
                validate_words(pixels)
        self.pixels[0, 0] = 0xFF000000
        with self.assertRaises(ValueError):
            validate_words(self.pixels)
        with self.assertRaises(TypeError):
            pack_bgr(bgr.astype(float))
        with self.assertRaises(ValueError):
            pack_bgr(bgr[:10])

    def test_output_format_checks(self):
        for word in (0x00000001, 0x80040000, 0x80000000 | (HEIGHT * 256)):
            with self.subTest(word=word):
                output = np.zeros(WIDTH, np.uint32)
                output[0] = word
                with self.assertRaises(LaserStripeError):
                    decode_words(output)
        with self.assertRaises(ValueError):
            decode_words(np.zeros(WIDTH - 1, np.uint32))
        output = np.zeros(WIDTH, np.uint32)
        output[0] = 0x80000000
        valid, y = decode_words(output)
        self.assertTrue(valid[0])
        self.assertEqual(y[0], 0)

    def test_timeout_arguments(self):
        for limit in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(limit=limit), self.assertRaises((ValueError, TypeError)):
                self.board.driver().run_words(self.pixels, timeout=limit)
        self.assertEqual(self.board.log, [])

    def test_dma_address_validation_before_transfer(self):
        adapter = SimpleAxiDma(self.board.dma)
        buf = Buffer((WIDTH,), np.uint32, 3, self.board.log)
        with self.assertRaises(ValueError):
            adapter.arm_receive(buf, OUTPUT_BYTES)
        buf.device_address = 0xFFFFFFFC
        with self.assertRaises(ValueError):
            adapter.arm_receive(buf, OUTPUT_BYTES)
        self.assertEqual(self.board.log, [])

    def test_frozen_sample_loading_and_comparison(self):
        pixels, config, expected = load_sample(PROJECT_ROOT / "handoff", "flat_zero.png", "full")
        self.assertEqual(pixels.nbytes, INPUT_BYTES)
        self.assertEqual((config.x0, config.y0, config.x1, config.y1), (155, 235, 355, 300))
        self.assertEqual(config.min_run, 8)
        self.assertTrue(compare(expected, expected)["passed"])
        damaged = expected.copy()
        column = np.flatnonzero(expected >> 31)[0]
        damaged[column] = 0
        self.assertFalse(compare(damaged, expected)["passed"])

    def test_all_46_frozen_configurations_dry_run(self):
        handoff = PROJECT_ROOT / "handoff"
        manifest = json.loads((handoff / "manifest.json").read_text(encoding="utf-8"))
        count = 0
        for sample in manifest["samples"]:
            for mode in ("core", "full"):
                with self.subTest(sample=sample["name"], mode=mode):
                    pixels, config, expected = load_sample(handoff, sample["name"], mode)
                    self.assertEqual(pixels.size, WIDTH * HEIGHT)
                    self.assertEqual(config.min_run, 0 if mode == "core" else 8)
                    self.assertTrue(compare(expected, expected)["passed"])
                    count += 1
        self.assertEqual(count, 46)

    def test_from_overlay_uses_public_mmio_and_actual_instance_names(self):
        class Overlay:
            def __getattr__(self, name):
                raise AssertionError("native PYNQ IP constructors must not be invoked: " + name)

        overlay = Overlay()
        overlay.ip_dict = {
            "laser_stripe_0": {"phys_addr": 0x43C00000, "addr_range": 65536},
            "axi_dma_0": {"phys_addr": 0x40400000, "addr_range": 65536,
                          "parameters": {"c_include_sg": "0", "c_sg_length_width": "22",
                                         "c_addr_width": "32", "c_include_mm2s": "1",
                                         "c_include_s2mm": "1"}}}
        maps = {0x43C00000: self.board.ip, 0x40400000: self.board.dma}
        factory = SimpleNamespace(allocate=self.board.allocate,
                                  MMIO=lambda address, size: maps[address])
        with patch.dict(sys.modules, {"pynq": factory}):
            driver = LaserStripe.from_overlay(overlay)
            self.assertEqual(driver.dma.length_bits, 22)  # Preserve actual, nondefault HWH width.
            self.assertEqual(driver.run_words(self.pixels).valid.sum(), 1)
            for key, invalid in (("c_include_sg", "1"), ("c_addr_width", "64"),
                                 ("c_include_s2mm", "0"), ("c_sg_length_width", "20")):
                parameters = overlay.ip_dict["axi_dma_0"]["parameters"]
                previous = parameters.get(key)
                parameters[key] = invalid
                with self.assertRaises((LaserStripeError, ValueError)):
                    LaserStripe.from_overlay(overlay)
                if previous is None:
                    del parameters[key]
                else:
                    parameters[key] = previous
            del parameters["c_sg_length_width"]
            with self.assertRaisesRegex(LaserStripeError, "missing"):
                LaserStripe.from_overlay(overlay)
            parameters["c_sg_length_width"] = "23"
            overlay.ip_dict["axi_dma_0"]["parameters"] = {k.upper(): v for k, v in parameters.items()}
            self.assertEqual(LaserStripe.from_overlay(overlay).dma.length_bits, 23)

    def test_second_allocation_failure_frees_first_without_dma(self):
        first = None

        def allocator(**kwargs):
            nonlocal first
            if first is None:
                first = self.board.allocate(**kwargs)
                return first
            raise MemoryError("second allocation failed")

        driver = LaserStripe(self.board.ip, SimpleAxiDma(self.board.dma), allocator)
        with self.assertRaises(MemoryError):
            driver.run_words(self.pixels)
        self.assertTrue(first.freed)
        self.assertFalse(any(e[0] == "dma" for e in self.board.log))

    def test_keyboard_interrupt_during_poll_recovers_before_release(self):
        driver = self.board.driver()
        self.board.hang = True
        with patch.object(driver.dma, "completed", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                driver.run_words(self.pixels)
        self.assertIn(("dma", 0, 4), self.board.log)
        self.assertIn(("dma", 0x30, 4), self.board.log)
        self.assertEqual(sum(e[0] == "free" for e in self.board.log), 2)

    def test_dma_reset_attempts_second_channel_after_first_write_error(self):
        original = self.board.dma.write

        def write(offset, value):
            if offset == 0 and value == 4:
                raise OSError("MM2S control write fault")
            return original(offset, value)

        adapter = SimpleAxiDma(self.board.dma)
        with patch.object(self.board.dma, "write", side_effect=write):
            adapter.reset(0.004, self.board.clock, self.board.clock.sleep, 0.001)
        self.assertIn(("dma", 0x30, 4), self.board.log)
        self.assertEqual(self.board.dma.read(4) & 1, 1)
        self.assertEqual(self.board.dma.read(0x34) & 1, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
