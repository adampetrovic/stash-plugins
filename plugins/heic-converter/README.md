# HEIC to JPEG Converter

A Stash plugin that converts HEIC/HEIF image files to full quality JPEG using ImageMagick.

## Why?

Stash does not natively support HEIC/HEIF files — they are ignored during scans and never indexed. This plugin bridges that gap by converting them to JPEG so Stash can pick them up.

## How it works

1. Queries Stash for configured library paths
2. Walks those directories looking for `.heic` / `.heif` files
3. Converts each to a `.jpg` at quality 100 using ImageMagick
4. Deletes the original HEIC file
5. Triggers a Stash metadata scan to index the new JPEGs

## Requirements

- **Python 3** (stdlib only — no pip packages needed)
- **ImageMagick** with HEIC/HEIF support (`magick` or `convert` binary on PATH)

### Alpine / Docker

```sh
apk add --no-cache python3 imagemagick libheif
```

## Tasks

| Task | Description |
|------|-------------|
| **Convert HEIC to JPEG** | Find and convert all HEIC/HEIF files, delete originals, trigger scan |
| **Dry Run - Scan for HEIC files** | List all HEIC/HEIF files found without converting |

## Installation

### Via plugin source index

Add this source URL in **Settings → Plugins → Available Plugins**:

```
https://adampetrovic.github.io/stash-plugins/main/index.yml
```

Then install "HEIC to JPEG Converter" from the available plugins list.

### Manual

Copy the `heic-converter` directory into your Stash `plugins` folder and reload plugins.
