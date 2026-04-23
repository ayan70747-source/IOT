import asyncio
import json
import logging
import os
import shutil
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from azure.iot.device import Message
from azure.iot.device.aio import IoTHubDeviceClient
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from gpiozero import MotionSensor


LOGGER = logging.getLogger("patient-monitor")


@dataclass(slots=True)
class Settings:
    iot_connection_string: str
    storage_connection_string: str
    blob_container_name: str
    device_id: str
    pir_gpio_pin: int = 17
    inactivity_timeout_seconds: int = 60
    recording_duration_seconds: int = 5
    video_directory: Path = Path("recordings")
    video_width: int = 1280
    video_height: int = 720
    video_framerate: int = 30
    motion_poll_interval_seconds: float = 0.2

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        iot_connection_string = os.getenv("IOT_CONNECTION_STRING")
        storage_connection_string = os.getenv("STORAGE_CONNECTION_STRING")
        blob_container_name = os.getenv("BLOB_CONTAINER_NAME", "patient-monitoring")
        device_id = os.getenv("DEVICE_ID", "rpi5-patient-monitor")

        missing = [
            name
            for name, value in (
                ("IOT_CONNECTION_STRING", iot_connection_string),
                ("STORAGE_CONNECTION_STRING", storage_connection_string),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        return cls(
            iot_connection_string=iot_connection_string,
            storage_connection_string=storage_connection_string,
            blob_container_name=blob_container_name,
            device_id=device_id,
            pir_gpio_pin=int(os.getenv("PIR_GPIO_PIN", "17")),
            inactivity_timeout_seconds=int(os.getenv("INACTIVITY_TIMEOUT_SECONDS", "60")),
            recording_duration_seconds=int(os.getenv("RECORDING_DURATION_SECONDS", "5")),
            video_directory=Path(os.getenv("VIDEO_DIRECTORY", "recordings")),
            video_width=int(os.getenv("VIDEO_WIDTH", "1280")),
            video_height=int(os.getenv("VIDEO_HEIGHT", "720")),
            video_framerate=int(os.getenv("VIDEO_FRAMERATE", "30")),
            motion_poll_interval_seconds=float(os.getenv("MOTION_POLL_INTERVAL_SECONDS", "0.2")),
        )


class PatientMonitoringSystem:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.shutdown_event = asyncio.Event()
        self.motion_sensor = MotionSensor(settings.pir_gpio_pin)
        self.iot_client = IoTHubDeviceClient.create_from_connection_string(
            settings.iot_connection_string
        )
        self.blob_service_client = BlobServiceClient.from_connection_string(
            settings.storage_connection_string
        )
        self.recording_lock = asyncio.Lock()
        self.inactivity_task: Optional[asyncio.Task] = None
        self.background_tasks: set[asyncio.Task] = set()
        self.motion_poll_task: Optional[asyncio.Task] = None
        self.last_motion_detected_at: Optional[datetime] = None
        self.last_no_motion_at: Optional[datetime] = None
        self.alert_sent_for_cycle = False
        self.iot_connected = False

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.settings.video_directory.mkdir(parents=True, exist_ok=True)

        await self.iot_client.connect()
        self.iot_connected = True
        await self._ensure_container_exists()
        self._register_signal_handlers()
        self._log_runtime_diagnostics()

        self.motion_poll_task = asyncio.create_task(self._poll_motion_state())
        self._track_task(self.motion_poll_task)

        LOGGER.info(
            "Patient monitoring started on GPIO %s with inactivity timeout %ss",
            self.settings.pir_gpio_pin,
            self.settings.inactivity_timeout_seconds,
        )
        await self.shutdown_event.wait()

    async def stop(self) -> None:
        if self.motion_poll_task:
            self.motion_poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.motion_poll_task

        self.motion_sensor.close()

        if self.inactivity_task:
            self.inactivity_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.inactivity_task

        if self.background_tasks:
            for task in list(self.background_tasks):
                task.cancel()
            await asyncio.gather(*self.background_tasks, return_exceptions=True)

        if self.iot_connected:
            with suppress(Exception):
                await self.iot_client.shutdown()
        LOGGER.info("Patient monitoring stopped")

    async def _ensure_container_exists(self) -> None:
        container_client = self.blob_service_client.get_container_client(
            self.settings.blob_container_name
        )
        try:
            await asyncio.to_thread(container_client.create_container)
        except ResourceExistsError:
            LOGGER.info("Blob container '%s' already exists", self.settings.blob_container_name)

    def _register_signal_handlers(self) -> None:
        assert self.loop is not None
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig, self.shutdown_event.set)
            except NotImplementedError:
                signal.signal(sig, lambda *_: self.shutdown_event.set())

    def _log_runtime_diagnostics(self) -> None:
        pin_factory_name = type(self.motion_sensor.pin_factory).__name__
        LOGGER.info("GPIO pin factory in use: %s", pin_factory_name)

        if pin_factory_name.lower().startswith("native"):
            LOGGER.warning(
                "Running with NativeFactory fallback. Install lgpio on Raspberry Pi for more reliable GPIO events."
            )

        if shutil.which("rpicam-vid") is None:
            LOGGER.error(
                "rpicam-vid is not available on PATH. Install Raspberry Pi camera tools or verify Camera Module setup."
            )

    async def _poll_motion_state(self) -> None:
        previous_state = self.motion_sensor.motion_detected
        LOGGER.info(
            "Starting motion polling loop at %.3fs interval",
            self.settings.motion_poll_interval_seconds,
        )

        while not self.shutdown_event.is_set():
            current_state = self.motion_sensor.motion_detected
            if current_state and not previous_state:
                self._schedule_motion_detected()
            elif previous_state and not current_state:
                self._schedule_motion_stopped()

            previous_state = current_state
            await asyncio.sleep(self.settings.motion_poll_interval_seconds)

    def _schedule_motion_detected(self) -> None:
        task = asyncio.create_task(self._on_motion_detected())
        self._track_task(task)

    def _schedule_motion_stopped(self) -> None:
        task = asyncio.create_task(self._on_motion_stopped())
        self._track_task(task)

    def _track_task(self, task: asyncio.Task) -> None:
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        task.add_done_callback(self._log_task_result)

    def _log_task_result(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exception = task.exception()
        if exception:
            LOGGER.exception("Background task failed", exc_info=exception)

    async def _on_motion_detected(self) -> None:
        motion_time = datetime.now(timezone.utc)
        self.last_motion_detected_at = motion_time
        self.alert_sent_for_cycle = False

        if self.inactivity_task:
            self.inactivity_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.inactivity_task
            self.inactivity_task = None

        self._track_task(
            asyncio.create_task(
                self._send_telemetry(
                    {
                        "eventType": "motionDetected",
                        "deviceId": self.settings.device_id,
                        "timestamp": motion_time.isoformat(),
                        "pirPin": self.settings.pir_gpio_pin,
                    }
                )
            )
        )

        if self.recording_lock.locked():
            LOGGER.info("Motion detected while recording is already in progress")
            return

        task = asyncio.create_task(self._capture_and_publish_workflow(motion_time))
        self._track_task(task)

    async def _on_motion_stopped(self) -> None:
        if not self.last_motion_detected_at:
            return

        self.last_no_motion_at = datetime.now(timezone.utc)

        if self.inactivity_task:
            self.inactivity_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.inactivity_task

        self.inactivity_task = asyncio.create_task(self._monitor_inactivity())
        self._track_task(self.inactivity_task)

    async def _monitor_inactivity(self) -> None:
        await asyncio.sleep(self.settings.inactivity_timeout_seconds)

        if self.motion_sensor.motion_detected or self.alert_sent_for_cycle:
            return

        alert_time = datetime.now(timezone.utc)
        self.alert_sent_for_cycle = True
        await self._send_telemetry(
            {
                "eventType": "potentialFallInactivity",
                "severity": "high",
                "deviceId": self.settings.device_id,
                "timestamp": alert_time.isoformat(),
                "lastMotionDetectedAt": self.last_motion_detected_at.isoformat()
                if self.last_motion_detected_at
                else None,
                "lastNoMotionAt": self.last_no_motion_at.isoformat()
                if self.last_no_motion_at
                else None,
                "inactivityTimeoutSeconds": self.settings.inactivity_timeout_seconds,
                "message": "Motion was detected and then stopped for the configured inactivity window.",
            }
        )
        LOGGER.warning("Potential fall/inactivity alert sent")

    async def _capture_and_publish_workflow(self, motion_time: datetime) -> None:
        async with self.recording_lock:
            video_path = self._build_video_path(motion_time)
            started_at = datetime.now(timezone.utc)

            LOGGER.info("Starting video capture: %s", video_path)
            await self._record_video(video_path)
            completed_at = datetime.now(timezone.utc)

            upload_task = asyncio.create_task(
                self._upload_video(
                    video_path=video_path,
                    motion_time=motion_time,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )
            telemetry_task = asyncio.create_task(
                self._send_telemetry(
                    {
                        "eventType": "videoRecorded",
                        "deviceId": self.settings.device_id,
                        "timestamp": completed_at.isoformat(),
                        "fileName": video_path.name,
                        "recordingDurationSeconds": self.settings.recording_duration_seconds,
                    }
                )
            )
            results = await asyncio.gather(upload_task, telemetry_task, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
            LOGGER.info("Capture workflow completed for %s", video_path.name)

    def _build_video_path(self, motion_time: datetime) -> Path:
        timestamp = motion_time.strftime("%Y%m%dT%H%M%SZ")
        file_name = f"motion-{timestamp}-{uuid4().hex[:8]}.mp4"
        return self.settings.video_directory / file_name

    async def _record_video(self, video_path: Path) -> None:
        command = [
            "rpicam-vid",
            "--timeout",
            str(self.settings.recording_duration_seconds * 1000),
            "--width",
            str(self.settings.video_width),
            "--height",
            str(self.settings.video_height),
            "--framerate",
            str(self.settings.video_framerate),
            "--output",
            str(video_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            LOGGER.error("rpicam-vid failed: %s", stderr.decode().strip())
            raise RuntimeError(
                f"Video recording failed with exit code {process.returncode}: {stderr.decode().strip()}"
            )

        if stdout:
            LOGGER.debug("rpicam-vid output: %s", stdout.decode().strip())

    async def _upload_video(
        self,
        video_path: Path,
        motion_time: datetime,
        started_at: datetime,
        completed_at: datetime,
    ) -> None:
        blob_name = f"videos/{video_path.name}"
        blob_client = self.blob_service_client.get_blob_client(
            container=self.settings.blob_container_name,
            blob=blob_name,
        )

        def upload() -> None:
            with video_path.open("rb") as data:
                blob_client.upload_blob(
                    data,
                    overwrite=True,
                    metadata={
                        "deviceId": self.settings.device_id,
                        "motionDetectedAt": motion_time.isoformat(),
                        "recordingStartedAt": started_at.isoformat(),
                        "recordingCompletedAt": completed_at.isoformat(),
                    },
                )

        await asyncio.to_thread(upload)
        await self._send_telemetry(
            {
                "eventType": "videoUploaded",
                "deviceId": self.settings.device_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "fileName": video_path.name,
                "blobName": blob_name,
                "blobContainer": self.settings.blob_container_name,
            }
        )

    async def _send_telemetry(self, payload: dict) -> None:
        message = Message(json.dumps(payload))
        message.content_encoding = "utf-8"
        message.content_type = "application/json"
        await self.iot_client.send_message(message)
        LOGGER.info("Telemetry sent: %s", payload.get("eventType", "unknown"))


async def run() -> int:
    system: Optional[PatientMonitoringSystem] = None

    try:
        settings = Settings.from_env()
        system = PatientMonitoringSystem(settings)
        await system.start()
    except Exception:
        LOGGER.exception("Patient monitoring crashed")
        return 1
    finally:
        if system is not None:
            await system.stop()

    return 0


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


if __name__ == "__main__":
    configure_logging()
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user")
        sys.exit(0)