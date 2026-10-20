import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import av

SCRIPT = Path(__file__).resolve().parents[1] / "audit_final_videos.py"
spec = importlib.util.spec_from_file_location("final_video_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def make_video(path, count=30, fps=30, codec="libx264"):
    with av.open(str(path), "w") as container:
        stream = container.add_stream(codec, rate=fps)
        stream.width = stream.height = 32
        stream.pix_fmt = "yuv420p"
        for _ in range(count):
            frame = av.VideoFrame(32, 32, "yuv420p")
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class FinalVideoAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "test.mp4"
        make_video(self.video)

    def test_valid_video(self):
        result = audit.audit_video(self.video, 1000, [32, 32])
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["decoded_frames"], 30)

    def test_wrong_codec(self):
        make_video(self.video, codec="mpeg4")
        self.assertIn("codec is not H.264", audit.audit_video(self.video, 1000, [32, 32])["errors"])

    def test_wrong_frame_rate(self):
        make_video(self.video, fps=24)
        self.assertIn("frame rate is not 30 Hz", audit.audit_video(self.video, 1000, [32, 32])["errors"])

    def test_wrong_dimensions(self):
        self.assertFalse(audit.audit_video(self.video, 1000, [64, 32])["passed"])

    def test_wrong_frame_count(self):
        self.assertIn("decoded frame count does not match round(30T)", audit.audit_video(self.video, 2000, [32, 32])["errors"])

    def test_corrupt_file(self):
        self.video.write_bytes(b"not a video")
        self.assertFalse(audit.audit_video(self.video, 1000, [32, 32])["passed"])

    def test_half_frame_rounding(self):
        make_video(self.video, count=5)
        result = audit.audit_video(self.video, 150, [32, 32])
        self.assertEqual(result["expected_frames"], 5)
        self.assertTrue(result["passed"], result)

    def fixture_event(self):
        event = self.root / "Test_valid/01_Test/segment/event_01"
        (event / "video").mkdir(parents=True)
        record = {"Timestamp": "1700000000000-1700000001000", "camera_set_id": "test"}
        (event / "test_record.json").write_text(json.dumps(record))
        cameras = [{"video_tag": tag, "image_size": [32, 32]} for tag in audit.TAGS]
        sets = {"test": {"camera_set_id": "test", "cameras": cameras}}
        (self.root / "camera_parameters.json").write_text(json.dumps({"camera_sets": list(sets.values())}))
        for tag in audit.TAGS:
            (event / "video" / f"video_{tag}_event_001.mp4").write_bytes(self.video.read_bytes())
        return event, sets

    def test_missing_view(self):
        event, sets = self.fixture_event()
        (event / "video/video_rear_event_001.mp4").unlink()
        result = audit.audit_event(event, sets)
        self.assertFalse(result["passed"])
        self.assertIn("video file set mismatch", result["errors"][0])

    def test_invalid_camera_reference(self):
        event, _ = self.fixture_event()
        self.assertFalse(audit.audit_event(event, {})["passed"])

    def test_cli_and_report_overwrite_protection(self):
        self.fixture_event()
        output = self.root / "report.json"
        command = [sys.executable, str(SCRIPT), "--dataset-root", str(self.root), "--output", str(output)]
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(output.read_text())
        self.assertTrue(report["passed"])
        self.assertEqual(report["events_checked"], 1)
        self.assertEqual(report["videos_checked"], 7)
        before = output.read_bytes()
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
