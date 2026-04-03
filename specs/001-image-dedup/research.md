# Research: Image Deduplication Plugin

**Branch**: `001-image-dedup` | **Date**: 2026-04-03

## R1: Perceptual Hashing Without pip Packages

### Decision
Use ImageMagick (subprocess) for image normalization combined with pure-Python hash computation. This produces **dHash** (difference hash) fingerprints — a well-established algorithm for perceptual image matching.

### Rationale
- Existing plugins in this repo use **stdlib only + external CLI tools** (tesseract for OCR, ImageMagick for image conversion). Following the same pattern keeps the dependency model consistent.
- ImageMagick can resize, convert to grayscale, and output raw pixel data. Python computes the hash from the raw bytes — no pip packages required.
- dHash is particularly effective for quality, format, and resize variants because it encodes relative brightness changes between adjacent pixels, which are preserved through recompression and resizing.

### Alternatives Considered
| Alternative | Pros | Cons | Rejected Because |
|-------------|------|------|------------------|
| `imagehash` + Pillow (pip) | Best accuracy, multiple hash types | Requires pip install, breaks ecosystem convention | Inconsistent with existing plugin dependency model |
| OpenCV (pip) | Feature matching for crops (SIFT/ORB) | Heavy dependency (~50MB), requires pip | Overkill; ImageMagick handles image processing |
| ffmpeg for image processing | Already bundled with Stash | Image manipulation API is awkward, limited pixel output formats | ImageMagick is more natural for still-image operations |
| Pure Python pixel parsing (no external tool) | Zero dependencies | Cannot read JPEG/PNG/WebP without a library | Not viable for real image formats |

### Implementation Notes
- **dHash algorithm**: Resize to 9×8 grayscale → compare each pixel to its right neighbor → produces 64-bit hash. Hamming distance between two dHashes indicates visual similarity.
- **Hamming distance thresholds**: 0 = identical, ≤10 = very similar (quality/format variants), ≤16 = somewhat similar (potential crop/edit). Default threshold: 10 (precision-focused).
- **ImageMagick command**: `magick <input> -colorspace Gray -resize 9x8! -depth 8 gray:-` outputs raw grayscale bytes.

---

## R2: Crop Detection Strategy

### Decision
Use a **dual-hash approach**: compute dHash for both the full image and a center-crop region (inner 60%). When comparing images, a match on either hash counts as a duplicate. This catches the most common crop pattern (border trimming) without requiring complex feature matching.

### Rationale
- No single perceptual hash reliably detects all crop types. dHash is excellent for quality/resize variants but degrades with heavy cropping because the pixel grid shifts.
- Center-crop hashing is simple to implement (ImageMagick `-gravity Center -crop 60%x60%+0+0`) and catches the most common crop pattern: social media border/watermark trimming.
- This approach stays within the ImageMagick-only dependency constraint.

### Alternatives Considered
| Alternative | Pros | Cons | Rejected Because |
|-------------|------|------|------------------|
| Block hashing (divide into grid) | Catches partial overlaps | Complex matching logic, O(n²) per block | Adds significant complexity for marginal gain over dual-hash |
| Feature point matching (ORB/SIFT) | Best crop detection | Requires OpenCV pip package | Breaks ecosystem convention |
| Color histogram comparison | Format-agnostic | High false positive rate for similar-but-different images | Poor precision; too many false matches |
| Multi-scale hashing (8 crops) | Comprehensive coverage | 8× storage and comparison cost | Diminishing returns vs dual-hash at 2× cost |

### Implementation Notes
- Full image dHash: standard 9×8 grayscale
- Center-crop dHash: ImageMagick crops inner 60% first, then same 9×8 hash
- Match criteria: `min(hamming(full_A, full_B), hamming(crop_A, crop_B), hamming(full_A, crop_B), hamming(crop_A, full_B)) ≤ threshold`
- This cross-comparison catches cases where one image is a crop of the other

---

## R3: Fingerprint Storage

### Decision
Use **SQLite** via Python's stdlib `sqlite3` module. Store the database file at `{pluginDir}/dedup.db`.

### Rationale
- `sqlite3` is part of Python's standard library — no pip packages needed.
- Supports indexed queries for efficient lookups by image ID.
- Handles concurrent reads safely (Stash hooks may fire in parallel).
- File-based, so it persists in the plugin directory alongside the plugin code.
- Well-suited for the data scale (10K–100K fingerprint records is trivial for SQLite).

### Alternatives Considered
| Alternative | Pros | Cons | Rejected Because |
|-------------|------|------|------------------|
| JSON file | Human-readable, simple | Slow for large datasets, no indexing, concurrent write risk | Performance degrades at scale; no atomic writes |
| Stash tags as storage | No external storage needed | Extremely limited; can't store binary hashes in tag names | Not viable for storing fingerprint data |
| CSV file | Simple, appendable | No indexing, slow lookups, concurrent write issues | Same problems as JSON with less structure |

### Schema
```sql
CREATE TABLE fingerprints (
    image_id    INTEGER PRIMARY KEY,
    file_hash   TEXT NOT NULL,
    dhash_full  INTEGER NOT NULL,
    dhash_crop  INTEGER NOT NULL,
    width       INTEGER,
    height      INTEGER,
    file_size   INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE groups (
    group_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_name    TEXT NOT NULL UNIQUE,
    resolved    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE group_members (
    group_id    INTEGER NOT NULL REFERENCES groups(group_id),
    image_id    INTEGER NOT NULL REFERENCES fingerprints(image_id),
    PRIMARY KEY (group_id, image_id)
);
```

