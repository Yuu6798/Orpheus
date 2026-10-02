"""Thin wrapper around an already-installed Demucs CLI; no model reimplementation."""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--audio', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--model', default='htdemucs')
    p.add_argument('--device', default='cpu')
    args = p.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run([sys.executable, '-m', 'demucs', '--two-stems=vocals', '-n', args.model,
                        '-d', args.device, '-o', folder, args.audio], check=True, shell=False)
        vocals = Path(folder) / args.model / Path(args.audio).stem / 'vocals.wav'
        shutil.copyfile(vocals, args.output)


if __name__ == '__main__':
    main()
