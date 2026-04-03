# Data Model: Image Deduplication Plugin

**Branch**: `001-image-dedup` | **Date**: 2026-04-03

## Entities

### Fingerprint

Stores the perceptual hash data for a single Stash image.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| image_id | integer | PK | Stash image ID (from GraphQL) |
| file_hash | text | NOT NULL | SHA-256 of the image file (exact duplicate detection) |
| dhash_full | integer | NOT NULL | 64-bit dHash of the full image |
| dhash_crop | integer | NOT NULL | 64-bit dHash of the center 60% crop |
| width | integer | nullable | Image width in pixels (cached from Stash) |
| height | integer | nullable | Image height in pixels (cached from Stash) |
| file_size | integer | nullable | File size in bytes (cached from Stash) |
| created_at | text | NOT NULL, default now | ISO 8601 timestamp of fingerprint creation |

**Identity rule**: One fingerprint per Stash image ID. If an image is re-scanned, its fingerprint is updated (upsert).

**Lifecycle**: Created when an image is first fingerprinted (batch scan or hook). Deleted when orphan cleanup detects the image no longer exists in Stash.

### DuplicateGroup

Tracks a group of images identified as duplicates, linked to a Stash tag.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| group_id | integer | PK, auto-increment | Internal group identifier |
| tag_name | text | UNIQUE, NOT NULL | Stash tag name (e.g., `dedup:group:0001`) |
| resolved | integer | NOT NULL, default 0 | 1 if the group has been resolved (keeper selected, duplicates deleted) |
| created_at | text | NOT NULL, default now | ISO 8601 timestamp of group creation |

**Identity rule**: One group per tag name. Tag names are sequential: `dedup:group:NNNN`.

**Lifecycle**: Created when a batch scan or hook discovers a new set of duplicates. Marked `resolved=1` when the resolve task processes it. Deleted (with tag cleanup) when no member images remain.

### GroupMember

Junction table linking images to their duplicate group.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| group_id | integer | FK → DuplicateGroup, composite PK | The duplicate group |
| image_id | integer | FK → Fingerprint, composite PK | The member image |

**Lifecycle**: Created when an image is assigned to a group. Removed when the image is deleted (during resolution or external deletion).

## Relationships

```
Fingerprint 1──* GroupMember *──1 DuplicateGroup
```

- A **Fingerprint** can belong to at most one **DuplicateGroup** (via GroupMember). Transitive grouping ensures no image appears in multiple groups.
- A **DuplicateGroup** has two or more **GroupMembers** (groups with <2 members are cleaned up).

## State Transitions

### Fingerprint Lifecycle

```
[Not Fingerprinted] ──(batch scan / hook)──→ [Fingerprinted]
[Fingerprinted] ──(image deleted in Stash)──→ [Orphaned]
[Orphaned] ──(next batch scan cleanup)──→ [Deleted]
```

### DuplicateGroup Lifecycle

```
[Created] ──(duplicates detected, tag applied)──→ [Active]
[Active] ──(user runs resolve task)──→ [Resolved]
[Active] ──(new match bridges another group)──→ [Merged] → old group [Deleted]
[Active] ──(all members deleted externally)──→ [Empty] → [Deleted]
[Resolved] ──(tag cleaned up)──→ [Deleted]
```

### Image Processing States (via Stash Tags)

| State | Tag Present | Meaning |
|-------|------------|---------|
| Unprocessed | No `auto:dedup` tag | Image has not been fingerprinted |
| Fingerprinted | `auto:dedup` tag present | Image has been fingerprinted and compared |
| In Duplicate Group | `auto:dedup` + `dedup:group:NNNN` | Image is part of an identified duplicate group |
| Resolved (keeper) | `auto:dedup` only (group tag removed) | Image was kept during resolution |

## Stash Entities Referenced (read/write via GraphQL)

These entities are owned by Stash — the plugin reads and writes them via GraphQL API.

| Entity | Plugin Reads | Plugin Writes |
|--------|-------------|---------------|
| Image | id, path, width, height, size, tags, performers, rating100, galleries, studio | tags, performers, rating100, galleries (during merge) |
| Tag | id, name | Create `auto:dedup` and `dedup:group:NNNN` tags |
| Gallery | id | Add keeper image to galleries (during merge) |
| Performer | id | Link merged performers to keeper |

## Validation Rules

- **Fingerprint uniqueness**: One fingerprint per image_id. Upsert on conflict.
- **Group tag format**: Must match pattern `dedup:group:NNNN` (4+ digit zero-padded).
- **Group membership**: An image can be in at most one group. If a union operation connects two groups, members of the smaller group are re-assigned to the larger group.
- **Similarity threshold**: Integer 0–64 (Hamming distance of 64-bit hashes). Default: 10. Validated on input.
- **Keeper selection**: Deterministic — highest resolution (width × height), then largest file size, then lowest image ID (stable tiebreaker).

## Data Volume Estimates

| Entity | Expected Count | Growth Rate |
|--------|---------------|-------------|
| Fingerprint | 1 per image in library (target: 10K–50K) | Grows with library |
| DuplicateGroup | ~5–15% of image count | Grows with library, shrinks with resolution |
| GroupMember | 2–5 per group average | Same as DuplicateGroup |

SQLite database size estimate: ~1MB per 10K fingerprints (well within filesystem constraints).
