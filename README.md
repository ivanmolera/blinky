# Blinky

A native macOS app for accessing your Blink cameras from your Mac. The interface uses SwiftUI and connects to Blink through a small Python process using [blinkpy](https://github.com/fronzbot/blinkpy). This is an independent project, with no affiliation with Blink or Amazon.

## Requirements

- macOS 14 or later.
- Xcode 15.3 or later, with Swift 5.10 or later.
- Python 3.10 or later to connect a Blink account. Demo mode does not require Python.

## Try the demo

From the project directory:

```sh
./scripts/run.sh --demo
```

Blinky opens with sample data so you can explore the interface without connecting cameras. Without `--demo`, it displays the sign-in screen or restores your saved session. You can also open `Package.swift` in Xcode and run the Blinky scheme.

## Connect your cameras

First, prepare the local Python environment:

```sh
./scripts/bootstrap.sh
./scripts/run.sh
```

The first command creates `.venv` and installs the blinkpy version specified in `Sources/Blinky/Bridge/requirements.txt`. It prefers Python 3.13/3.12/3.11/3.10 when available and falls back to `python3`. You can choose an interpreter with `BLINKY_BOOTSTRAP_PYTHON=/path/to/python3 ./scripts/bootstrap.sh`. The script does not install system tools or require `sudo`.

Connect your Blink account in the app and complete any verification requested by Blink. Connecting a real account requires Internet access. You do not need to keep Blinky open to continue using the official Blink app.

## Build an app for Finder

```sh
./scripts/build-app.sh
open dist/Blinky.app
```

The script builds the release configuration and creates `dist/Blinky.app` with a local ad hoc signature. It does not install the app in `/Applications` or use developer certificates.

If you have run `bootstrap.sh`, the generated app stores the path to the `.venv` interpreter in `Info.plist`. Keep the project directory at that location. If you move it, prepare the environment and build the app again. This package is intended for local use: it does not yet bundle a Python interpreter for distribution and is not notarized.

To use a different interpreter when running from Terminal:

```sh
BLINKY_PYTHON=/path/to/python3 ./scripts/run.sh
```

The interpreter must have the bridge dependencies installed. `BLINKY_BRIDGE_PATH` lets you specify a different path to `bridge.py` during development.

## Current scope

The app includes sign-in and two-step verification, session restoration through Keychain, system and camera lists, snapshot and recording requests, system and motion detection controls, and manual MP4 clip export. The bridge implementation is in place; it still needs validation with a real account and cameras.

This version retrieves clips from the cloud: up to 50 clips from the last 48 hours, with a maximum of 20 MB per download. Access depends on the recordings available to your account. Clips stored only on USB are not yet supported. See [the protocol and its limits](docs/bridge.md).

Live video is not yet implemented. Blink uses cloud services: this app does not turn the cameras into local cameras or provide continuous recording. The integration uses a community library, and changes to Blink's authentication or protocol may require an update. Frequently refreshing images or querying cameras can increase battery consumption.

## Development and checks

```sh
swift test
swift build -c release
python3 -m unittest discover -s Tests/Python -p 'test_*.py'
```

The bridge tests run without a Blink account. The real connection, camera content, and account authentication require manual verification.

Do not commit passwords, verification codes, tokens, clips, or logs containing private data to the repository. `.gitignore` excludes `.venv`, build output, and `.env` files.
