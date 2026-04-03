# Tasks: Image Deduplication Plugin

**Input**: Design documents from `/specs/001-image-dedup/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

All source code lives under `plugins/image-dedup/` following the established plugin directory pattern.

---

## Phase 1: Setup

**Purpose**: Create plugin directory structure and static configuration files

- [x] T001 Create plugin directory structure: `plugins/image-dedup/`, `plugins/image-dedup/tests/`, `plugins/image-dedup/tests/fixtures/`, and `plugins/image-dedup/tests/__init__.py`
- [x] T002 [P] Create plugin manifest YAML from contract spec in `plugins/image-dedup/image-dedup.yml` — define name, description, version, url, exec (python3 + pluginDir), interface (raw), hooks (Image.Create.Post), and all 5 tasks (Find Duplicates, Dry Run - Find Duplicates, Resolve Duplicates, Dry Run - Resolve Duplicates, Cleanup) with their defaultArgs mode values
- [x] T003 [P] Create user-facing `plugins/image-dedup/README.md` — document plugin purpose, prerequisites (Python 3.10+, ImageMagick), installation steps, task descriptions, tag scheme (auto:dedup, dedup:group:NNNN), and similarity threshold configuration

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure in `plugins/image-dedup/image_dedup.py` that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 Implement configuration constants and logging helpers in `plugins/image-dedup/image_dedup.py` — define PROCESSED_TAG (`auto:dedup`), GROUP_TAG_PREFIX (`dedup:group:`), DEFAULT_THRESHOLD (10), and logging functions (log_trace, log_debug, log_info, log_warning, log_error, log_progress) using Stash's SOH stderr protocol (`\x01{level}\x02{msg}\n`)
- [x] T005 Implement GraphQL request function and Stash API helpers in `plugins/image-dedup/image_dedup.py` — graphql_request (urllib, session cookie auth), find_or_create_tag, find_unprocessed_images (paginated, tag EXCLUDES auto:dedup, with rating100/visual_files width+height+size/performers/tags/galleries), find_images_by_tag (paginated, tag INCLUDES), get_image (single by ID with all metadata fields), update_image (tag_ids, performer_ids, rating100), destroy_image (id, delete_file=true, delete_generated=true), add_images_to_gallery (gallery_id, image_ids), destroy_tag (id). All queries must include `visual_files { ... on ImageFile { path width height size } }`, `rating100`, `performers { id name }`, `tags { id name }`, `galleries { id title }` per the GraphQL operations contract
- [x] T006 Implement SQLite database module in `plugins/image-dedup/image_dedup.py` — init_db (create tables with schema from data-model.md: fingerprints, groups, group_members; use `pluginDir/dedup.db` path), upsert_fingerprint (INSERT OR REPLACE with image_id, file_hash, dhash_full, dhash_crop, width, height, file_size), get_all_fingerprints (return list of tuples), get_fingerprint (by image_id), create_group (auto-increment, generate tag_name as `dedup:group:NNNN` zero-padded), add_group_member (group_id, image_id), get_group_members (by group_id), get_image_group (return group_id for an image_id), get_unresolved_groups (where resolved=0), mark_group_resolved (set resolved=1), delete_group (cascade delete members), get_next_group_number
- [x] T007 Implement ImageMagick dependency check and perceptual fingerprint generation in `plugins/image-dedup/image_dedup.py` — check_magick (verify `magick` or `convert` binary exists via shutil.which, return command list), compute_dhash (accept image path + magick_cmd, run `magick <path> -colorspace Gray -resize 9x8! -depth 8 gray:-` via subprocess, read 72 raw bytes, compute 64-bit difference hash by comparing each pixel to its right neighbor), compute_dhash_crop (run `magick <path> -gravity Center -crop 60%x60%+0+0 +repage -colorspace Gray -resize 9x8! -depth 8 gray:-`, same hash computation), compute_file_hash (SHA-256 of file contents via hashlib), fingerprint_image (combine all three: file_hash + dhash_full + dhash_crop, handle subprocess errors gracefully returning None for corrupt/unreadable files with log_warning)
- [x] T008 Implement UnionFind class and similarity comparison functions in `plugins/image-dedup/image_dedup.py` — UnionFind with __init__ (parent dict, rank dict), find (with path compression), union (by rank), groups (return dict of root→set of members, only sets with 2+ members); hamming_distance (XOR + popcount via `bin(a ^ b).count('1')`), is_duplicate (accept two fingerprint tuples + threshold, return True if min of 4 cross-comparisons ≤ threshold: full↔full, crop↔crop, full↔crop, crop↔full)
- [x] T009 Implement main() entry point with stdin JSON parsing, mode routing, and error handling in `plugins/image-dedup/image_dedup.py` — read stdin, parse JSON, extract server_connection and args, check for hookContext (route to mode_hook), route args.mode to mode_scan/mode_dry_run/mode_resolve/mode_resolve_dry_run/mode_cleanup, wrap in try/except with log_error, output JSON result to stdout, add `if __name__ == "__main__": main()` guard. Mode functions will be stubs initially (return "not implemented") to be filled in by user story tasks

**Checkpoint**: Foundation ready — all core utilities (logging, GraphQL, SQLite, fingerprinting, comparison, Union-Find) are in place. User story implementation can now begin.

---

## Phase 3: User Story 1 — Batch Deduplication Scan (Priority: P1) 🎯 MVP

**Goal**: User triggers "Find Duplicates" task → plugin fingerprints all unprocessed images → identifies duplicate groups → tags them with `dedup:group:NNNN`

**Independent Test**: Import a set of known duplicate images (identical, cropped, resized, re-encoded) into Stash, run "Find Duplicates", verify all expected groups are tagged correctly. Re-run and verify previously fingerprinted images are skipped.

### Implementation for User Story 1

- [x] T010 [US1] Implement batch fingerprinting loop in `plugins/image-dedup/image_dedup.py` — function `fingerprint_all_unprocessed(connection, db, magick_cmd)`: paginate through find_unprocessed_images (100 per page), for each image extract file path from visual_files, call fingerprint_image, upsert into SQLite with width/height/file_size from visual_files, add auto:dedup tag to the image via update_image (preserving existing tags), call log_progress with fraction complete, skip and log_warning for images where fingerprinting returns None (corrupt/unreadable), return count of newly fingerprinted images
- [x] T011 [US1] Implement pairwise comparison and group discovery in `plugins/image-dedup/image_dedup.py` — function `discover_groups(db, threshold)`: load all fingerprints from SQLite into memory as list of (image_id, dhash_full, dhash_crop), initialize UnionFind, iterate all pairs with nested loop (i < j), call is_duplicate for each pair, union matching image_ids, extract groups from UnionFind (sets with 2+ members), return list of sets of image_ids
- [x] T012 [US1] Implement group tag creation and assignment in `plugins/image-dedup/image_dedup.py` — function `assign_group_tags(connection, db, groups)`: for each group of image_ids, check if any member already belongs to a group in SQLite (handle merging existing groups), if new group: get_next_group_number, create_group in SQLite, find_or_create_tag in Stash for the tag_name, for each member image: add_group_member in SQLite, add the group tag to the image via update_image (preserving existing tags including auto:dedup). Handle group merging: if members span multiple existing groups, consolidate into one group (remove old group tags, reassign members, delete old SQLite group records)
- [x] T013 [US1] Implement mode_scan function in `plugins/image-dedup/image_dedup.py` — replace stub: check_magick (abort with install instructions if missing), init_db, get processed_tag_id via find_or_create_tag, call fingerprint_all_unprocessed, call discover_groups, call assign_group_tags, log summary ("Scan complete: N images fingerprinted, M duplicate groups found"), return summary string

**Checkpoint**: User Story 1 is fully functional. Running "Find Duplicates" task will fingerprint all images, discover transitive duplicate groups, and tag them. Users can browse groups via Stash tag filtering.

---

## Phase 4: User Story 2 — Automatic Deduplication on New Images (Priority: P2)

**Goal**: When new images are added via Stash scan, they are automatically fingerprinted and checked against existing fingerprints. Duplicates are immediately tagged into the correct group.

**Independent Test**: After a batch scan, import a known duplicate image via Stash scan, verify it is automatically fingerprinted and tagged into the existing duplicate group.

### Implementation for User Story 2

- [x] T014 [US2] Implement single-image comparison against existing fingerprint database in `plugins/image-dedup/image_dedup.py` — function `find_matches(db, new_fingerprint, threshold)`: load all existing fingerprints from SQLite, compare new fingerprint against each using is_duplicate, return list of matching image_ids. Separate function `determine_group_action(db, matching_ids)`: if no matches return None; if all matches in same existing group return that group_id; if matches span multiple groups return list of group_ids to merge; if matches have no group return "new_group"
- [x] T015 [US2] Implement mode_hook function in `plugins/image-dedup/image_dedup.py` — replace stub: extract hook type and image ID from hookContext, verify it's Image.Create.Post, check_magick, init_db, get_image from Stash, extract file path/dimensions/size from visual_files, fingerprint_image (skip with log_warning if fails), upsert_fingerprint into SQLite, add auto:dedup tag, call find_matches, if matches found: call determine_group_action, handle three cases: (1) add to existing group — add_group_member + tag image with group tag, (2) create new group — create_group + add all members + tag all with new group tag, (3) merge groups — consolidate members into one group, retag all images, delete old groups. Log result ("Image {id}: new duplicate found in group {tag}" or "Image {id}: no duplicates found")

**Checkpoint**: User Story 2 is functional. New images added via scan are automatically fingerprinted and grouped. Combined with US1, the library stays clean automatically.

---

## Phase 5: User Story 3 — Resolving Duplicate Groups (Priority: P3)

**Goal**: User runs "Resolve Duplicates" → for each group, the highest resolution image is kept, metadata from all members is merged onto it, and duplicates are deleted.

**Independent Test**: Create a duplicate group with images of varying resolution and different performers/tags/ratings/gallery memberships, run "Resolve Duplicates", verify keeper has correct resolution, all metadata is merged, and duplicates are deleted.

### Implementation for User Story 3

- [x] T016 [US3] Implement keeper selection logic in `plugins/image-dedup/image_dedup.py` — function `select_keeper(images)`: accept list of image dicts (from GraphQL with visual_files, id), extract width×height for each, select image with highest pixel count, tiebreaker: largest file_size from visual_files, second tiebreaker: lowest image ID (int comparison for stability), return keeper image dict and list of non-keeper image dicts
- [x] T017 [US3] Implement metadata merge logic in `plugins/image-dedup/image_dedup.py` — function `merge_metadata(keeper, duplicates)`: collect all performer IDs from keeper + all duplicates (union, deduplicated), collect all tag IDs excluding tags matching GROUP_TAG_PREFIX or PROCESSED_TAG (union, deduplicated), then add back the PROCESSED_TAG id, compute max rating100 across all images (treating None/0 as no rating), collect all gallery IDs from keeper + all duplicates (union, deduplicated), return dict with merged performer_ids, tag_ids, rating100, gallery_ids
- [x] T018 [US3] Implement mode_resolve function in `plugins/image-dedup/image_dedup.py` — replace stub: init_db, get_unresolved_groups from SQLite, for each group: get_group_members to get image_ids, fetch full image data from Stash for each member (get_image), skip single-member groups (clean up group tag + delete SQLite group), call select_keeper, call merge_metadata, update_image on keeper with merged metadata, for each gallery_id not already containing keeper: add_images_to_gallery, for each non-keeper: destroy_image (delete_file=true), remove group tag from keeper (update_image with tag_ids excluding group tag), mark_group_resolved in SQLite, log each resolution ("Group {tag}: kept image {id} ({w}x{h}), deleted {n} duplicates, merged {p} performers/{t} tags/{g} galleries"), log summary ("Resolved {n} groups: kept {k} images, deleted {d} duplicates")
- [x] T019 [US3] Implement mode_resolve_dry_run function in `plugins/image-dedup/image_dedup.py` — replace stub: same logic as mode_resolve but replace all mutations with log_info statements describing what would happen ("Would keep image {id} ({w}x{h}, {size}B), delete images {ids}", "Would merge performers: {names}", "Would merge tags: {names}", "Would set rating to {r}", "Would add to galleries: {titles}"), return summary of planned actions without modifying any data

**Checkpoint**: User Story 3 is functional. Users can resolve duplicate groups automatically with full metadata preservation. Combined with US1+US2, the full detect→review→resolve workflow is complete.

---

## Phase 6: User Story 4 — Dry Run Mode (Priority: P3)

**Goal**: User runs "Dry Run - Find Duplicates" → plugin performs full analysis and reports potential duplicate groups in logs without creating tags or modifying any data.

**Independent Test**: Run dry run on a library with known duplicates, verify groups are logged but no tags are created and no database writes occur.

### Implementation for User Story 4

- [x] T020 [US4] Implement mode_dry_run function in `plugins/image-dedup/image_dedup.py` — replace stub: check_magick, create temporary in-memory SQLite database (`:memory:`) instead of persistent DB, fingerprint all unprocessed images into the temporary DB (WITHOUT adding auto:dedup tags to Stash), run discover_groups on the temporary DB, log each discovered group ("Potential duplicate group: images {ids} (similarity details)"), for each group log image details (path, resolution, file size) to help user evaluate, log summary ("Dry run complete: found {n} potential duplicate groups across {m} images"), return summary string. No Stash mutations, no persistent SQLite writes

**Checkpoint**: All four user stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Cleanup task, edge case hardening, and documentation finalization

- [x] T021 Implement mode_cleanup function in `plugins/image-dedup/image_dedup.py` — init_db, get all image_ids from fingerprints table, for each: call get_image from Stash, if image no longer exists (returns None): delete fingerprint from SQLite, log_info orphaned fingerprint removed; get all groups from SQLite, for each: get_group_members, count how many members still have valid fingerprints, if 0 members: delete group from SQLite + destroy_tag in Stash, if 1 member: remove group tag from the single image + delete group from SQLite + destroy_tag in Stash, log summary ("Cleanup: removed {f} orphaned fingerprints, {g} empty groups, {s} single-member groups")
- [x] T022 Add edge case handling across all modes in `plugins/image-dedup/image_dedup.py` — ensure fingerprint_image catches subprocess.CalledProcessError and OSError (corrupt/missing files) returning None with log_warning; ensure GraphQL errors during batch operations don't abort the entire scan (wrap individual image updates in try/except, log_warning, continue); ensure SQLite operations use WAL journal mode for concurrent read safety (PRAGMA journal_mode=WAL in init_db); ensure destroy_image handles already-deleted images gracefully; add timeout to ImageMagick subprocess calls (30 second timeout per image)
- [x] T023 [P] Finalize `plugins/image-dedup/README.md` with complete documentation — add detailed usage examples for each task, document the tag scheme with examples, explain similarity threshold tuning (lower = stricter/fewer matches, higher = more permissive/more matches, default 10), document ImageMagick installation for all platforms (Alpine/Debian/macOS), add troubleshooting section (common issues: ImageMagick not found, SQLite permission errors, large library performance tips)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (directory must exist) — **BLOCKS all user stories**
- **User Story 1 (Phase 3)**: Depends on Phase 2 completion
- **User Story 2 (Phase 4)**: Depends on Phase 2 completion (shares foundational fingerprint/comparison code with US1, but does NOT require US1 to be complete — hook mode works independently)
- **User Story 3 (Phase 5)**: Depends on Phase 2 completion (resolve operates on groups created by US1 or US2, but the code itself only depends on foundational helpers)
- **User Story 4 (Phase 6)**: Depends on Phase 2 completion (uses fingerprint + comparison code)
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies on other stories — fully independent after Phase 2
- **User Story 2 (P2)**: No dependencies on other stories — works independently (hook creates groups even without prior batch scan)
- **User Story 3 (P3)**: Functionally operates on groups created by US1/US2 — but the code depends only on foundational Phase 2 code. Can be implemented in parallel with US1/US2.
- **User Story 4 (P3)**: No dependencies on other stories — uses in-memory DB, fully self-contained

### Within Each User Story

- Tasks are ordered by dependency within each phase
- Models/data operations before orchestration functions
- Core logic before mode wrappers

### Parallel Opportunities

- **Phase 1**: T002 and T003 can run in parallel (different files)
- **Phase 3–6**: All four user story phases can theoretically start in parallel after Phase 2 (they build different mode functions in the same file, but each touches distinct functions)
- **Phase 7**: T023 (README) can run in parallel with T021/T022 (different file)

---

## Parallel Example: Phase 1

```
# These can run simultaneously (different files):
Task T002: Create plugin manifest YAML in plugins/image-dedup/image-dedup.yml
Task T003: Create README.md in plugins/image-dedup/README.md
```

## Parallel Example: User Stories (after Phase 2)

```
# After foundational phase, these story implementations can proceed in parallel:
Phase 3 (US1): T010 → T011 → T012 → T013  (batch scan pipeline)
Phase 4 (US2): T014 → T015                  (hook processing)
Phase 5 (US3): T016 → T017 → T018 → T019   (resolution pipeline)
Phase 6 (US4): T020                          (dry run)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T003)
2. Complete Phase 2: Foundational (T004–T009)
3. Complete Phase 3: User Story 1 (T010–T013)
4. **STOP and VALIDATE**: Run "Find Duplicates" on a test library with known duplicates. Verify groups are correctly identified and tagged. Verify re-runs skip already-processed images.
5. This MVP alone delivers the core value: finding duplicates in an existing library.

### Incremental Delivery

1. **MVP** → Phase 1 + 2 + 3 (Batch Scan) — find duplicates in existing library
2. **+US2** → Phase 4 (Auto Hook) — keep library clean going forward
3. **+US4** → Phase 6 (Dry Run) — preview before committing (quick win, one task)
4. **+US3** → Phase 5 (Resolve) — automated cleanup with metadata merge
5. **Polish** → Phase 7 (Cleanup mode, edge cases, docs)

### Suggested Order

US4 (Dry Run) is recommended before US3 (Resolve) because:
- It's a single task (T020) — quick to implement
- It gives users confidence before running the destructive resolve operation
- The resolve dry run (T019 in US3) can reference its pattern

---

## Notes

- All implementation tasks target the same file (`plugins/image-dedup/image_dedup.py`) following the single-file plugin pattern established by `username-extractor` and `heic-converter`
- [P] tasks operate on different files and have no dependencies on incomplete tasks
- [US*] labels map tasks to their user story for traceability
- Commit after each task or logical group
- The SQLite database (`dedup.db`) is created at runtime in the plugin directory — not committed to version control
- ImageMagick is a runtime dependency, not a build dependency — the plugin checks for it at startup and logs install instructions if missing
