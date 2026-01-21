#!/usr/bin/env python3
"""
Kiwix Setup Script for groundwave-ai

Downloads and sets up Kiwix tools and Wikipedia for offline AI knowledge.
"""

import os
import sys
import platform
import subprocess
import tarfile
import shutil
from pathlib import Path

# Configuration
KIWIX_VERSION = "3.8.1"
DATA_DIR = Path(__file__).parent.parent / "data" / "kiwix"

# Kiwix tools download URLs by platform
KIWIX_URLS = {
    "darwin_arm64": f"https://download.kiwix.org/release/kiwix-tools/kiwix-tools_macos-arm64-{KIWIX_VERSION}.tar.gz",
    "darwin_x86_64": f"https://download.kiwix.org/release/kiwix-tools/kiwix-tools_macos-x86_64-{KIWIX_VERSION}.tar.gz",
    "linux_x86_64": f"https://download.kiwix.org/release/kiwix-tools/kiwix-tools_linux-x86_64-{KIWIX_VERSION}.tar.gz",
    "linux_aarch64": f"https://download.kiwix.org/release/kiwix-tools/kiwix-tools_linux-aarch64-{KIWIX_VERSION}.tar.gz",
}

# Wikipedia ZIM files (Simple English is recommended - much smaller)
WIKIPEDIA_ZIMS = {
    "simple": {
        "name": "Simple English Wikipedia (recommended)",
        "url": "https://download.kiwix.org/zim/wikipedia/wikipedia_en_simple_all_nopic_2025-01.zim",
        "size": "~900 MB"
    },
    "full": {
        "name": "Full English Wikipedia (large!)",
        "url": "https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_nopic_2024-10.zim",
        "size": "~25 GB"
    }
}


def get_platform_key():
    """Get the platform key for downloads."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    if system == "darwin":
        if machine == "arm64":
            return "darwin_arm64"
        return "darwin_x86_64"
    elif system == "linux":
        if machine == "aarch64":
            return "linux_aarch64"
        return "linux_x86_64"
    else:
        print(f"Unsupported platform: {system} {machine}")
        sys.exit(1)


def download_file(url: str, dest: Path, desc: str = ""):
    """Download a file with progress."""
    import urllib.request
    
    print(f"Downloading {desc or url}...")
    print(f"  URL: {url}")
    print(f"  Destination: {dest}")
    
    def progress_hook(count, block_size, total_size):
        if total_size > 0:
            percent = int(count * block_size * 100 / total_size)
            mb_done = count * block_size / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            print(f"\r  Progress: {percent}% ({mb_done:.1f}/{mb_total:.1f} MB)", end="", flush=True)
    
    try:
        urllib.request.urlretrieve(url, dest, progress_hook)
        print()  # newline after progress
        return True
    except Exception as e:
        print(f"\n  Error: {e}")
        return False


def setup_kiwix_tools():
    """Download and extract Kiwix tools."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    platform_key = get_platform_key()
    url = KIWIX_URLS.get(platform_key)
    
    if not url:
        print(f"No Kiwix tools available for {platform_key}")
        return None
    
    tarball = DATA_DIR / "kiwix-tools.tar.gz"
    
    # Download if not exists
    if not tarball.exists():
        if not download_file(url, tarball, "Kiwix tools"):
            return None
    else:
        print(f"Kiwix tools already downloaded: {tarball}")
    
    # Extract
    extract_dir = DATA_DIR / f"kiwix-tools_{platform_key.replace('_', '-')}-{KIWIX_VERSION}"
    if not extract_dir.exists():
        print(f"Extracting to {extract_dir}...")
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(DATA_DIR)
    
    # Find kiwix-serve
    kiwix_serve = None
    for pattern in ["kiwix-serve", "*/kiwix-serve"]:
        matches = list(DATA_DIR.glob(f"*/{pattern}"))
        if matches:
            kiwix_serve = matches[0]
            break
    
    if kiwix_serve and kiwix_serve.exists():
        print(f"Kiwix tools ready: {kiwix_serve}")
        return kiwix_serve
    else:
        print("Could not find kiwix-serve after extraction")
        return None


def setup_wikipedia(edition: str = "simple"):
    """Download Wikipedia ZIM file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    if edition not in WIKIPEDIA_ZIMS:
        print(f"Unknown edition: {edition}")
        return None
    
    zim_info = WIKIPEDIA_ZIMS[edition]
    zim_file = DATA_DIR / f"wikipedia_{edition}.zim"
    
    if zim_file.exists():
        print(f"Wikipedia already downloaded: {zim_file}")
        return zim_file
    
    print(f"\nDownloading {zim_info['name']} ({zim_info['size']})...")
    print("This may take a while...")
    
    if download_file(zim_info["url"], zim_file, zim_info["name"]):
        print(f"Wikipedia downloaded: {zim_file}")
        return zim_file
    
    return None


def create_start_script(kiwix_serve: Path, zim_file: Path):
    """Create a script to start the Kiwix server."""
    script_path = DATA_DIR / "start-kiwix.sh"
    
    script_content = f"""#!/bin/bash
# Start Kiwix server for groundwave-ai
cd "{DATA_DIR}"
"{kiwix_serve}" --port 8080 "{zim_file}"
"""
    
    with open(script_path, "w") as f:
        f.write(script_content)
    
    os.chmod(script_path, 0o755)
    print(f"\nStart script created: {script_path}")
    return script_path


def main():
    print("=" * 60)
    print("groundwave-ai Kiwix Setup")
    print("=" * 60)
    print()
    
    # Step 1: Download Kiwix tools
    print("[1/3] Setting up Kiwix tools...")
    kiwix_serve = setup_kiwix_tools()
    if not kiwix_serve:
        print("Failed to setup Kiwix tools")
        sys.exit(1)
    
    # Step 2: Ask which Wikipedia edition
    print()
    print("[2/3] Choose Wikipedia edition:")
    print("  1. Simple English (recommended, ~900 MB)")
    print("  2. Full English (large, ~25 GB)")
    print("  3. Skip (I'll download manually)")
    
    choice = input("\nChoice [1]: ").strip() or "1"
    
    zim_file = None
    if choice == "1":
        zim_file = setup_wikipedia("simple")
    elif choice == "2":
        zim_file = setup_wikipedia("full")
    elif choice == "3":
        print("Skipping Wikipedia download")
    else:
        print("Invalid choice, skipping")
    
    # Step 3: Create start script
    if kiwix_serve and zim_file:
        print()
        print("[3/3] Creating start script...")
        start_script = create_start_script(kiwix_serve, zim_file)
        
        print()
        print("=" * 60)
        print("Setup Complete!")
        print("=" * 60)
        print()
        print("To start Kiwix server:")
        print(f"  {start_script}")
        print()
        print("Then configure in groundwave-ai:")
        print("  - Kiwix URL: http://localhost:8080")
        print("  - Library: wikipedia_en_simple")
        print("  - Enable Kiwix in admin panel or config.yaml")
    else:
        print()
        print("Partial setup complete. Download a ZIM file manually from:")
        print("  https://download.kiwix.org/zim/wikipedia/")


if __name__ == "__main__":
    main()
