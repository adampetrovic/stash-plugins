"""
HEIC to JPEG Converter for Stash

Scans Stash library paths for HEIC/HEIF image files and converts them to
full quality JPEG using ImageMagick. Since Stash does not natively support
HEIC files, this plugin works at the filesystem level — walking the configured
library directories and converting any .heic/.heif files it finds.

After conversion, run a Stash scan to index the new JPEG files.

Requirements:
  - python3 (stdlib only, no pip packages)
  - imagemagick with HEIC support (magick or convert binary)
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HEIC_EXTENSIONS = {".heic", ".heif"}


# ---------------------------------------------------------------------------
# Logging – external plugins log via stderr with SOH <level> STX prefix
# See: pkg/plugin/common/log in the stash source
# ---------------------------------------------------------------------------

def _log(level_char: str, msg: str) -> None:
    print(f"\x01{level_char}\x02{msg}\n", file=sys.stderr, flush=True)


def log_trace(msg: str) -> None:
    _log("t", msg)


def log_debug(msg: str) -> None:
    _log("d", msg)


def log_info(msg: str) -> None:
    _log("i", msg)


def log_warning(msg: str) -> None:
    _log("w", msg)


def log_error(msg: str) -> None:
    _log("e", msg)


def log_progress(value: float) -> None:
    _log("p", str(min(max(0.0, value), 1.0)))


# ---------------------------------------------------------------------------
# GraphQL helper – uses urllib so we don't need `requests`
# ---------------------------------------------------------------------------

def graphql_request(connection: dict, query: str, variables: dict | None = None) -> dict:
    scheme = connection.get("Scheme", "http")
    port = connection.get("Port", 9999)
    url = f"{scheme}://localhost:{port}/graphql"

    payload = json.dumps({"query": query, "variables": variables or {}}).encode()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    cookie = connection.get("SessionCookie")
    if cookie and cookie.get("Value"):
        headers["Cookie"] = f"{cookie['Name']}={cookie['Value']}"

    req = urllib.request.Request(url, data=payload, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GraphQL request failed: {exc}") from exc

    if "errors" in result:
        raise RuntimeError(f"GraphQL errors: {result['errors']}")

    return result.get("data", {})


# ---------------------------------------------------------------------------
# Stash API helpers
# ---------------------------------------------------------------------------

def get_library_paths(connection: dict) -> list[str]:
    """Return the list of library (stash) paths configured in Stash."""
    query = """
    query Configuration {
        configuration {
            general {
                stashes {
                    path
                }
            }
        }
    }
    """
    data = graphql_request(connection, query)
    stashes = data["configuration"]["general"]["stashes"]
    return [s["path"] for s in stashes]


def trigger_scan(connection: dict) -> None:
    """Ask Stash to start a metadata scan."""
    mutation = """
    mutation MetadataScan {
        metadataScan(input: {})
    }
    """
    try:
        graphql_request(connection, mutation)
        log_info("Triggered Stash metadata scan")
    except Exception as exc:
        log_warning(f"Could not trigger scan: {exc}")


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def find_heic_files(paths: list[str]) -> list[str]:
    """Walk the given directory paths and return all HEIC/HEIF file paths."""
    found: list[str] = []
    for base_path in paths:
        if not os.path.isdir(base_path):
            log_warning(f"Library path does not exist or is not a directory: {base_path}")
            continue
        for root, _dirs, files in os.walk(base_path):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in HEIC_EXTENSIONS:
                    found.append(os.path.join(root, fname))
    return sorted(found)


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _find_magick_binary() -> list[str]:
    """Return the ImageMagick convert command as a list of args."""
    # ImageMagick 7: `magick convert` (preferred)
    if shutil.which("magick"):
        return ["magick"]
    # ImageMagick 6 / Alpine compat: `convert`
    if shutil.which("convert"):
        return ["convert"]
    raise RuntimeError(
        "ImageMagick not found. Ensure 'magick' or 'convert' is installed and on PATH."
    )


def convert_heic_to_jpeg(heic_path: str, magick_cmd: list[str]) -> str | None:
    """
    Convert a HEIC file to a JPEG at quality 100.

    Returns the output JPEG path on success, or None on failure.
    The original HEIC file is NOT deleted here — the caller decides.
    """
    base, _ = os.path.splitext(heic_path)
    jpeg_path = base + ".jpg"

    if os.path.exists(jpeg_path):
        log_warning(f"JPEG already exists, skipping: {jpeg_path}")
        return None

    cmd = magick_cmd + [heic_path, "-quality", "100", jpeg_path]
    log_debug(f"Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        log_error(f"ImageMagick failed for {heic_path}: {result.stderr.strip()}")
        # Clean up partial output
        if os.path.exists(jpeg_path):
            os.remove(jpeg_path)
        return None

    return jpeg_path


# ---------------------------------------------------------------------------
# Plugin modes
# ---------------------------------------------------------------------------

def mode_scan(paths: list[str]) -> str:
    """Dry-run: list HEIC files without converting."""
    heic_files = find_heic_files(paths)

    if not heic_files:
        log_info("No HEIC/HEIF files found in library paths")
        return "No HEIC/HEIF files found"

    log_info(f"Found {len(heic_files)} HEIC/HEIF file(s):")
    for f in heic_files:
        log_info(f"  {f}")

    return f"Found {len(heic_files)} HEIC/HEIF file(s)"


def mode_convert(connection: dict, paths: list[str]) -> str:
    """Convert all HEIC files to JPEG, delete originals, trigger scan."""
    heic_files = find_heic_files(paths)

    if not heic_files:
        log_info("No HEIC/HEIF files found in library paths")
        return "No HEIC/HEIF files found"

    log_info(f"Found {len(heic_files)} HEIC/HEIF file(s) to convert")

    magick_cmd = _find_magick_binary()
    log_info(f"Using ImageMagick command: {' '.join(magick_cmd)}")

    converted = 0
    skipped = 0
    failed = 0

    for i, heic_path in enumerate(heic_files):
        log_info(f"[{i + 1}/{len(heic_files)}] Converting: {heic_path}")

        jpeg_path = convert_heic_to_jpeg(heic_path, magick_cmd)

        if jpeg_path is None:
            skipped += 1
        else:
            try:
                os.remove(heic_path)
                log_info(f"  → {jpeg_path} (original deleted)")
                converted += 1
            except OSError as exc:
                log_error(f"  Converted but failed to delete original: {exc}")
                failed += 1

        log_progress((i + 1) / len(heic_files))

    msg = f"Done: {converted} converted, {skipped} skipped, {failed} failed"
    log_info(msg)

    if converted > 0:
        log_info("Triggering Stash scan to index new JPEG files...")
        trigger_scan(connection)

    return msg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    raw_input = sys.stdin.read()
    try:
        json_input = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Failed to parse input: {exc}"}))
        sys.exit(1)

    connection = json_input.get("server_connection", {})
    mode = json_input.get("args", {}).get("mode", "convert")

    try:
        log_info(f"HEIC Converter starting (mode: {mode})")

        paths = get_library_paths(connection)
        log_info(f"Library paths: {', '.join(paths)}")

        if mode == "scan":
            result = mode_scan(paths)
        elif mode == "convert":
            result = mode_convert(connection, paths)
        else:
            result = f"Unknown mode: {mode}"
            log_error(result)

        print(json.dumps({"output": result}))

    except Exception as exc:
        log_error(f"Plugin failed: {exc}")
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
