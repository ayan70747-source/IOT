# IoT Patient Monitoring System

Production-oriented Raspberry Pi 5 project for motion-triggered patient monitoring with a PIR sensor on GPIO 17, video capture through Raspberry Pi Camera Module 3, Azure Blob Storage uploads, and Azure IoT Hub telemetry/alerting.

## Repository Structure

```text
.
|-- .github/
|   `-- workflows/
|       `-- ci.yml
|-- .env.example
|-- .gitignore
|-- README.md
|-- deploy/
|   `-- patient-monitor.service
|-- main.py
|-- scripts/
|   `-- install_service.sh
|-- tests/
|   `-- test_main.py
`-- requirements.txt
```

## Features

- Non-blocking motion detection using `gpiozero.MotionSensor` and `asyncio`
- 5-second video recording with `rpicam-vid`
- Concurrent Azure Blob upload and Azure IoT Hub telemetry after each capture
- Fall detection / inactivity alert logic: motion followed by no motion for a configurable period
- Environment-based configuration with `python-dotenv`
- Graceful shutdown support for production deployment on Raspberry Pi OS
- GitHub Actions validation for every push and pull request
- `systemd` service template for unattended boot-time startup
- Bootstrap script to install, configure, and enable the service on a Raspberry Pi
- Unit tests for async inactivity, recording, and upload flows

## Hardware Wiring

### PIR Sensor to Raspberry Pi 5

- PIR `VCC` -> Raspberry Pi `5V`
- PIR `GND` -> Raspberry Pi `GND`
- PIR `OUT` -> Raspberry Pi `GPIO 17` (physical pin 11)

### Camera Module 3

- Connect the Raspberry Pi Camera Module 3 to the CSI camera port on the Raspberry Pi 5
- Enable the camera interface in Raspberry Pi OS if required

## Prerequisites

- Raspberry Pi 5 running a current Raspberry Pi OS release
- Raspberry Pi Camera Module 3 installed and verified
- PIR motion sensor wired to GPIO 17
- Python 3.11+
- `rpicam-vid` available on the device
- Azure IoT Hub and Azure Storage account

## Local Setup

1. Create and activate a virtual environment:

	```bash
	python3 -m venv .venv
	source .venv/bin/activate
	```

2. Install Python dependencies:

	```bash
	pip install -r requirements.txt
	```

3. Create your environment file:

	```bash
	cp .env.example .env
	```

4. Update `.env` with your Azure connection strings and any optional overrides.

5. Start the monitoring service:

	```bash
	python main.py
	```

## Quick Start: See Motion Detection and Video Capture on Raspberry Pi

Follow these steps in order on the Raspberry Pi.

1. Install OS packages used by camera and reliable GPIO access:

		  ```bash
		  sudo apt update
		  sudo apt install -y python3-lgpio rpicam-apps
		  ```

2. Verify the camera works before running the app:

		  ```bash
		  rpicam-hello -t 3000
		  which rpicam-vid
		  ```

	Expected result: a 3-second camera preview opens, and `rpicam-vid` prints a valid path.

3. Configure the project and install dependencies:

		  ```bash
		  cd /home/pi/Downloads/IOT-main
		  python3 -m venv .venv
		  source .venv/bin/activate
		  pip install -r requirements.txt
		  cp .env.example .env
		  ```

4. Edit `.env` and set your real values:
	- `IOT_CONNECTION_STRING`
	- `STORAGE_CONNECTION_STRING`
	- `GPIOZERO_PIN_FACTORY=lgpio`
	- Optional: `LOG_LEVEL=DEBUG`

5. Start the app:

		  ```bash
		  source .venv/bin/activate
		  python main.py
		  ```

6. Confirm startup logs are healthy:
	- Look for `Successfully connected to Hub`
	- Look for `GPIO pin factory in use:` and confirm it is not `NativeFactory`
	- Look for `Starting motion polling loop`

7. Trigger the PIR sensor with movement.
	- Wave a hand in front of the PIR sensor for 2 to 5 seconds.
	- Wait for the sensor delay period to settle (many PIR modules need a short cooldown).

8. Confirm motion and recording logs appear:
	- `Telemetry sent: motionDetected`
	- `Starting video capture:`
	- `Capture workflow completed`
	- `Telemetry sent: videoUploaded`

9. Confirm the video exists locally:

		  ```bash
		  ls -lh recordings
		  ```

	Expected result: one or more files named like `motion-YYYYMMDDTHHMMSSZ-xxxxxxxx.mp4`.

10. Confirm upload in Azure Blob Storage:
	 - Open your storage account container (default `patient-monitoring`).
	 - Open the `videos/` path and verify the same filename appears.

