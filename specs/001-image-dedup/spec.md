# Feature Specification: Image Deduplication Plugin

**Feature Branch**: `001-image-dedup`  
**Created**: 2026-04-03  
**Status**: Draft  
**Input**: User description: "i want to build a stash plugin that allows me to run image deduplication against my image library. It needs to be able to hook into new images added as the result of a scan as well as a batch run (once off) in the plugin settings. The goal of image deduplication is not only to match identical hash-based matches, but also match identical images that are cropped, different quality etc that would cause their hashes not to match."

## Clarifications

### Session 2026-04-03

- Q: Should duplicates form transitive groups (A≈B, B≈C → {A,B,C} in one group)? → A: Yes, transitive grouping — all connected images form a single group.
- Q: What is the resolution workflow for duplicate groups? → A: Keep the highest resolution image, delete the rest, and merge metadata (performers, tags, ratings) from all group members onto the keeper.
- Q: Should gallery membership be merged onto the keeper during resolution? → A: Yes, merge galleries — the keeper is added to all galleries that any deleted duplicate belonged to.

## User Scenarios & Testing

### User Story 1 - Batch Deduplication Scan (Priority: P1)

A user with a large existing image library wants to find all duplicate and near-duplicate images across their collection. They navigate to the plugin tasks in Stash settings and trigger a full deduplication scan. The plugin analyses every image in the library, generates perceptual fingerprints, compares them against each other, and groups visually similar images together. Once complete, the user can browse duplicate groups to review which images are duplicates and decide what to keep.

**Why this priority**: This is the core value proposition — finding duplicates across an entire existing library. Most users will start here to clean up an already-populated collection before relying on automatic detection going forward.

**Independent Test**: Can be fully tested by importing a set of known duplicate images (identical copies, cropped versions, resized versions, re-encoded versions) into a Stash library, running the batch scan task, and verifying that all expected duplicate groups are correctly identified and tagged.

**Acceptance Scenarios**:

1. **Given** a library with 50 images including 3 groups of known duplicates (exact copies, cropped versions, and quality variants), **When** the user runs the batch deduplication scan task, **Then** all 3 duplicate groups are identified and each image in a group is tagged with a shared group identifier.
2. **Given** a library where a batch scan has already been run and all images are fingerprinted, **When** the user runs the batch scan again, **Then** only un-fingerprinted images are processed (previously fingerprinted images are skipped), completing significantly faster.
3. **Given** a library with no duplicate images, **When** the user runs the batch scan, **Then** no duplicate groups are created and the user is informed that no duplicates were found.
4. **Given** a batch scan is in progress on a large library, **When** the user checks the Stash task/log output, **Then** they can see progress information indicating how many images have been processed out of the total.

---

### User Story 2 - Automatic Deduplication on New Images (Priority: P2)

When new images are added to the library via a Stash scan, the plugin automatically fingerprints each new image and checks it against the existing fingerprint database. If a match is found, the new image is tagged into the appropriate duplicate group so the user is immediately aware of the duplication without needing to run a manual scan.

**Why this priority**: This keeps the library clean on an ongoing basis after the initial batch scan. Without this, users would need to repeatedly run batch scans every time they import new content.

**Independent Test**: Can be tested by first running a batch scan on a base library, then triggering a Stash scan that imports a duplicate of an existing image, and verifying the new image is automatically detected and grouped.

**Acceptance Scenarios**:

1. **Given** a library with fingerprinted images from a previous batch scan, **When** a Stash scan adds a new image that is visually identical to an existing image, **Then** the new image is automatically fingerprinted, matched to the existing image, and both are tagged in the same duplicate group.
2. **Given** a library with fingerprinted images, **When** a Stash scan adds a new image that has no visual match in the library, **Then** the image is fingerprinted and stored for future comparisons but no duplicate group is created.
3. **Given** a Stash scan adds multiple new images at once, **When** two of the new images are duplicates of each other but not of any existing image, **Then** both new images are fingerprinted and grouped together as duplicates.

---

### User Story 3 - Resolving Duplicate Groups (Priority: P3)

After duplicate groups have been identified, the user wants to resolve them — keeping the best version and cleaning up the rest. The user runs a "Resolve Duplicates" task that automatically processes each duplicate group: it selects the highest resolution image as the keeper, merges metadata (performers, tags, ratings) from all other group members onto the keeper, and deletes the lower-quality duplicates. Before running the resolve task, the user can preview duplicate groups by filtering on group tags and optionally adjust groups manually (remove images from a group by removing the tag) if they disagree with a grouping.

**Why this priority**: Finding duplicates is only useful if the user can act on them. This story provides the resolution workflow that turns detection into actual library cleanup. The automated resolve task handles the common case (keep best quality, merge metadata) while still allowing manual review beforehand.

