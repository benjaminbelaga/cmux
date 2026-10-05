#!/usr/bin/env python3
"""Exercise canonical signing against an already-built isolated sidebar canary.

The artifact must already have its nested code signed by the normal all pass.
This runs the supported main-only final pass and every canonical verification.
It never builds, launches, installs, or touches the production application.
"""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--identity', required=True)
    args = parser.parse_args()
    app = args.app.resolve(strict=True)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    assert info['CFBundleIdentifier'].startswith('com.cmuxterm.app.debug.'), 'isolated tag required'
    assert not str(app).startswith('/Applications/'), 'installed applications forbidden'
    executable = info['CFBundleExecutable']
    assert executable != 'cmux', 'regression needs the real tagged executable name'
    assert '/' not in executable and executable not in ('', '.', '..')
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, CMUX_SIGN_MODE='main-only')
    subprocess.run([
        str(root / 'scripts/sign-cmux-bundle.sh'), str(app),
        str(root / 'cmux.sidebar-canary.entitlements'), args.identity,
    ], cwd=root, env=env, check=True, timeout=120)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    actual = subprocess.check_output(['/usr/bin/lipo', '-archs', str(app / 'Contents/MacOS' / executable)], text=True).split()
    sidecar = subprocess.check_output(['/usr/bin/lipo', '-archs', str(app / 'Contents/Resources/bin/cmux-diff-sidecar')], text=True).split()
    assert actual and sorted(actual) == sorted(sidecar)
    print('tagged executable signing: 1 artifact verified; exact slices and strict nested signatures')


if __name__ == '__main__':
    main()
