# Local Blink bridge

Blinky uses `blinkpy==0.25.9`, a community library for Blink's cloud service. The native app runs `bridge.py` as a child process without opening an HTTP port. Python 3.10 or later is required; prepare the environment with `scripts/bootstrap.sh`.

## Protocol

Each request and response occupies one JSON line. Request: `{"id":"identifier","method":"health","params":{}}`. Response: `{"id":"identifier","result":{...}}` or `{"id":"identifier","error":{"code":"code","message":"safe message"}}`.

The channel contains sensitive data and must not be logged. The bridge discards stdout/stderr from external libraries and silences their logs, which may contain authenticated URLs or identifiers. It does not accept download paths or URLs supplied by the client: `download_clip` only accepts the ID of a clip previously discovered in the account.

| Method | Parameters | Result |
| --- | --- | --- |
| `health` | None | Bridge/Python/blinkpy versions and bridge availability; works without blinkpy installed. |
| `login` | `username`, `password` | `status`, `message`, and `session`; may return `requires_2fa`. |
| `verify` | `code` | Completes the verification started by `login`. |
| `restore` | `session` | Restores a session using its refresh token, without a password. |
| `session` | None | Current session, including tokens refreshed during an operation. |
| `logout` | None | Discards the process session and closes local connections. |
| `snapshot` | None | `systems`, `cameras`, `clips`, `refreshedAt`. |
| `set_system_armed` | `system_id`, `armed` (boolean) | Requests arming or disarming the system. |
| `set_motion_enabled` | `camera_id`, `enabled` (boolean) | Requests enabling or disabling motion detection. |
| `refresh_thumbnail` | `camera_id` | Requests a new image from the camera. |
| `record` | `camera_id` | Requests a clip; does not start continuous recording. |
| `download_clip` | `clip_id` | `dataBase64`, `mimeType`, `filename` for export by the app. |

`session` is a string containing a JSON object, suitable for storage in macOS Keychain. The bridge does not create credential, session, or media files. The password and PIN are removed when authentication completes and are excluded from the serialized session. The client must store the session returned by `login`/`verify`/`restore` and query `session` again after operations to persist token rotation. A revoked refresh token requires signing in again; there is no automatic retry with a password.

Camera IDs are opaque: `<network_id>:<camera_type>:<camera_id>`. The `armed` and `motionEnabled` states may be `null` when Blink does not report them; they must not be interpreted as disabled. `updatedAt` is only provided when the image timestamp can be extracted from Blink's image URL; `refreshedAt` indicates when the state was refreshed, not when every image was captured.

## Initial version limits

- State and clip lists are cached for at least 60 seconds. An explicit change allows an additional refresh to check the result. New image or clip requests have a minimum interval of 60 seconds per camera. Detection does not provide real-time notifications.
- Clips: queries cover at most the first two cloud pages (up to 50 items) from the last 48 hours. Access depends on the recordings available to the account. Clips stored only on USB are not queried in this version.
- Each download is limited to 20 MB, and HTTP responses are read in chunks. Live video, continuous recording, two-way audio, and automatic downloads are not provided.
- `logout` disconnects locally; it does not guarantee token revocation on Blink's servers. The app must also remove the session from Keychain.
- The library uses private Blink interfaces, which may change. Automated tests use substitutes for the account and cameras; real compatibility requires testing with the user's account.

## Verified sources

- [blinkpy v0.25.9 release](https://github.com/fronzbot/blinkpy/releases/tag/v0.25.9).
- [Authentication and session attributes in this release](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/auth.py).
- [Startup, verification, and clip metadata](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/blinkpy.py).
- [Camera methods](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/camera.py).