**Independent Test**: Can be tested by creating a duplicate group with images of varying resolution and different metadata (different performers, tags, ratings on different group members), running the resolve task, and verifying the highest-resolution image survives with all metadata merged onto it and all other images are deleted.

**Acceptance Scenarios**:

1. **Given** a duplicate group containing a 1080p image and a 720p copy of the same image, **When** the user runs the resolve task, **Then** the 1080p image is kept and the 720p image is deleted.
2. **Given** a duplicate group where Image A has performers P1 and P2, and Image B has performer P3, **When** the resolve task processes this group, **Then** the keeper image has performers P1, P2, and P3 (union of all performers).
3. **Given** a duplicate group where Image A has tags T1 and T2, and Image B has tags T2 and T3, **When** the resolve task processes this group, **Then** the keeper image has tags T1, T2, and T3 (union of all tags, excluding dedup-related tags).
4. **Given** a duplicate group where Image A is rated 3 and Image B is rated 5, **When** the resolve task processes this group, **Then** the keeper image has a rating of 5 (highest rating wins).
5. **Given** a duplicate group where all images have the same resolution, **When** the resolve task processes this group, **Then** the image with the largest file size is kept as the keeper (as a tiebreaker for quality).
6. **Given** a duplicate group where Image A belongs to Gallery X and Image B belongs to Gallery Y, **When** the resolve task keeps Image A as the highest resolution, **Then** Image A is added to Gallery Y (preserving all gallery associations from deleted duplicates).
7. **Given** the user disagrees with a grouping, **When** they remove the duplicate group tag from an image before running the resolve task, **Then** that image is excluded from resolution and retained as-is.

---

### User Story 4 - Dry Run Mode (Priority: P3)

A user wants to preview what the deduplication scan would find without making any changes to their library. They run a dry run task that performs the full analysis and reports duplicate groups in the log output but does not create or modify any tags.

**Why this priority**: Gives users confidence in what the plugin will do before committing to changes. Follows the pattern established by existing plugins in this repository.

**Independent Test**: Can be tested by running the dry run task on a library with known duplicates and verifying that duplicate groups are reported in logs but no tags are created or modified on any images.

**Acceptance Scenarios**:

1. **Given** a library with duplicate images, **When** the user runs the dry run task, **Then** potential duplicate groups are reported in the Stash log output without any tags being created or modified.
2. **Given** a library with no duplicates, **When** the user runs the dry run task, **Then** the log reports that no duplicates were found.

---

### Edge Cases

- What happens when an image file is corrupt or unreadable? The plugin should skip it, log a warning, and continue processing remaining images.
- What happens when the library contains thousands of images? The fingerprint comparison must remain performant and not cause Stash to become unresponsive.
- What happens when an image is deleted from Stash but its fingerprint still exists? Orphaned fingerprints should be cleaned up during the next batch scan.
- What happens when the same image exists in different formats (e.g., JPEG vs PNG)? Perceptual matching should still identify them as duplicates since the visual content is the same.
- What happens when two images are only slightly similar (e.g., consecutive frames from a video)? The similarity threshold should be tunable to avoid false positives — the default should favour precision over recall.
- What happens when an image that was part of a duplicate group is deleted outside the plugin? The group tag should remain on surviving images; a single-member group is acceptable until the next cleanup.
- What happens when a new match bridges two existing duplicate groups? The groups must be merged — all images receive the same group tag, and the old group tag is removed.
- What happens when resolution is run on a group where the highest-resolution image has no metadata but a lower-resolution duplicate has performers and tags? The metadata from all group members is merged onto the keeper regardless of which image originally had it.
- What happens when a group contains only one image (e.g., its duplicates were already manually deleted)? The resolve task should skip single-member groups and clean up the orphaned group tag.

## Requirements

### Functional Requirements

