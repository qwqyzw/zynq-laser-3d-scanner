"""Bounded, synchronous host driver for laser_stripe_axi v1.0.

PYNQ is imported only by ``LaserStripe.from_overlay``. The DMA adapter owns
the complete simple-mode DMA instance; never mix it with channel.transfer().
"""
from dataclasses import dataclass
from numbers import Integral
import math
import time

import numpy as np


WIDTH, HEIGHT = 640, 480
INPUT_BYTES, OUTPUT_BYTES = 1228800, 2560
CONTROL, STATUS, ID, VERSION = 0x00, 0x04, 0x08, 0x0C
X0, Y0, X1, Y1, THRESHOLD_Q8, MIN_RUN = 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24
REG_INPUT_BYTES, REG_OUTPUT_BYTES, REG_WIDTH, REG_HEIGHT = 0x28, 0x2C, 0x30, 0x34
COMPLETED_FRAMES, CYCLES_LOW, CYCLES_HIGH = 0x38, 0x3C, 0x40
START, ABORT, CLEAR = 1, 2, 4
BUSY, DONE, ERROR, RESETTING = 1, 2, 4, 8
IP_ID, IP_VERSION = 0x4C535431, 0x00010000

# Strong references intentionally survive driver garbage collection after an
# unconfirmed DMA reset. Only recover() with verified quiescence releases them.
_UNQUIESCED_BUFFERS = {}


class LaserStripeError(RuntimeError):
    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class LaserStripeTimeout(LaserStripeError):
    pass


class RecoveryError(LaserStripeError):
    """DMA may still access retained physical buffers. Do not resume work."""


def _integer(name, value, low, high):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
        raise TypeError(f"{name} must be an integer")
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"{name} must be in [{low}, {high}]")
    return value


