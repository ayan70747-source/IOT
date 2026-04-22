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

## Production Deployment with systemd

1. Copy the repository to the Raspberry Pi, for example into `/opt/iot-patient-monitor`.
2. Create the virtual environment and install dependencies:

	```bash
	cd /opt/iot-patient-monitor
	python3 -m venv .venv
	source .venv/bin/activate
	pip install -r requirements.txt
	```

3. Copy `.env.example` to `.env` and add your production Azure connection strings.
4. Review [deploy/patient-monitor.service](/workspaces/IOT/deploy/patient-monitor.service) and adjust `User`, `Group`, `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` if your install path or Linux user differs.
5. Install and start the service:

	```bash
	sudo cp deploy/patient-monitor.service /etc/systemd/system/patient-monitor.service
	sudo systemctl daemon-reload
	sudo systemctl enable patient-monitor.service
	sudo systemctl start patient-monitor.service
	```

6. Inspect logs if needed:

	```bash
	sudo journalctl -u patient-monitor.service -f
	```

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

## Continuous Integration

- GitHub Actions runs on pushes to `main` and on pull requests.
- The workflow installs dependencies and validates `main.py` with `python -m py_compile`.
- The workflow file is stored in [.github/workflows/ci.yml](/workspaces/IOT/.github/workflows/ci.yml).