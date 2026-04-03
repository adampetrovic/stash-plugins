# Image Deduplication

Finds duplicate and near-duplicate images in your Stash library using perceptual hashing. Detects exact copies, cropped versions, resized variants, and re-encoded images regardless of format or quality differences.

## Prerequisites

- **Python 3.10+** (included with Stash Docker image)
- **ImageMagick** — `magick` or `convert` must be on PATH
  - Alpine (Docker): `apk add imagemagick`
  - macOS: `brew install imagemagick`
  - Debian/Ubuntu: `apt-get install imagemagick`

## Installation

1. Copy the `image-dedup/` directory into your Stash plugins folder
2. Reload plugins: **Settings → Plugins → Reload Plugins**
3. Verify "Image Deduplication" appears in the plugin list

## Tasks

| Task | Mode | Description |
|------|------|-------------|
| **Find Duplicates** | `scan` | Fingerprint all unprocessed images and identify duplicate groups |
| **Dry Run - Find Duplicates** | `dry_run` | Preview what scan would find without making changes |
| **Resolve Duplicates** | `resolve` | Keep best image per group, merge metadata, delete rest |
| **Dry Run - Resolve Duplicates** | `resolve_dry_run` | Preview what resolve would do |
| **Cleanup** | `cleanup` | Remove orphaned fingerprints and empty groups |

## How It Works

1. **Fingerprinting**: Each image is processed by ImageMagick to generate a 64-bit perceptual hash (dHash). Two hashes are computed — one for the full image and one for the center 60% crop — to detect border-trimmed variants.

2. **Comparison**: All fingerprints are compared pairwise using Hamming distance. Images within the similarity threshold are considered duplicates.

3. **Grouping**: Duplicates are grouped transitively using Union-Find. If A matches B and B matches C, all three form one group — even if A and C don't match directly.

4. **Tagging**: Each duplicate group is tagged with `dedup:group:NNNN` in Stash. Browse groups by filtering on these tags.

5. **Resolution**: The resolve task keeps the highest resolution image (file size tiebreaker), merges performers, tags, ratings, and gallery memberships from all copies onto the keeper, then deletes the duplicates.

## Tags

| Tag | Meaning |
|-----|---------|
| `auto:dedup` | Image has been fingerprinted (processing marker) |
| `dedup:group:0001` | Image belongs to duplicate group 1 |
| `dedup:group:0002` | Image belongs to duplicate group 2 |
| ... | Sequential group numbers |

## Similarity Threshold

The default threshold is **10** (Hamming distance). This favours precision — fewer false positives at the cost of potentially missing some borderline matches.

| Threshold | Behaviour |
|-----------|-----------|
| 1–5 | Very strict — only near-identical images |
| 6–10 | **Default** — catches quality/format/resize variants |
| 11–16 | Permissive — catches moderate crops and edits |
| 17+ | Too loose — high false positive rate |

To adjust, modify `DEFAULT_THRESHOLD` in `image_dedup.py`.

## Recommended Workflow

1. Run **Dry Run - Find Duplicates** to preview results
2. Run **Find Duplicates** to create duplicate groups
3. Browse groups via Stash tag filter (`dedup:group:*`)
4. Remove tags from any images you disagree with as duplicates
5. Run **Dry Run - Resolve Duplicates** to preview resolution plan
6. Run **Resolve Duplicates** to keep best, merge metadata, delete rest
7. Run **Cleanup** periodically to remove stale data

## Data Storage

Fingerprints and group data are stored in `dedup.db` (SQLite) in the plugin directory. This file is created automatically and not committed to version control.

## License

[AGPL-3.0](../../LICENCE)
