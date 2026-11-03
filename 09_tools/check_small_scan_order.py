"""Offline failure/ordering checks for the bounded PL + UART acquisition flow."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "04_runtime"))
from rotation_scan import run_scan


class Motor:
    def __init__(self, events):
        self.events = events
        self.value = 12.
        self.motion_log = []
        self.transactions = []

    def prepare(self):
        self.events.append("prepare")
        return {"initial_position_deg": self.value, "initial_settle": {"fixture": True},
                "backend": "OFFLINE_FIXTURE"}

    def move_to(self, value, rpm):
        self.events.append("move")
        self.motion_log.append({"target": value, "rpm": rpm})
        self.value = value
        return {"motor_position_deg": value}

    def position(self):
        return {"motor_position_deg": self.value}

    def stop(self):
        self.events.append("stop")
        return {"fixture": True}

    def close(self):
        self.events.append("motor_closed")


class Camera:
    def __init__(self, events, fail_at=None, motor=None):
        self.events, self.fail_at, self.motor = events, fail_at, motor
        self.count = 0

    def start(self, root):
        self.events.append("camera_start")
        return {"fixture": True}

    def capture(self, folder):
        self.count += 1
        self.events.append("capture_%d" % self.count)
        if self.count == self.fail_at:
            raise RuntimeError("fixture current-scene quality failure")
        folder.mkdir(parents=True)
        frames = []
        for index in range(3):
            path = folder / ("%d.fixture" % index)
            path.write_bytes(b"offline hash fixture")
            frames.append({"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if self.motor is not None and self.count == 2:
            self.motor.value += .2
        return {"images": frames, "source": "OFFLINE_FIXTURE"}

    def close(self):
        self.events.append("camera_closed")
        return {"fixture": True}


class ScanOrderTests(unittest.TestCase):
    def run_fixture(self, fail_at=None, drift=False):
        events = []
        motor = Motor(events)
        camera = Camera(events, fail_at, motor if drift else None)
        with tempfile.TemporaryDirectory() as temp:
            result = run_scan(motor, camera, Path(temp) / "scan", angles=(0, 5, 0),
                              simulation=True, progress=lambda _: None,
                              preflight_capture=True, reuse_initial_settle=True,
                              max_angle_deg=10, max_points=3)
        return result, events, motor

    def test_quality_precedes_motor_and_origin_needs_no_extra_move(self):
        result, events, motor = self.run_fixture()
        self.assertEqual(result["status"], "completed")
        self.assertLess(events.index("capture_1"), events.index("prepare"))
        self.assertEqual(motor.motion_log, [{"target": 17., "rpm": 1}, {"target": 12., "rpm": 1}])
        self.assertTrue(result["points"][0]["move"]["initial_settle_reused"])
        self.assertFalse(result["real_hardware_pipeline_verified"])
        self.assertEqual(events[-3:], ["stop", "camera_closed", "motor_closed"])

    def test_failed_preflight_does_not_prepare_or_move(self):
        result, events, motor = self.run_fixture(fail_at=1)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("prepare", events)
        self.assertNotIn("move", events)
        self.assertEqual(motor.motion_log, [])
        self.assertEqual(events[-3:], ["stop", "camera_closed", "motor_closed"])

    def test_failed_pose_cancels_remaining_angle(self):
        result, events, motor = self.run_fixture(fail_at=3)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(motor.motion_log), 1)
        self.assertEqual(motor.motion_log[0]["target"], 17.)
        self.assertEqual(result["points"][-1]["status"], "failed")
        self.assertNotIn("capture_4", events)

    def test_capture_drift_stops_before_next_move(self):
        result, events, motor = self.run_fixture(drift=True)
        self.assertEqual(result["status"], "failed")
        self.assertIn("position changed", result["error"])
        self.assertEqual(motor.motion_log, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
