# Building the Blinky disk image

Run the packaging script from a checkout on the Mac that will build Blinky.

```bash
./scripts/build-dmg.sh
```

The script creates a disk image in `dist/`. Open it in Finder and drag Blinky into Applications. The disk image contains a private copy of CPython, `blinkpy` and its dependencies, and an FFmpeg build for Live View. Build on Apple Silicon for `arm64` or on Intel for `x86_64`. The filename includes the architecture. The build Mac needs Xcode and internet access to retrieve the pinned Python and FFmpeg distributions and the Python wheels.

The runtime licenses are in the app’s `Contents/Resources` folder. Publish `ffmpeg-9.1.tar.xz` and `ffmpeg-9.1-build.txt` from `dist/` alongside the DMG so recipients can access the corresponding FFmpeg source and build instructions. [FFmpeg distribution guidance](https://ffmpeg.org/legal.html).

The default disk image is for local testing and has `-local` in its filename. For a public download, install a **Developer ID Application** signing identity and [create a notarytool keychain profile](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow) on the build Mac. Use the identity and profile name when packaging.

```bash
BLINKY_SIGN_IDENTITY='Developer ID Application: Your Name (TEAMID)' \
BLINKY_NOTARY_PROFILE='blinky-notary' \
./scripts/build-dmg.sh
```

The script signs the app with Hardened Runtime, signs the disk image, submits it for notarization, and staples and validates the ticket. Share the resulting DMG without the `-local` suffix. Recipients install it from Finder. [Apple's packaging instructions](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution).
