import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import main


class SettingsTests(unittest.TestCase):
    def test_from_env_loads_required_settings_and_defaults(self) -> None:
        env = {
            "IOT_CONNECTION_STRING": "HostName=test.azure-devices.net;DeviceId=device;SharedAccessKey=key",
            "STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=key;EndpointSuffix=core.windows.net",
        }

        with patch("main.load_dotenv"), patch.dict(os.environ, env, clear=True):
            settings = main.Settings.from_env()

        self.assertEqual(settings.pir_gpio_pin, 17)
        self.assertEqual(settings.recording_duration_seconds, 5)
        self.assertEqual(settings.inactivity_timeout_seconds, 60)
        self.assertEqual(settings.blob_container_name, "patient-monitoring")

    def test_from_env_raises_when_required_values_are_missing(self) -> None:
        with patch("main.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                main.Settings.from_env()


class PatientMonitoringSystemAsyncTests(unittest.IsolatedAsyncioTestCase):
    def build_system(self) -> main.PatientMonitoringSystem:
        system = main.PatientMonitoringSystem.__new__(main.PatientMonitoringSystem)
        system.settings = main.Settings(
            iot_connection_string="iot",
            storage_connection_string="storage",
            blob_container_name="patient-monitoring",
            device_id="device-123",
            inactivity_timeout_seconds=60,
            recording_duration_seconds=5,
        )
        system.recording_lock = asyncio.Lock()
        system.motion_sensor = SimpleNamespace(motion_detected=False)
        system.last_motion_detected_at = datetime(2026, 4, 22, tzinfo=timezone.utc)
        system.last_no_motion_at = datetime(2026, 4, 22, 0, 1, tzinfo=timezone.utc)
        system.alert_sent_for_cycle = False
        system.background_tasks = set()
        system.iot_connected = False
        return system

    async def test_monitor_inactivity_sends_alert_after_timeout(self) -> None:
        system = self.build_system()
        system._send_telemetry = AsyncMock()

        with patch("main.asyncio.sleep", new=AsyncMock()):
            await system._monitor_inactivity()

        system._send_telemetry.assert_awaited_once()
        payload = system._send_telemetry.await_args.args[0]
        self.assertEqual(payload["eventType"], "potentialFallInactivity")
        self.assertEqual(payload["deviceId"], system.settings.device_id)
        self.assertTrue(system.alert_sent_for_cycle)

    async def test_monitor_inactivity_skips_alert_when_motion_returns(self) -> None:
        system = self.build_system()
        system.motion_sensor.motion_detected = True
        system._send_telemetry = AsyncMock()

        with patch("main.asyncio.sleep", new=AsyncMock()):
            await system._monitor_inactivity()

        system._send_telemetry.assert_not_awaited()

    async def test_capture_and_publish_workflow_records_uploads_and_emits_telemetry(self) -> None:
        system = self.build_system()
        motion_time = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
        video_path = Path("recordings/test-video.mp4")
        system._build_video_path = MagicMock(return_value=video_path)
        system._record_video = AsyncMock()
        system._upload_video = AsyncMock()
        system._send_telemetry = AsyncMock()

        await system._capture_and_publish_workflow(motion_time)

        system._build_video_path.assert_called_once_with(motion_time)
        system._record_video.assert_awaited_once_with(video_path)
        system._upload_video.assert_awaited_once()
        system._send_telemetry.assert_awaited_once()

    async def test_upload_video_sends_blob_and_upload_telemetry(self) -> None:
        system = self.build_system()
        blob_client = MagicMock()
        system.blob_service_client = MagicMock()
        system.blob_service_client.get_blob_client.return_value = blob_client
        system._send_telemetry = AsyncMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "motion.mp4"
            video_path.write_bytes(b"video-bytes")

            motion_time = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
            started_at = datetime(2026, 4, 22, 12, 0, 1, tzinfo=timezone.utc)
            completed_at = datetime(2026, 4, 22, 12, 0, 6, tzinfo=timezone.utc)

            await system._upload_video(video_path, motion_time, started_at, completed_at)

        system.blob_service_client.get_blob_client.assert_called_once_with(
            container="patient-monitoring",
            blob="videos/motion.mp4",
        )
        blob_client.upload_blob.assert_called_once()
        upload_payload = system._send_telemetry.await_args.args[0]
        self.assertEqual(upload_payload["eventType"], "videoUploaded")


if __name__ == "__main__":
    unittest.main()