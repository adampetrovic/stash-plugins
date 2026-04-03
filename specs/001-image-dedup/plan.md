# Implementation Plan: Image Deduplication Plugin

**Branch**: `001-image-dedup` | **Date**: 2026-04-03 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-image-dedup/spec.md`

## Summary

Build a Stash plugin that detects duplicate and near-duplicate images using perceptual hashing (dHash via ImageMagick), groups them transitively using Union-Find, and provides a resolution workflow that keeps the highest-quality version while merging metadata from all duplicates. The plugin hooks into Image.Create.Post for automatic detection and provides batch scan, dry run, resolve, and cleanup tasks.

## Technical Context

**Language/Version**: Python 3.10+ (stdlib only — no pip packages)
**Primary Dependencies**: ImageMagick (external CLI tool for image processing)
**Storage**: SQLite via Python stdlib `sqlite3` (fingerprint database at `{pluginDir}/dedup.db`)
**Testing**: pytest with mocks (matching existing plugin test pattern)
**Target Platform**: Stash Docker container (Alpine Linux) or any Stash installation with Python 3 + ImageMagick
**Project Type**: Stash plugin (raw interface, stdin/stdout JSON protocol)
**Performance Goals**: Batch scan 10K images in <30 min; single-image hook <10 sec against 10K fingerprints
**Constraints**: No pip packages; ImageMagick subprocess for all image processing; Stash GraphQL API for all library interactions
**Scale/Scope**: Target 10K–50K image libraries; SQLite handles this comfortably

## Constitution Check

*GATE: The project constitution is unpopulated (template placeholders only). No gates to evaluate. Proceeding.*

## Project Structure

### Documentation (this feature)

```text
specs/001-image-dedup/
├── plan.md              # This file
├── research.md          # Phase 0: technology decisions and rationale
├── data-model.md        # Phase 1: SQLite schema, entity relationships, state transitions
├── quickstart.md        # Phase 1: setup, usage, and development guide
├── contracts/
│   ├── plugin-manifest.yml    # Plugin YAML contract (hooks, tasks)
│   ├── stdin-protocol.md      # Stdin/stdout JSON protocol
│   └── graphql-operations.md  # GraphQL queries and mutations used
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
plugins/image-dedup/
├── image-dedup.yml          # Plugin manifest (copied from contracts/plugin-manifest.yml)
├── image_dedup.py           # Main plugin script — all modes, GraphQL helpers, fingerprinting
├── README.md                # User-facing documentation
└── tests/
    ├── __init__.py
    ├── test_fingerprint.py  # dHash generation via ImageMagick, file hashing
    ├── test_grouping.py     # Union-Find, transitive grouping, group merging
    ├── test_comparison.py   # Hamming distance, threshold, dual-hash cross-comparison
    ├── test_resolution.py   # Keeper selection, metadata merge, gallery merge
    ├── test_modes.py        # Plugin modes (scan, dry_run, resolve, hook, cleanup)
    └── fixtures/            # Test images: original, crop, resize, reencoded variants
```

**Structure Decision**: Single-directory plugin following the established pattern of `plugins/username-extractor/` and `plugins/heic-converter/`. All logic in one Python file with tests in a `tests/` subdirectory. SQLite database created at runtime in the plugin directory.

## Key Architecture Decisions

### 1. Perceptual Hashing: dHash via ImageMagick
- ImageMagick resizes images to 9×8 grayscale and outputs raw pixels
- Python computes 64-bit difference hash from the raw byte stream
- Dual-hash approach: full image + center 60% crop (catches border-cropped variants)
- See [research.md](research.md) R1 and R2 for full rationale

### 2. Storage: SQLite (stdlib)
- `fingerprints` table: image_id, file_hash, dhash_full, dhash_crop, dimensions
- `groups` table: group_id, tag_name, resolved status
- `group_members` table: group_id ↔ image_id junction
- See [data-model.md](data-model.md) for complete schema

### 3. Grouping: Union-Find
- Transitive grouping via Disjoint Set Union data structure
- Automatically handles group merging when new matches bridge existing groups
- Connected components extracted after all comparisons complete
- See [research.md](research.md) R5

### 4. Similarity Comparison: Brute-Force Pairwise
- Hamming distance on 64-bit integers: `bin(a ^ b).count('1')`
- 50M comparisons at 10K scale completes in seconds
- Dual-hash: 4 cross-comparisons per pair (full↔full, crop↔crop, full↔crop, crop↔full)
- Match threshold: Hamming distance ≤ 10 (configurable)
- See [research.md](research.md) R4

### 5. Plugin Modes
| Mode | Trigger | Modifies Data | Description |
|------|---------|--------------|-------------|
| hook | Image.Create.Post | Yes | Fingerprint new image, check for matches, tag if duplicate found |
| scan | Task: Find Duplicates | Yes | Batch fingerprint + group all un-processed images |
| dry_run | Task: Dry Run | No | Report what scan would find |
| resolve | Task: Resolve Duplicates | Yes (destructive) | Keep best, merge metadata, delete rest |
| resolve_dry_run | Task: Dry Run Resolve | No | Preview resolve actions |
| cleanup | Task: Cleanup | Yes | Remove orphaned fingerprints, empty groups |

### 6. Tag Scheme
- `auto:dedup` — processing state marker (image has been fingerprinted)
- `dedup:group:NNNN` — duplicate group membership (zero-padded 4+ digits)
- Tags created via Stash GraphQL `tagCreate` mutation on first use

### 7. Resolution Workflow (Destructive)
For each unresolved duplicate group:
1. Query all member images with full metadata
2. Select keeper: highest resolution (width × height), then largest file size, then lowest image ID
3. Collect metadata union: performers (all unique IDs), tags (all unique, excluding dedup tags), rating (max), galleries (all unique)
4. Update keeper image with merged metadata
5. Add keeper to all galleries from deleted duplicates
6. Delete non-keeper images via `imageDestroy(delete_file: true)`
7. Remove group tag from keeper, mark group as resolved

## Complexity Tracking

No constitution violations to justify — constitution is unpopulated.
