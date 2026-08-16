import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PROJECT_ROOT / "wimi_analytics"
RUNTIME_DIR = PACKAGE_DIR / "runtime" / "python"
MODEL_DIR = PACKAGE_DIR / "models"
MANIFEST_PATH = PACKAGE_DIR / "model_manifest.json"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified(path, item):
    return (
        path.is_file()
        and path.stat().st_size == int(item["size"])
        and sha256_file(path) == item["sha256"]
    )


def install_runtime(manifest):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    packages = [
        f"numpy=={manifest['numpy']}",
        f"opencv-python-headless=={manifest['opencv_python_headless']}",
    ]
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--upgrade",
        "--target",
        str(RUNTIME_DIR),
        *packages,
    ]
    subprocess.run(command, check=True)


def download_model(item):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    destination = MODEL_DIR / item["filename"]
    if verified(destination, item):
        print(f"OK modelo verificado: {destination.name}")
        return
    temporary = destination.with_suffix(destination.suffix + ".download")
    try:
        request = urllib.request.Request(item["url"], headers={"User-Agent": "WIMI-NVR-Setup/1"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if not verified(temporary, item):
            raise RuntimeError(f"hash_or_size_mismatch:{item['filename']}")
        os.replace(temporary, destination)
        print(f"OK modelo instalado: {destination.name}")
    finally:
        if temporary.exists():
            temporary.unlink()


def verify_runtime(manifest):
    code = (
        "import cv2,numpy; from importlib.metadata import version; "
        "print('cv2='+cv2.__version__); "
        "print('numpy='+numpy.__version__); "
        "print('opencv-wheel='+version('opencv-python-headless')); "
        "assert version('opencv-python-headless') == "
        + repr(manifest["opencv_python_headless"])
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(RUNTIME_DIR)
    subprocess.run([sys.executable, "-c", code], check=True, env=environment)


def main():
    parser = argparse.ArgumentParser(description="Configura a visao local isolada do WIMI")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not args.verify_only:
        install_runtime(manifest)
        for item in manifest["models"].values():
            download_model(item)
    verify_runtime(manifest)
    for item in manifest["models"].values():
        path = MODEL_DIR / item["filename"]
        if not verified(path, item):
            raise RuntimeError(f"model_not_verified:{item['filename']}")
    print("WIMI Vision pronto: runtime isolado e modelos verificados.")


if __name__ == "__main__":
    main()
