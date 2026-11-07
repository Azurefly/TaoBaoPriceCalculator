"""Validate a human's artifact-specific acceptance record; does not manufacture test evidence."""
import argparse
import json
from pathlib import Path
from release_policy import validate_acceptance

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    record = json.loads(args.evidence.read_text(encoding='utf-8'))
    manifest = validate_acceptance(args.root, record)
    print('Acceptance record matches all five candidate installers: ' + manifest['version'])