---

## R4: Similarity Comparison at Scale

### Decision
Use **brute-force pairwise comparison** with bit-manipulation for Hamming distance. At 10K images this is ~50M comparisons of 64-bit integers — completes in seconds in Python.

### Rationale
- Hamming distance of two 64-bit integers is: `bin(a ^ b).count('1')` — a single-line operation.
- At 10K images: 10,000 × 9,999 / 2 = ~50M comparisons. In Python, each XOR + popcount takes ~100ns → total ~5 seconds.
- For the dual-hash approach (4 cross-comparisons per pair), ~20 seconds total — well within the 30-minute target.
- No need for approximate nearest-neighbor data structures (BK-tree, VP-tree) at this scale.

### Alternatives Considered
| Alternative | Pros | Cons | Rejected Because |
|-------------|------|------|------------------|
| BK-tree | O(n log n) lookup | Complex to implement in pure Python | Unnecessary complexity at 10K scale |
| VP-tree | Fast nearest-neighbor | Requires scipy or custom implementation | pip dependency; not needed at current scale |
| Locality-sensitive hashing (LSH) | Sub-linear query time | Complex, probabilistic, may miss matches | Overkill; brute force is fast enough |
| Pre-sorted hash comparison | Fast for exact/near matches | Misses matches with large Hamming distance | Only works for very similar hashes |

### Implementation Notes
- Batch comparison: load all fingerprints from SQLite into memory, compare pairwise
- Hook comparison: load all fingerprints, compare single new fingerprint against all
- For hook processing (SC-004: <10 seconds), comparing 1 hash against 10K fingerprints = 40K comparisons, ~milliseconds

---

## R5: Transitive Grouping Algorithm

### Decision
Use **Union-Find (Disjoint Set Union)** data structure for transitive group management.

### Rationale
- Union-Find is the standard algorithm for connected component discovery, which is exactly what transitive grouping requires.
- O(α(n)) amortized per operation (effectively constant time) with path compression and union by rank.
- Trivial to implement in pure Python (~20 lines).
- Naturally handles group merging (FR-017): when a new match bridges two groups, `union(a, b)` merges them automatically.

### Implementation Notes
```python
class UnionFind:
    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])  # path compression
        return self.parent[x]

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1

    def groups(self):
        from collections import defaultdict
        result = defaultdict(set)
        for x in self.parent:
            result[self.find(x)].add(x)
        return {k: v for k, v in result.items() if len(v) > 1}
```

---

## R6: Stash GraphQL Image Fields

### Decision
Query the following fields for image operations, based on Stash's GraphQL schema.

### Image Query (extended from existing plugin pattern)
```graphql
query FindImage($id: ID!) {
    findImage(id: $id) {
        id
        title
        details
        rating100
        visual_files {
            ... on ImageFile {
                path
                width
                height
                size
            }
        }
        studio { id name }
        tags { id name }
        performers { id name }
        galleries { id title }
    }
}
```

### Key Fields for Resolution
- **Keeper selection**: `visual_files.width`, `visual_files.height` (resolution), `visual_files.size` (file size tiebreaker)
- **Metadata merge**: `performers`, `tags`, `rating100`, `galleries`
- **Image deletion**: `imageDestroy` mutation with `id` and `delete_file: true`

### Image Deletion Mutation
```graphql
mutation ImageDestroy($id: ID!, $delete_file: Boolean) {
    imageDestroy(input: { id: $id, delete_file: $delete_file })
}
```

### Image Gallery Update
```graphql
mutation AddImagesToGallery($gallery_id: ID!, $image_ids: [ID!]!) {
    addImagesToGallery(input: { gallery_id: $gallery_id, image_ids: $image_ids })
}
```

---

## R7: Stash Plugin Lifecycle & Hook Behavior

### Decision
Follow the established plugin pattern: single Python entry point, raw interface, stdin JSON input.

### Findings
- **Hook execution**: Stash calls the plugin binary with hook context in `args.hookContext`. Each hook fires independently per image created. For bulk scans, hooks fire sequentially (not in parallel).
- **Task execution**: Plugin tasks are triggered via the UI or GraphQL API. The plugin receives task `mode` in `args.mode`.
- **Progress reporting**: Use `log_progress(float)` with the SOH protocol to report progress to the Stash UI.
- **Plugin directory**: Available as `{pluginDir}` in the YAML config. The Python script can determine its own directory via `os.path.dirname(os.path.abspath(__file__))`.

### Plugin YAML Structure (for this plugin)
```yaml
name: Image Deduplication
description: ...
version: 1.0.0
url: https://github.com/adampetrovic/stash-plugins
exec:
  - python3
  - "{pluginDir}/image_dedup.py"
interface: raw
hooks:
  - name: "Fingerprint on Image Create"
    triggeredBy:
      - Image.Create.Post
tasks:
  - name: "Find Duplicates"
    defaultArgs:
      mode: scan
  - name: "Dry Run"
    defaultArgs:
      mode: dry_run
  - name: "Resolve Duplicates"
    defaultArgs:
      mode: resolve
```
