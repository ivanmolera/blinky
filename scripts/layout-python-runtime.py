"""Place bundled Python's native code in standard macOS bundle locations."""

import os
from pathlib import Path
import platform
import re
import subprocess
import sys


MACH_O = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}
SYSTEM_LIBRARIES = ("/usr/lib/", "/System/Library/")


def native_code(path):
    if path.is_file() and not path.is_symlink():
        with path.open("rb") as stream:
            return stream.read(4) in MACH_O
    return False


def relocate(contents):
    python = contents / "Resources/Python"
    helpers = contents / "Helpers"
    frameworks = contents / "Frameworks"
    launcher = (python / "bin/python3").resolve(strict=True)
    helpers.mkdir(exist_ok=True)
    frameworks.mkdir(exist_ok=True)

    files = [path for path in sorted(python.rglob("*")) if native_code(path)]
    if launcher not in files:
        raise ValueError("Missing bundled Python launcher")
    locations = {}
    for source in files:
        target = helpers / "python3" if source == launcher else frameworks / source.name
        if target.exists() or target in locations.values():
            raise ValueError(f"Duplicate bundled code name {target.name}")
        locations[source] = target

    for source, target in locations.items():
        source.rename(target)
        source.symlink_to(os.path.relpath(target, source.parent))

    libraries = {path.name for path in locations.values() if path.parent == frameworks}
    arch = {"aarch64": "arm64"}.get(platform.machine(), platform.machine())
    for binary in locations.values():
        architectures = subprocess.check_output(["lipo", "-archs", binary], text=True).split()
        if arch not in architectures:
            raise ValueError(f"Bundled code {binary.name} does not support {arch}")
        if len(architectures) > 1:
            thin = binary.with_name(f"{binary.name}.thin")
            subprocess.run(["lipo", binary, "-thin", arch, "-output", thin], check=True)
            thin.chmod(binary.stat().st_mode & 0o777)
            thin.replace(binary)
        dependencies = subprocess.check_output(["otool", "-L", binary], text=True).splitlines()[1:]
        if subprocess.check_output(["otool", "-D", binary], text=True).splitlines()[1:]:
            dependencies = dependencies[1:]  # The first entry is the library's install name.
        changes = []
        for entry in dependencies:
            dependency = entry.strip().split(" (", 1)[0]
            if dependency.startswith(SYSTEM_LIBRARIES):
                continue
            name = Path(dependency).name
            if dependency.startswith("/") or name not in libraries:
                raise ValueError(f"External library dependency {dependency} in {binary.name}")
            relative = "../Frameworks/" if binary.parent == helpers else ""
            adjusted = f"@loader_path/{relative}{name}"
            if dependency != adjusted:
                changes.extend(["-change", dependency, adjusted])

        commands = subprocess.check_output(["otool", "-l", binary], text=True)
        for rpath in re.findall(r"\bcmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset \d+\)", commands):
            changes.extend(["-delete_rpath", rpath])
        if changes:
            subprocess.run(["install_name_tool", *changes, binary], check=True)


if __name__ == "__main__":
    relocate(Path(sys.argv[1]).resolve(strict=True))
