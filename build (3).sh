#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Repair the November 9 design upload, whose folders were flattened by upload.
# Only the original presentation files (or missing files) are repaired.
# Later edits in static/ and templates/ take precedence over the loose copies.
python - <<'PY'
import hashlib
from pathlib import Path

def blob_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

# source, correct destination, approved design hash, original destination hash
files = [
    ('app.css', 'static/app.css', '6d53ea46876e8d8c10c6cd3d78affcd71d795eca', '104d85c9241eb2037279fc0f1d51d5617f99ca7b'),
    ('base.html', 'templates/base.html', 'd36f00bc825da0662319d654f9ca6294c343bdd8', '01cc020237acc46861efda3c5c38931f7b5f20b7'),
    ('dashboard.html', 'templates/dashboard.html', '71756a2c13e75d429c5af63f7b49ae232dd8e26a', '529a5896a30739eb5162d4be60a8eb645a795998'),
    ('login.html', 'templates/registration/login.html', '7a048d0e6bc94ca7589458be8d01b1b06706207a', 'bd72f7767dccb43f965b033e97f7a9282a8a82c2'),
    ('globe.html', 'templates/brand/globe.html', '027e13691a10192ddcda4ce8b5ecdad589b5c046', None),
    ('pallet_wrapper.html', 'templates/brand/pallet_wrapper.html', '075be78cb376cee3f4ad7fe71f0d1e0e9841fde7', None),
    ('globe-motion.js', 'static/brand/globe-motion.js', 'd0fbd727824c6212d06c0c5e63cc503212ba8a60', None),
    ('pallet-wrapper.js', 'static/brand/pallet-wrapper.js', 'e12066a8bf0d2f30a819bfe75cc2178f9f479873', None),
    ('s3pl-logo.png', 'static/brand/s3pl-logo.png', 'd6d851d95bff530d3401f5c52e37f31ef5fda039', None),
]

pending = []
for source, destination, design_sha, original_sha in files:
    target = Path(destination)
    if target.exists():
        current_sha = blob_sha(target.read_bytes())
        if current_sha == design_sha:
            continue
        if current_sha != original_sha:
            print(f'S3PL design: preserving later edit at {destination}')
            continue
    origin = Path(source)
    if not origin.is_file():
        raise SystemExit(f'S3PL design repair: missing {source}; upload the design files before deploying.')
    data = origin.read_bytes()
    if blob_sha(data) != design_sha:
        raise SystemExit(f'S3PL design repair: {source} does not match the prepared design; no repair files were written.')
    pending.append((target, data))

for target, data in pending:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f'S3PL design: installed {target}')
print(f'S3PL design ready ({len(pending)} presentation files repaired).')
PY

python -m pip install -r requirements.txt
python manage.py collectstatic --noinput