If motion is still not detected, go to [Raspberry Pi Hardware Troubleshooting](README.md#raspberry-pi-hardware-troubleshooting).

## Production Deployment with systemd

1. Copy the repository to the Raspberry Pi, for example into `/opt/iot-patient-monitor`.
2. Fast path: run the installer script as root to copy files, create the virtualenv, install dependencies, and register the `systemd` service.

	```bash
	sudo bash scripts/install_service.sh
	```

   Optional overrides:

	```bash
	sudo APP_DIR=/srv/patient-monitor SERVICE_USER=pi SERVICE_GROUP=pi bash scripts/install_service.sh
	```

3. If you prefer manual setup, create the virtual environment and install dependencies:

	```bash
	cd /opt/iot-patient-monitor
	python3 -m venv .venv
	source .venv/bin/activate
	pip install -r requirements.txt
	```

4. Copy `.env.example` to `.env` and add your production Azure connection strings.
5. Review [deploy/patient-monitor.service](/workspaces/IOT/deploy/patient-monitor.service) and adjust `User`, `Group`, `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` if your install path or Linux user differs.
6. Install and start the service:

	```bash
	sudo cp deploy/patient-monitor.service /etc/systemd/system/patient-monitor.service
	sudo systemctl daemon-reload
	sudo systemctl enable patient-monitor.service
	sudo systemctl start patient-monitor.service
	```

7. Inspect logs if needed:

	```bash
	sudo journalctl -u patient-monitor.service -f
	```

The installer script is stored in [scripts/install_service.sh](/workspaces/IOT/scripts/install_service.sh).

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `IOT_CONNECTION_STRING` | Yes | Azure IoT Hub device connection string |
| `STORAGE_CONNECTION_STRING` | Yes | Azure Storage account connection string |
| `BLOB_CONTAINER_NAME` | No | Blob container name for recordings |
| `DEVICE_ID` | No | Device label included in telemetry |
| `PIR_GPIO_PIN` | No | PIR output GPIO pin, defaults to `17` |
| `INACTIVITY_TIMEOUT_SECONDS` | No | Time after no motion before fall/inactivity alert |
| `RECORDING_DURATION_SECONDS` | No | Video recording length in seconds, defaults to `5` |
| `VIDEO_DIRECTORY` | No | Local folder for captured recordings |
| `VIDEO_WIDTH` | No | Capture width |
| `VIDEO_HEIGHT` | No | Capture height |
| `VIDEO_FRAMERATE` | No | Capture frame rate |
| `MOTION_POLL_INTERVAL_SECONDS` | No | Motion state polling interval used for edge detection fallback, defaults to `0.2` |
| `LOG_LEVEL` | No | Logging verbosity |

## Azure Portal Configuration

### 1. Create an IoT Hub

1. In Azure Portal, create an IoT Hub resource.
2. Open the IoT Hub and create a new device under `Devices`.
3. Copy the device connection string into `IOT_CONNECTION_STRING`.

### 2. Create a Storage Account

1. In Azure Portal, create a Storage Account.
2. In `Data storage` -> `Containers`, create a blob container, for example `patient-monitoring`.
3. Open `Access keys` and copy a connection string into `STORAGE_CONNECTION_STRING`.
4. Set `BLOB_CONTAINER_NAME` in `.env` to the container name you created.

### 3. Validate Message Flow

1. Run the application on the Raspberry Pi.
2. Trigger the PIR sensor with movement.
3. Confirm these outcomes:
	- A 5-second `.mp4` recording is created locally
	- The file is uploaded to Azure Blob Storage
	- Telemetry events appear in IoT Hub for motion, recording, upload, and inactivity alerts

## Runtime Behavior

- On motion detection, the application immediately emits motion telemetry.
- If no recording is running, it starts a 5-second `rpicam-vid` capture in a background task.
- Once recording completes, the upload to Blob Storage and telemetry publication run concurrently.
- If motion stops and remains absent for `INACTIVITY_TIMEOUT_SECONDS`, the system sends a `potentialFallInactivity` alert to IoT Hub.
- New motion cancels any pending inactivity alert timer.

## Deployment Notes

- Run the project directly on the Raspberry Pi so `gpiozero` and `rpicam-vid` can access the hardware.
- For service deployment, use `systemd` and point the unit to the virtual environment Python binary.
- Keep `.env` out of source control.
- Review container existence and Azure RBAC/networking rules before production rollout.

## Raspberry Pi Hardware Troubleshooting

- If logs show `PinFactoryFallback` warnings, install a supported GPIO backend:

	```bash
	sudo apt update
	sudo apt install -y python3-lgpio
	```

- You can then force the backend in your `.env` or service environment:

	```bash
	GPIOZERO_PIN_FACTORY=lgpio
	```

- Verify camera tooling is installed and the camera is detected:

	```bash
	which rpicam-vid
	rpicam-hello -t 3000
	```

## Continuous Integration

- GitHub Actions runs on pushes to `main` and on pull requests.
- The workflow installs dependencies, validates `main.py` with `python -m py_compile`, and runs unit tests under `tests/`.
- The workflow file is stored in [.github/workflows/ci.yml](/workspaces/IOT/.github/workflows/ci.yml).

## Test Suite

- Run the local unit tests with `python -m unittest discover -s tests -p "test_*.py" -v`.
- The tests use mocks instead of real GPIO, camera, or Azure hardware so they can run in CI and on developer machines.
- The current test module is [tests/test_main.py](/workspaces/IOT/tests/test_main.py).