def _duration(name, value, *, zero_ok=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (value == 0 and not zero_ok):
        raise ValueError(f"{name} must be {'nonnegative' if zero_ok else 'positive'} and finite")
    return value


@dataclass(frozen=True)
class StripeConfig:
    x0: int = 0
    y0: int = 0
    x1: int = WIDTH
    y1: int = HEIGHT
    threshold_q8: int = 3072
    min_run: int = 8

    def __post_init__(self):
        for name, high in (("x0", WIDTH - 1), ("y0", HEIGHT - 1),
                           ("x1", WIDTH), ("y1", HEIGHT),
                           ("threshold_q8", 65535), ("min_run", 8)):
            object.__setattr__(self, name, _integer(name, getattr(self, name), 0, high))
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("ROI must satisfy x0 < x1 and y0 < y1; upper bounds are exclusive")
        if self.min_run not in (0, 8):
            raise ValueError("min_run must be 0 (core) or 8 (full)")


def pack_bgr(image):
    """Pack an OpenCV BGR uint8[480,640,3] image into uint32[480,640]."""
    if not isinstance(image, np.ndarray) or image.dtype != np.dtype(np.uint8):
        raise TypeError("BGR image must be a numpy uint8 array")
    if image.shape != (HEIGHT, WIDTH, 3):
        raise ValueError("BGR image shape must be (480, 640, 3)")
    bgr = image.astype(np.uint32)
    return (bgr[:, :, 2] << 16) | (bgr[:, :, 1] << 8) | bgr[:, :, 0]


def validate_words(words):
    if not isinstance(words, np.ndarray) or words.dtype != np.dtype(np.uint32):
        raise TypeError("pixels must be an exact numpy uint32 array")
    if words.shape not in ((HEIGHT, WIDTH), (HEIGHT * WIDTH,)):
        raise ValueError("pixels shape must be (480, 640) or (307200,)")
    if np.any(words & np.uint32(0xFF000000)):
        raise ValueError("pixel format must be 0x00RRGGBB; high 8 bits must be zero")
    return words.reshape(-1)


def decode_words(words):
    """Return (valid, global_y_pixels), rejecting corrupt or stale records."""
    if not isinstance(words, np.ndarray) or words.dtype != np.dtype(np.uint32):
        raise TypeError("records must be an exact numpy uint32 array")
    if words.shape != (WIDTH,):
        raise ValueError("output must contain exactly 640 uint32 records")
    valid = (words & np.uint32(0x80000000)) != 0
    q8 = words & np.uint32(0x0003FFFF)
    if np.any(words & np.uint32(0x7FFC0000)):
        raise LaserStripeError("output reserved bits 30:18 are nonzero")
    if np.any((~valid) & (words != 0)):
        raise LaserStripeError("an invalid output record must be exactly zero")
    if np.any(valid & (q8 >= HEIGHT * 256)):
        raise LaserStripeError("valid output y is outside [0, 480)")
    y = q8.astype(np.float64) / 256.0
    y[~valid] = np.nan
    return valid, y


@dataclass(frozen=True)
class StripeResult:
    words: np.ndarray
    valid: np.ndarray
    y: np.ndarray
    cycles: int
    completed_frames: int


class SimpleAxiDma:
    """AXI DMA PG021 simple mode, 32-bit addresses, both channels.

    Use only public MMIO read/write and buffer device_address APIs. No channel
    wait(), private PYNQ fields, interrupts, SG, or DRE are required. The caller
    must give this adapter exclusive ownership of the DMA and its stream IP.
    """
    MM2S_CR, MM2S_SR, MM2S_ADDR, MM2S_LENGTH = 0x00, 0x04, 0x18, 0x28
    S2MM_CR, S2MM_SR, S2MM_ADDR, S2MM_LENGTH = 0x30, 0x34, 0x48, 0x58
    HALTED, IDLE, SG_INCLUDED = 1, 2, 8
    ERROR_MASK, IOC, IRQ_MASK = 0x770, 0x1000, 0x7000

    def __init__(self, mmio, *, length_bits=23):
        self.mmio = mmio
        self.length_bits = _integer("length_bits", length_bits, 21, 26)

    def snapshot(self):
        return {"mm2s_cr": int(self.mmio.read(self.MM2S_CR)),
                "mm2s_sr": int(self.mmio.read(self.MM2S_SR)),
                "s2mm_cr": int(self.mmio.read(self.S2MM_CR)),
                "s2mm_sr": int(self.mmio.read(self.S2MM_SR)),
                "received_bytes": int(self.mmio.read(self.S2MM_LENGTH))}

    def assert_ready(self):
        snap = self.snapshot()
        for name in ("mm2s", "s2mm"):
            status, control = snap[name + "_sr"], snap[name + "_cr"]
            if status & self.SG_INCLUDED:
                raise LaserStripeError("scatter/gather DMA is unsupported", snap)
            if status & self.ERROR_MASK or control & 4:
                raise LaserStripeError("DMA needs recovery before use", snap)
            if not status & (self.IDLE | self.HALTED):
                raise LaserStripeError("DMA is already transferring; exclusive ownership required", snap)

    def _arm(self, buffer, nbytes, *, receive):
        nbytes = _integer("nbytes", nbytes, 1, (1 << self.length_bits) - 1)
        address = _integer("buffer.device_address", buffer.device_address, 0, 0xFFFFFFFF)
        if address % 4 or address + nbytes > 0x100000000:
            raise ValueError("DMA requires aligned buffers within 32-bit address space")
        if int(buffer.nbytes) < nbytes:
            raise ValueError("DMA buffer is too small")
        cr, sr, addr, length = ((self.S2MM_CR, self.S2MM_SR, self.S2MM_ADDR, self.S2MM_LENGTH)
                                if receive else
                                (self.MM2S_CR, self.MM2S_SR, self.MM2S_ADDR, self.MM2S_LENGTH))
        self.mmio.write(sr, self.IRQ_MASK)  # W1C stale completion/IRQ flags.
        self.mmio.write(cr, 1)  # RS, polling mode, interrupts disabled.
        self.mmio.write(addr, address)
        self.mmio.write(length, nbytes)  # Writing length starts this channel.

    def arm_receive(self, buffer, nbytes):
        self._arm(buffer, nbytes, receive=True)

    def arm_send(self, buffer, nbytes):
        self._arm(buffer, nbytes, receive=False)

    def completed(self):
        snap = self.snapshot()
        for name in ("mm2s", "s2mm"):
            status = snap[name + "_sr"]
            if status & self.ERROR_MASK:
                raise LaserStripeError(f"{name} DMA hardware error", snap)
            if status & self.HALTED:
                raise LaserStripeError(f"{name} DMA unexpectedly halted", snap)
        done_mask = self.IDLE | self.IOC
        return all(snap[n + "_sr"] & done_mask == done_mask for n in ("mm2s", "s2mm"))

    def verify_receive_length(self, expected):
        actual = int(self.mmio.read(self.S2MM_LENGTH))
        if actual != expected:
            raise LaserStripeError(f"DMA received {actual} bytes; expected {expected}", self.snapshot())

    def reset(self, timeout, clock, sleep, poll_interval):
        """Request the DMA-wide reset and confirm BOTH channels are halted.

        PG021 reset on either control register resets the entire DMA. Attempt
        both writes even if one MMIO access raises, and never trust reset bit
        auto-clear alone as proof that both directions have stopped.
        """
        failures = []
        for offset in (self.MM2S_CR, self.S2MM_CR):
            try:
                self.mmio.write(offset, 4)
            except Exception as exc:
                failures.append(repr(exc))
        deadline = clock() + timeout
        while True:
            try:
                snap = self.snapshot()
                clear = not (snap["mm2s_cr"] & 4 or snap["s2mm_cr"] & 4)
                halted = all(snap[n + "_sr"] & self.HALTED for n in ("mm2s", "s2mm"))
                clean = all(not snap[n + "_sr"] & self.ERROR_MASK for n in ("mm2s", "s2mm"))
                if clear and halted and clean:
                    return
            except Exception as exc:
                failures.append(repr(exc))
            if clock() >= deadline:
                raise RecoveryError("DMA reset did not confirm both channels halted",
                                    {"reset_access_errors": failures[-4:]})
            sleep(poll_interval)


class LaserStripe:
    """One frame at a time; create one driver per IP/DMA, and do not share it.

    ``dma`` must implement SimpleAxiDma's public adapter methods. ``allocator``
    has pynq.allocate(shape=..., dtype=...) semantics, including cache methods.
    """
    def __init__(self, mmio, dma, allocator, *, timeout=10.0, recovery_timeout=1.0,
                 poll_interval=0.001, clock=time.monotonic, sleep=time.sleep):
        self.mmio, self.dma, self.allocator = mmio, dma, allocator
        self.timeout = _duration("timeout", timeout)
        self.recovery_timeout = _duration("recovery_timeout", recovery_timeout)
        self.poll_interval = _duration("poll_interval", poll_interval, zero_ok=True)
        self.clock, self.sleep = clock, sleep
        self.healthy = True
        self._quarantine_keys = []
        self._running = False
        self.validate_identity()

    @classmethod
    def from_overlay(cls, overlay, *, ip_name="laser_stripe_0", dma_name="axi_dma_0",
                     **kwargs):
        """Bind an already loaded overlay; does not download or reprogram it."""
        from pynq import MMIO, allocate  # Board-only dependency, deliberately lazy.
        # Never getattr(overlay, dma_name): PYNQ's native DMA constructor starts
        # its channels, changing fresh hardware from Halted to non-idle before
        # its first transaction. Bind public metadata/MMIO without that effect.
        ip_info = overlay.ip_dict[ip_name]
        dma_info = overlay.ip_dict[dma_name]
        params = {str(k).lower(): v for k, v in dma_info.get("parameters", {}).items()}
        required = ("c_include_sg", "c_sg_length_width", "c_addr_width",
                    "c_include_mm2s", "c_include_s2mm")
        missing = [key for key in required if key not in params]
        if missing:
            raise LaserStripeError("DMA HWH metadata is missing: " + ", ".join(missing))
        # HWH values may be decimal strings or ints, with uppercase/lowercase
        # keys depending on parser version. Missing metadata is never guessed.
        if int(params["c_include_sg"]) != 0:
            raise LaserStripeError("DMA must use simple mode (C_INCLUDE_SG=0)")
        if int(params["c_addr_width"]) != 32:
            raise LaserStripeError("this DMA adapter requires C_ADDR_WIDTH=32")
        if any(int(params[key]) != 1 for key in ("c_include_mm2s", "c_include_s2mm")):
            raise LaserStripeError("both MM2S and S2MM DMA channels are required")
        bits = int(params["c_sg_length_width"])
        dma = SimpleAxiDma(MMIO(dma_info["phys_addr"], dma_info["addr_range"]), length_bits=bits)
        return cls(MMIO(ip_info["phys_addr"], ip_info["addr_range"]), dma, allocate, **kwargs)

    @property
    def quarantined_buffer_count(self):
        return sum(len(_UNQUIESCED_BUFFERS[k]) for k in self._quarantine_keys)

    def validate_identity(self):
        expected = {ID: IP_ID, VERSION: IP_VERSION, REG_INPUT_BYTES: INPUT_BYTES,
                    REG_OUTPUT_BYTES: OUTPUT_BYTES, REG_WIDTH: WIDTH, REG_HEIGHT: HEIGHT}
        for offset, wanted in expected.items():
            actual = int(self.mmio.read(offset))
            if actual != wanted:
                raise LaserStripeError(f"IP register 0x{offset:02x}: 0x{actual:08x}, expected 0x{wanted:08x}")

    def diagnostics(self):
        result = {}
        for name, offset in (("status", STATUS), ("completed_frames", COMPLETED_FRAMES)):
            try:
                result[name] = int(self.mmio.read(offset))
            except Exception as exc:
                result[name] = repr(exc)
        try:
            result["dma"] = self.dma.snapshot()
        except Exception as exc:
            result["dma"] = repr(exc)
        result["quarantined_buffers"] = self.quarantined_buffer_count
        return result

    def _ensure_idle(self):
        if not self.healthy:
            raise RecoveryError("driver is quarantined; recover() must succeed before reuse", self.diagnostics())
        status = int(self.mmio.read(STATUS))
        if status & (BUSY | RESETTING):
            raise LaserStripeError("core is busy or resetting; no configuration was written", {"status": status})
        self.dma.assert_ready()

    def configure(self, config):
        if not isinstance(config, StripeConfig):
            raise TypeError("config must be StripeConfig")
        self._ensure_idle()
        for offset, value in ((X0, config.x0), (Y0, config.y0), (X1, config.x1),
                              (Y1, config.y1), (THRESHOLD_Q8, config.threshold_q8),
                              (MIN_RUN, config.min_run)):
            self.mmio.write(offset, value)

    def _cycles(self):
        for _ in range(4):
            high1 = int(self.mmio.read(CYCLES_HIGH))
            low = int(self.mmio.read(CYCLES_LOW))
            high2 = int(self.mmio.read(CYCLES_HIGH))
            if high1 == high2:
                return (high1 << 32) | low
        raise LaserStripeError("cycle counter did not produce a coherent read")

    @staticmethod
    def _release(buffers):
        for buffer in buffers:
            buffer.freebuffer()

    def _quarantine(self, buffers):
        if buffers:
            key = object()
            _UNQUIESCED_BUFFERS[key] = list(buffers)
            self._quarantine_keys.append(key)
        self.healthy = False

    def recover(self):
        """Abort the core, reset BOTH DMA directions, then release held buffers."""
        self.healthy = False
        failures = []
        try:
            self.mmio.write(CONTROL, ABORT)
        except Exception as exc:
            failures.append("core abort: " + repr(exc))
        try:
            self.dma.reset(self.recovery_timeout, self.clock, self.sleep, self.poll_interval)
        except Exception as exc:
            failures.append("DMA reset: " + repr(exc))
        deadline = self.clock() + self.recovery_timeout
        core_idle = False
        while True:
            try:
                core_idle = not int(self.mmio.read(STATUS)) & (BUSY | RESETTING)
                if core_idle:
                    break
            except Exception:
                pass
            if self.clock() >= deadline:
                failures.append("core did not leave busy/resetting state")
                break
            self.sleep(self.poll_interval)
        if failures or not core_idle:
            self.healthy = False
            raise RecoveryError("recovery failed; buffers remain quarantined",
                                {**self.diagnostics(), "recovery_errors": failures})
        self.mmio.write(CONTROL, CLEAR)
        for key in self._quarantine_keys:
            self._release(_UNQUIESCED_BUFFERS.pop(key))
        self._quarantine_keys.clear()
        self.healthy = True

    def run_bgr(self, image, config=None, *, timeout=None):
        return self.run_words(pack_bgr(image), config, timeout=timeout)

    def run_words(self, words, config=None, *, timeout=None):
        words = validate_words(words)
        config = StripeConfig() if config is None else config
        limit = self.timeout if timeout is None else _duration("timeout", timeout)
        if self._running:
            raise LaserStripeError("driver is already running a frame")
        self.configure(config)
        self._running = True
        buffers, touched = [], False
        try:
            tx = self.allocator(shape=(WIDTH * HEIGHT,), dtype=np.uint32)
            buffers.append(tx)
            rx = self.allocator(shape=(WIDTH,), dtype=np.uint32)
            buffers.append(rx)
            tx[:] = words
            rx[:] = np.uint32(0xDEADBEEF)  # Detect missing writes on every frame.
            tx.flush()
            rx.flush()  # Prevent dirty output cache lines overwriting DMA writes.
            previous_count = int(self.mmio.read(COMPLETED_FRAMES))
            self.mmio.write(CONTROL, CLEAR)
            deadline = self.clock() + limit
            touched = True  # Any following MMIO error could leave a DMA active.
            self.dma.arm_receive(rx, OUTPUT_BYTES)
            self.mmio.write(CONTROL, START)
            self.dma.arm_send(tx, INPUT_BYTES)
            while True:
                status = int(self.mmio.read(STATUS))
                if status & ERROR:
                    code = (status >> 8) & 15
                    raise LaserStripeError(f"core hardware error_code={code}", {"status": status})
                dma_done = self.dma.completed()
                if status & DONE and not status & (BUSY | RESETTING) and dma_done:
                    break
                if self.clock() >= deadline:
                    raise LaserStripeTimeout(f"frame timed out after {limit:g} seconds")
                self.sleep(self.poll_interval)
            self.dma.verify_receive_length(OUTPUT_BYTES)
            count = int(self.mmio.read(COMPLETED_FRAMES))
            if count != ((previous_count + 1) & 0xFFFFFFFF):
                raise LaserStripeError("completed frame count did not advance by one")
            rx.invalidate()
            output = np.asarray(rx).copy()
            valid, y = decode_words(output)
            return StripeResult(output, valid, y, self._cycles(), count)
        except BaseException as exc:
            if touched:
                before = self.diagnostics()
                try:
                    self.recover()
                except BaseException as recovery_exc:
                    self._quarantine(buffers)
                    buffers = []
                    raise RecoveryError("frame failed and recovery could not confirm quiescence; buffers retained",
                                        {"before_recovery": before, "after_recovery": self.diagnostics(),
                                         "cause": repr(exc), "recovery": repr(recovery_exc)}) from exc
                if isinstance(exc, LaserStripeError):
                    exc.diagnostics = {"before_recovery": before, "recovered": True}
                elif isinstance(exc, Exception):
                    raise LaserStripeError("frame failed; core and both DMA channels recovered",
                                           {"before_recovery": before, "recovered": True,
                                            "cause": repr(exc)}) from exc
            raise
        finally:
            self._running = False
            self._release(buffers)
