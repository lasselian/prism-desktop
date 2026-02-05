#!/usr/bin/env python3
"""
Linux build script for PrismDesktop.

Builds a standalone executable using PyInstaller.
Run this on a Linux machine.

Usage:
    python3 build_linux.py
"""

import subprocess
import sys
import platform

def main():
    if platform.system() != 'Linux':
        print("Warning: This script is intended to be run on Linux.")
        print(f"Current platform: {platform.system()}")
        response = input("Continue anyway? (y/N): ")
        if response.lower() != 'y':
            sys.exit(1)
    
    # Ensure PyInstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyinstaller'], check=True)
    
    # Build the application
    pyinstaller_args = [
        sys.executable, '-m', 'PyInstaller',
        'main.py',
        '--name=PrismDesktop',
        '--onefile',
        '--windowed',
        '--add-data=materialdesignicons-webfont.ttf:.',
        '--add-data=mdi_mapping.json:.',
        '--add-data=icon.png:.',
        '--icon=icon.png',
        '--clean',
    ]
    
    print("Building PrismDesktop for Linux...")
    print(f"Command: {' '.join(pyinstaller_args)}")
    
    result = subprocess.run(pyinstaller_args)
    
    if result.returncode == 0:
        print("\n✅ Build successful!")
        print("Output: dist/PrismDesktop")
        print("\nTo run: ./dist/PrismDesktop")
    else:
        print("\n❌ Build failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
