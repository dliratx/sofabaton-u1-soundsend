"""Check local offline build prerequisites without network or device access."""
import hashlib
import importlib.metadata
from pathlib import Path

root = Path(__file__).resolve().parents[1]
required = {
    'research/zig-toolchain/zig-x86_64-windows-0.15.2/zig.exe': None,
    'research/firmware-candidates/OTA_U1_5_V32.bin':
        '6facfda529024239abfa7eb7293d9d4d2ca17c11008c245699de94e4b5c49fc7',
    'captures/volume-roundtrip-01.jsonl': None,
}
errors = []
for relative, digest in required.items():
    path = root / relative
    if not path.is_file():
        errors.append(f'Missing: {relative}')
    elif digest and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        errors.append(f'Wrong SHA-256: {relative}')
    else:
        print(f'OK: {relative}')
for package, expected in [('bleak', '3.0.2'), ('unicorn', '2.1.4'), ('capstone', '5.0.9')]:
    try:
        version = importlib.metadata.version(package)
        if version != expected:
            errors.append(f'{package}: expected {expected}, found {version}')
        else:
            print(f'OK: {package} {version}')
    except importlib.metadata.PackageNotFoundError:
        errors.append(f'Missing Python package: {package}')
for error in errors:
    print(error)
raise SystemExit(bool(errors))