- **FR-001**: The plugin MUST generate perceptual fingerprints for images that detect visual similarity regardless of file format, compression quality, resolution, or cropping differences.
- **FR-002**: The plugin MUST support a batch scan task that processes all un-fingerprinted images in the library and identifies duplicate groups.
- **FR-003**: The plugin MUST automatically fingerprint and check for duplicates when new images are added to the library via a Stash scan (Image.Create.Post hook).
- **FR-004**: The plugin MUST tag duplicate images with a shared group identifier tag so users can filter and review duplicate groups through the standard Stash interface.
- **FR-005**: The plugin MUST track which images have already been fingerprinted to avoid redundant processing on subsequent batch scans.
- **FR-006**: The plugin MUST provide a dry run task that reports potential duplicates in the log without modifying any library data.
- **FR-007**: The plugin MUST support a configurable similarity threshold that controls how visually similar two images must be to be considered duplicates.
- **FR-008**: The plugin MUST log progress information during batch scans, including the number of images processed and duplicates found.
- **FR-009**: The plugin MUST handle corrupt or unreadable image files gracefully by logging a warning and continuing to process remaining images.
- **FR-010**: The plugin MUST detect exact hash-based duplicates (identical files) in addition to perceptual near-duplicates.
- **FR-011**: The plugin MUST use a duplicate group tagging scheme that allows users to identify which images belong to the same duplicate group (e.g., `dedup:group:001`, `dedup:group:002`).
- **FR-012**: The plugin MUST store perceptual fingerprints persistently so they survive plugin restarts and do not need to be regenerated.
- **FR-013**: The plugin MUST use transitive grouping — if Image A matches Image B and Image B matches Image C, all three MUST be placed in a single duplicate group regardless of whether A and C match directly.
- **FR-014**: The plugin MUST provide a "Resolve Duplicates" task that processes each duplicate group by keeping the highest resolution image (largest pixel dimensions), merging metadata onto it, and deleting the remaining images in the group.
- **FR-015**: During resolution, the plugin MUST merge metadata from all group members onto the keeper image: performers (union), tags (union, excluding dedup-related tags), ratings (highest value wins), and gallery memberships (union — keeper is added to all galleries that any deleted duplicate belonged to).
- **FR-016**: During resolution, when multiple images in a group share the same resolution, the plugin MUST use file size as a tiebreaker (largest file kept, indicating higher quality/less compression).
- **FR-017**: When a new match discovered during hook processing or batch scan bridges two existing duplicate groups, the plugin MUST merge those groups into a single group.

### Key Entities

- **Perceptual Fingerprint**: A compact representation of an image's visual content, used for similarity comparison. Generated once per image and stored persistently. Must be resilient to cropping, resizing, quality changes, and format differences.
- **Duplicate Group**: A set of two or more images that are visually identical or near-identical based on their perceptual fingerprints, formed through transitive matching (if A≈B and B≈C, then {A,B,C} are one group). Identified by a shared tag applied to all member images. Groups may merge when new evidence connects previously separate groups.
- **Similarity Threshold**: A user-configurable value that determines the maximum perceptual distance between two fingerprints for them to be considered duplicates. Controls the trade-off between finding more duplicates (higher recall) and avoiding false matches (higher precision).
- **Processing State**: A per-image marker (tag) indicating whether an image has already been fingerprinted, preventing redundant work on subsequent scans.

## Success Criteria

### Measurable Outcomes

- **SC-001**: The plugin correctly identifies 95%+ of true duplicate pairs in a test library containing exact copies, cropped versions, resized versions, and re-encoded versions of the same images.
- **SC-002**: The plugin produces fewer than 5% false positive matches (images incorrectly grouped as duplicates) at the default similarity threshold.
- **SC-003**: Batch scanning a library of 10,000 images completes within a reasonable timeframe (under 30 minutes) on typical hardware.
- **SC-004**: Automatic deduplication of a single newly added image completes within 10 seconds, even when compared against a library of 10,000 existing fingerprints.
- **SC-005**: Re-running a batch scan on a fully fingerprinted library completes in under 1 minute (skipping already-processed images).
- **SC-006**: Users can identify and review all duplicate groups using standard Stash tag filtering without requiring any external tools or interfaces.
- **SC-007**: After running the resolve task, no metadata (performers, tags, ratings, gallery memberships) from deleted duplicates is lost — all metadata is present on the surviving keeper image.
- **SC-008**: The resolve task correctly selects the highest resolution image as keeper in 100% of cases, falling back to largest file size when resolutions are equal.

## Assumptions

- The plugin follows the established conventions and patterns of the existing plugins in this repository.
- Stash provides accessible file paths for images, allowing the plugin to read image data directly for fingerprint generation.
- Perceptual fingerprints are stored persistently within the plugin's data area so they survive restarts and do not need to be regenerated.
- The default similarity threshold is tuned conservatively to favour precision (fewer false positives) over recall (finding every possible match), since incorrectly grouping unrelated images is more disruptive than missing a marginal duplicate.
- The plugin uses the existing tag system in Stash for duplicate group identification, consistent with how other plugins in this repository mark processed items.
- A "processed" tag (e.g., `auto:dedup`) is applied to fingerprinted images to track processing state, following the same convention as the Username Extractor plugin's `auto:ocr` tag.
- Image deduplication applies only to images (not scenes/videos), as specified in the user description.
- The plugin will require an external image processing dependency (available in the Stash Docker container or installable via package manager) for generating perceptual fingerprints.
