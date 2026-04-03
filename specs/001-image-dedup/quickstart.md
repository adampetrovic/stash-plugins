# Quickstart: Image Deduplication Plugin

**Branch**: `001-image-dedup` | **Date**: 2026-04-03

## Prerequisites

- **Stash** running (Docker or bare-metal)
- **Python 3.10+** (bundled with Stash Docker image)
- **ImageMagick** with `magick` or `convert` binary on PATH
  - Docker (Alpine): `apk add imagemagick`
  - macOS: `brew install imagemagick`
  - Debian/Ubuntu: `apt-get install imagemagick`

## Installation

1. Copy the `plugins/image-dedup/` directory into your Stash plugins folder
2. Reload plugins in Stash: **Settings → Plugins → Reload Plugins**
3. Verify "Image Deduplication" appears in the plugin list

## Usage

### First Run: Batch Scan
1. Go to **Settings → Tasks**
2. Under "Image Deduplication", click **Find Duplicates**
3. Wait for the scan to complete (progress shown in task bar)
4. Browse duplicate groups: filter images by tags matching `dedup:group:*`

### Automatic Detection
After the initial batch scan, new images added via Stash scan are automatically fingerprinted and checked for duplicates. No action needed — duplicates are tagged immediately.

### Reviewing Duplicates
1. In the image browser, filter by a `dedup:group:NNNN` tag to see a group
2. Compare resolution, file size, and metadata across group members
3. Optionally remove the group tag from any image you disagree with as a duplicate

### Resolving Duplicates
1. Review groups first (optional but recommended)
2. Run **Resolve Duplicates** task
3. For each group: highest resolution image is kept, metadata merged, others deleted
4. Run **Dry Run - Resolve Duplicates** first if you want to preview actions

### Cleanup
Run the **Cleanup** task periodically to:
- Remove fingerprints for deleted images
- Clean up empty or single-member duplicate groups
- Remove orphaned group tags

## File Structure

```
plugins/image-dedup/
├── image-dedup.yml          # Plugin manifest (hooks, tasks)
├── image_dedup.py           # Main plugin script
├── README.md                # User documentation
└── tests/
    ├── __init__.py
    ├── test_fingerprint.py  # Perceptual hash generation tests
    ├── test_grouping.py     # Union-Find / transitive grouping tests
    ├── test_comparison.py   # Similarity comparison tests
    ├── test_resolution.py   # Keeper selection / metadata merge tests
    ├── test_modes.py        # Plugin mode (scan/resolve/hook) tests
    └── fixtures/            # Test images (originals + variants)
```

## Configuration

The plugin uses these Stash tags (created automatically):
- `auto:dedup` — marks images that have been fingerprinted
- `dedup:group:NNNN` — identifies duplicate group membership

Similarity threshold default: Hamming distance ≤ 10 (tunable in code; conservative precision-focused default).

## Development

```bash
# Run tests
cd plugins/image-dedup
python3 -m pytest tests/ -v

# Run a specific test file
python3 -m pytest tests/test_fingerprint.py -v

# Test plugin manually (simulates Stash stdin)
echo '{"server_connection":{"Scheme":"http","Port":9999},"args":{"mode":"dry_run"}}' | python3 image_dedup.py
```
