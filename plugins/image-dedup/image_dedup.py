"""
Image Deduplication for Stash

Finds duplicate and near-duplicate images using perceptual hashing (dHash)
via ImageMagick. Groups duplicates transitively using Union-Find, and provides
resolution that keeps the highest-quality version while merging metadata.

Modes:
  scan           - Fingerprint unprocessed images, discover and tag groups
  dry_run        - Preview scan results without modifications
  hook           - Fingerprint single new image on Image.Create.Post
  resolve        - Keep best per group, merge metadata, delete rest
  resolve_dry_run - Preview resolve actions
  cleanup        - Remove orphaned fingerprints and empty groups

Requirements:
  - python3 (stdlib only, no pip packages)
  - imagemagick (magick or convert binary)
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROCESSED_TAG = "auto:dedup"
GROUP_TAG_PREFIX = "dedup:group:"
DEFAULT_THRESHOLD = 10
BATCH_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Logging — Stash SOH protocol via stderr
# ---------------------------------------------------------------------------

def _log(level_char, msg):
    print(f"\x01{level_char}\x02{msg}\n", file=sys.stderr, flush=True)


def log_trace(msg):
    _log("t", msg)


def log_debug(msg):
    _log("d", msg)


def log_info(msg):
    _log("i", msg)


def log_warning(msg):
    _log("w", msg)


def log_error(msg):
    _log("e", msg)


def log_progress(value):
    _log("p", str(min(max(0.0, value), 1.0)))


# ---------------------------------------------------------------------------
# GraphQL helper
# ---------------------------------------------------------------------------

def _read_api_key():
    """Read Stash API key from config.yml if available."""
    config_paths = [
        os.path.join(os.environ.get("STASH_METADATA", ""), "config.yml"),
        os.path.expanduser("~/.stash/config.yml"),
        "/root/.stash/config.yml",
    ]
    for path in config_paths:
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("api_key:"):
                            return line.split(":", 1)[1].strip().strip('"').strip("'")
            except OSError:
                pass
    return None


# Cache the API key so we only read config once
_API_KEY = None


def graphql_request(connection, query, variables=None):
    global _API_KEY

    scheme = connection.get("Scheme", "http")
    port = connection.get("Port", 9999)
    url = f"{scheme}://localhost:{port}/graphql"

    payload = json.dumps({"query": query, "variables": variables or {}}).encode()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Prefer API key (doesn't expire) over session cookie
    if _API_KEY is None:
        _API_KEY = _read_api_key() or ""
    if _API_KEY:
        headers["ApiKey"] = _API_KEY
    else:
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

IMAGE_FRAGMENT = """
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
"""


def find_or_create_tag(connection, tag_name):
    """Find a tag by name, creating it if it doesn't exist. Returns tag ID."""
    query = """
    query FindTags($filter: FindFilterType, $tag_filter: TagFilterType) {
        findTags(filter: $filter, tag_filter: $tag_filter) {
            tags { id name }
        }
    }
    """
    data = graphql_request(connection, query, {
        "filter": {"per_page": 5},
        "tag_filter": {"name": {"value": tag_name, "modifier": "EQUALS"}},
    })
    tags = data.get("findTags", {}).get("tags", [])
    for t in tags:
        if t["name"].lower() == tag_name.lower():
            return t["id"]

    mutation = """
    mutation TagCreate($input: TagCreateInput!) {
        tagCreate(input: $input) { id }
    }
    """
    data = graphql_request(connection, mutation, {"input": {"name": tag_name}})
    tag_id = data["tagCreate"]["id"]
    log_info(f"Created tag '{tag_name}' (id={tag_id})")
    return tag_id


def find_unprocessed_images(connection, processed_tag_id, page=1, per_page=BATCH_PAGE_SIZE):
    """Find images NOT tagged with the processed tag."""
    query = f"""
    query FindImages($filter: FindFilterType, $image_filter: ImageFilterType) {{
        findImages(filter: $filter, image_filter: $image_filter) {{
            count
            images {{ {IMAGE_FRAGMENT} }}
        }}
    }}
    """
    variables = {
        "filter": {"page": page, "per_page": per_page},
        "image_filter": {
            "tags": {
                "value": [processed_tag_id],
                "modifier": "EXCLUDES",
                "depth": 0,
            }
        },
    }
    data = graphql_request(connection, query, variables)
    result = data.get("findImages", {})
    return result.get("images", []), result.get("count", 0)


def find_images_by_tag(connection, tag_id, page=1, per_page=BATCH_PAGE_SIZE):
    """Find images tagged with a specific tag."""
    query = f"""
    query FindImages($filter: FindFilterType, $image_filter: ImageFilterType) {{
        findImages(filter: $filter, image_filter: $image_filter) {{
            count
            images {{ {IMAGE_FRAGMENT} }}
        }}
    }}
    """
    variables = {
        "filter": {"page": page, "per_page": per_page},
        "image_filter": {
            "tags": {
                "value": [tag_id],
                "modifier": "INCLUDES",
                "depth": 0,
            }
        },
    }
    data = graphql_request(connection, query, variables)
    result = data.get("findImages", {})
    return result.get("images", []), result.get("count", 0)


def get_image(connection, image_id):
    """Get a single image by ID with all metadata fields."""
    query = f"""
    query FindImage($id: ID!) {{
        findImage(id: $id) {{ {IMAGE_FRAGMENT} }}
    }}
    """
    data = graphql_request(connection, query, {"id": str(image_id)})
    return data.get("findImage")


def update_image(connection, image_id, tag_ids=None, performer_ids=None,
                 rating100=None):
    """Update an image's tags, performers, and/or rating."""
    mutation = """
    mutation ImageUpdate($input: ImageUpdateInput!) {
        imageUpdate(input: $input) { id }
    }
    """
    inp = {"id": str(image_id)}
    if tag_ids is not None:
        inp["tag_ids"] = tag_ids
    if performer_ids is not None:
        inp["performer_ids"] = performer_ids
    if rating100 is not None:
        inp["rating100"] = rating100
    graphql_request(connection, mutation, {"input": inp})


def destroy_image(connection, image_id):
    """Delete an image and its file from disk."""
    mutation = """
    mutation ImageDestroy($input: ImageDestroyInput!) {
        imageDestroy(input: $input)
    }
    """
    graphql_request(connection, mutation, {
        "input": {
            "id": str(image_id),
            "delete_file": True,
            "delete_generated": True,
        }
    })


def add_images_to_gallery(connection, gallery_id, image_ids):
    """Add images to a gallery."""
    mutation = """
    mutation AddImages($input: GalleryAddInput!) {
        addImagesToGallery(input: $input)
    }
    """
    graphql_request(connection, mutation, {
        "input": {
            "gallery_id": str(gallery_id),
            "image_ids": [str(i) for i in image_ids],
        }
    })


def destroy_tag(connection, tag_id):
    """Delete a tag."""
    mutation = """
    mutation TagDestroy($input: TagDestroyInput!) {
        tagDestroy(input: $input)
    }
    """
    graphql_request(connection, mutation, {"input": {"id": str(tag_id)}})


# ---------------------------------------------------------------------------
# Image data extraction helpers
# ---------------------------------------------------------------------------

def get_image_path(image):
    """Extract file path from an image dict."""
    for vf in image.get("visual_files", []):
        path = vf.get("path")
        if path:
            return path
    return None


def get_image_dimensions(image):
    """Extract (width, height, file_size) from an image dict."""
    for vf in image.get("visual_files", []):
        w = vf.get("width")
        h = vf.get("height")
        s = vf.get("size")
        if w and h:
            return w, h, s or 0
    return 0, 0, 0


# ---------------------------------------------------------------------------
# SQLite database
# ---------------------------------------------------------------------------

def init_db(db_path):
    """Initialize SQLite database, creating tables if needed. Returns connection."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fingerprints (
            image_id    INTEGER PRIMARY KEY,
            file_hash   TEXT NOT NULL,
            dhash_full  INTEGER NOT NULL,
            dhash_crop  INTEGER NOT NULL,
            width       INTEGER,
            height      INTEGER,
            file_size   INTEGER,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS groups (
            group_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_name    TEXT NOT NULL UNIQUE,
            resolved    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS group_members (
            group_id    INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
            image_id    INTEGER NOT NULL REFERENCES fingerprints(image_id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, image_id)
        );
    """)
    conn.commit()
    return conn


def _to_signed64(val):
    """Convert unsigned 64-bit int to signed for SQLite storage."""
    if val >= (1 << 63):
        val -= (1 << 64)
    return val


def _to_unsigned64(val):
    """Convert signed 64-bit int back to unsigned for hash operations."""
    if val < 0:
        val += (1 << 64)
    return val


def upsert_fingerprint(db, image_id, file_hash, dhash_full, dhash_crop,
                        width=None, height=None, file_size=None):
    """Insert or replace a fingerprint record."""
    db.execute(
        """INSERT OR REPLACE INTO fingerprints
           (image_id, file_hash, dhash_full, dhash_crop, width, height, file_size)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (image_id, file_hash, _to_signed64(dhash_full),
         _to_signed64(dhash_crop), width, height, file_size),
    )
    db.commit()


def get_all_fingerprints(db):
    """Return all fingerprints as list of (image_id, file_hash, dhash_full, dhash_crop)."""
    rows = db.execute(
        "SELECT image_id, file_hash, dhash_full, dhash_crop FROM fingerprints"
    ).fetchall()
    return [(r[0], r[1], _to_unsigned64(r[2]), _to_unsigned64(r[3])) for r in rows]


def get_all_fingerprints_full(db):
    """Return all fingerprints with dimension data."""
    rows = db.execute(
        "SELECT image_id, file_hash, dhash_full, dhash_crop, width, height, file_size "
        "FROM fingerprints"
    ).fetchall()
    return [(r[0], r[1], _to_unsigned64(r[2]), _to_unsigned64(r[3]),
             r[4], r[5], r[6]) for r in rows]


def get_fingerprint(db, image_id):
    """Get a single fingerprint by image ID."""
    row = db.execute(
        "SELECT image_id, file_hash, dhash_full, dhash_crop FROM fingerprints WHERE image_id=?",
        (image_id,),
    ).fetchone()
    if row is None:
        return None
    return (row[0], row[1], _to_unsigned64(row[2]), _to_unsigned64(row[3]))


def get_next_group_number(db):
    """Get the next sequential group number."""
    row = db.execute("SELECT MAX(group_id) FROM groups").fetchone()
    return (row[0] or 0) + 1


def create_group(db, tag_name):
    """Create a new duplicate group. Returns group_id."""
    cursor = db.execute(
        "INSERT INTO groups (tag_name) VALUES (?)", (tag_name,)
    )
    db.commit()
    return cursor.lastrowid


def add_group_member(db, group_id, image_id):
    """Add an image to a duplicate group."""
    db.execute(
        "INSERT OR IGNORE INTO group_members (group_id, image_id) VALUES (?, ?)",
        (group_id, image_id),
    )
    db.commit()


def get_group_members(db, group_id):
    """Get all image IDs in a group."""
    rows = db.execute(
        "SELECT image_id FROM group_members WHERE group_id=?", (group_id,)
    ).fetchall()
    return [r[0] for r in rows]


def get_image_group(db, image_id):
    """Get the group_id for an image, or None."""
    row = db.execute(
        "SELECT group_id FROM group_members WHERE image_id=?", (image_id,)
    ).fetchone()
    return row[0] if row else None


def get_all_groups(db):
    """Get all groups as list of (group_id, tag_name, resolved)."""
    return db.execute(
        "SELECT group_id, tag_name, resolved FROM groups"
    ).fetchall()


def get_unresolved_groups(db):
    """Get all unresolved groups as list of (group_id, tag_name)."""
    return db.execute(
        "SELECT group_id, tag_name FROM groups WHERE resolved=0"
    ).fetchall()


def mark_group_resolved(db, group_id):
    """Mark a group as resolved."""
    db.execute("UPDATE groups SET resolved=1 WHERE group_id=?", (group_id,))
    db.commit()


def delete_group(db, group_id):
    """Delete a group and its member records."""
    db.execute("DELETE FROM group_members WHERE group_id=?", (group_id,))
    db.execute("DELETE FROM groups WHERE group_id=?", (group_id,))
    db.commit()


def delete_fingerprint(db, image_id):
    """Delete a fingerprint record."""
    db.execute("DELETE FROM group_members WHERE image_id=?", (image_id,))
    db.execute("DELETE FROM fingerprints WHERE image_id=?", (image_id,))
    db.commit()


def get_all_fingerprint_image_ids(db):
    """Return all image IDs that have fingerprints."""
    rows = db.execute("SELECT image_id FROM fingerprints").fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# ImageMagick & fingerprint generation
# ---------------------------------------------------------------------------

def check_magick():
    """Verify ImageMagick is available. Returns command list or None."""
    if shutil.which("magick"):
        return ["magick"]
    if shutil.which("convert"):
        return ["convert"]
    return None


def _run_magick(magick_cmd, args, timeout=30):
    """Run an ImageMagick command and return stdout bytes."""
    cmd = magick_cmd + args
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ImageMagick failed: {stderr}")
    return result.stdout


def _compute_dhash_from_bytes(raw_bytes):
    """Compute 64-bit dHash from 72 raw grayscale bytes (9×8 image)."""
    if len(raw_bytes) < 72:
        raise ValueError(f"Expected 72 bytes, got {len(raw_bytes)}")
    hash_value = 0
    for row in range(8):
        for col in range(8):
            left = raw_bytes[row * 9 + col]
            right = raw_bytes[row * 9 + col + 1]
            if left > right:
                hash_value |= 1 << (row * 8 + col)
    return hash_value


def compute_dhash_full(image_path, magick_cmd):
    """Compute dHash of the full image."""
    raw = _run_magick(magick_cmd, [
        image_path, "-colorspace", "Gray", "-resize", "9x8!",
        "-depth", "8", "gray:-",
    ])
    return _compute_dhash_from_bytes(raw)


def compute_dhash_crop(image_path, magick_cmd):
    """Compute dHash of the center 60% crop."""
    raw = _run_magick(magick_cmd, [
        image_path, "-gravity", "Center", "-crop", "60%x60%+0+0",
        "+repage", "-colorspace", "Gray", "-resize", "9x8!",
        "-depth", "8", "gray:-",
    ])
    return _compute_dhash_from_bytes(raw)


def compute_file_hash(file_path):
    """Compute SHA-256 hash of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def fingerprint_image(image_path, magick_cmd):
    """
    Generate all fingerprints for an image.

    Returns (file_hash, dhash_full, dhash_crop) or None if the image
    cannot be processed.
    """
    try:
        file_hash = compute_file_hash(image_path)
        dhash_full = compute_dhash_full(image_path, magick_cmd)
        dhash_crop = compute_dhash_crop(image_path, magick_cmd)
        return file_hash, dhash_full, dhash_crop
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        log_warning(f"Cannot fingerprint {image_path}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Union-Find (Disjoint Set Union)
# ---------------------------------------------------------------------------

class UnionFind:
    """Union-Find with path compression and union by rank."""

    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
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
        """Return dict of root → set of members (only groups with 2+ members)."""
        result = defaultdict(set)
        for x in self.parent:
            result[self.find(x)].add(x)
        return {k: v for k, v in result.items() if len(v) > 1}


# ---------------------------------------------------------------------------
# Similarity comparison
# ---------------------------------------------------------------------------

def hamming_distance(a, b):
    """Hamming distance between two 64-bit integers."""
    return bin(a ^ b).count("1")


def is_duplicate(fp_a, fp_b, threshold=DEFAULT_THRESHOLD):
    """
    Check if two fingerprints are duplicates using dual-hash cross-comparison.

    fp_a and fp_b are tuples of (image_id, file_hash, dhash_full, dhash_crop)
    or at minimum (_, _, dhash_full, dhash_crop).

    Returns True if the minimum Hamming distance across 4 comparisons
    is within the threshold.
    """
    # Exact file hash match — always a duplicate
    if fp_a[1] == fp_b[1]:
        return True

    full_a, crop_a = fp_a[2], fp_a[3]
    full_b, crop_b = fp_b[2], fp_b[3]

    min_dist = min(
        hamming_distance(full_a, full_b),
        hamming_distance(crop_a, crop_b),
        hamming_distance(full_a, crop_b),
        hamming_distance(crop_a, full_b),
    )
    return min_dist <= threshold


# ---------------------------------------------------------------------------
# Batch fingerprinting (US1 — T010)
# ---------------------------------------------------------------------------

def fingerprint_all_unprocessed(connection, db, magick_cmd):
    """
    Fingerprint all images not yet tagged with auto:dedup.

    Returns count of newly fingerprinted images.
    """
    processed_tag_id = find_or_create_tag(connection, PROCESSED_TAG)

    page = 1
    total_count = None
    fingerprinted = 0
    skipped = 0
    processed_so_far = 0

    while True:
        images, count = find_unprocessed_images(
            connection, processed_tag_id, page=page, per_page=BATCH_PAGE_SIZE
        )
        if total_count is None:
            total_count = count
            log_info(f"Found {total_count} unprocessed images")

        if not images:
            break

        for image in images:
            image_id = int(image["id"])
            image_path = get_image_path(image)

            if not image_path:
                log_warning(f"Image {image_id}: no file path, skipping")
                skipped += 1
                processed_so_far += 1
                if total_count > 0:
                    log_progress(processed_so_far / total_count)
                continue

            if not os.path.exists(image_path):
                log_warning(f"Image {image_id}: file not found at {image_path}, skipping")
                skipped += 1
                processed_so_far += 1
                if total_count > 0:
                    log_progress(processed_so_far / total_count)
                continue

            result = fingerprint_image(image_path, magick_cmd)
            if result is None:
                skipped += 1
                processed_so_far += 1
                if total_count > 0:
                    log_progress(processed_so_far / total_count)
                continue

            file_hash, dhash_full, dhash_crop = result
            w, h, size = get_image_dimensions(image)

            upsert_fingerprint(db, image_id, file_hash, dhash_full, dhash_crop,
                               width=w, height=h, file_size=size)

            # Add processed tag, preserving existing tags
            existing_tag_ids = [t["id"] for t in image.get("tags", [])]
            if processed_tag_id not in existing_tag_ids:
                existing_tag_ids.append(processed_tag_id)
            try:
                update_image(connection, image_id, tag_ids=existing_tag_ids)
            except Exception as exc:
                log_warning(f"Image {image_id}: failed to update tags: {exc}")

            fingerprinted += 1
            processed_so_far += 1
            if total_count > 0:
                log_progress(processed_so_far / total_count)

            if fingerprinted % 50 == 0:
                log_info(f"Fingerprinted {fingerprinted} images so far...")

        # Always request page 1 since processed images are excluded by tag filter
        # (they now have auto:dedup tag, so they won't appear again)
        page = 1

    if skipped:
        log_warning(f"Skipped {skipped} images (missing/corrupt)")
    log_info(f"Fingerprinted {fingerprinted} new images")
    return fingerprinted


# ---------------------------------------------------------------------------
# Pairwise comparison & group discovery (US1 — T011)
# ---------------------------------------------------------------------------

def discover_groups(db, threshold=DEFAULT_THRESHOLD):
    """
    Compare all fingerprints pairwise and discover transitive groups.

    Returns list of sets, where each set contains image_ids in a group.
    """
    fingerprints = get_all_fingerprints(db)
    n = len(fingerprints)
    log_info(f"Comparing {n} fingerprints ({n * (n - 1) // 2} pairs)...")

    uf = UnionFind()
    match_count = 0

    for i in range(n):
        for j in range(i + 1, n):
            if is_duplicate(fingerprints[i], fingerprints[j], threshold):
                uf.union(fingerprints[i][0], fingerprints[j][0])
                match_count += 1

    groups = list(uf.groups().values())
    log_info(f"Found {match_count} duplicate pairs → {len(groups)} groups")
    return groups


# ---------------------------------------------------------------------------
# Group tag creation & assignment (US1 — T012)
# ---------------------------------------------------------------------------

def assign_group_tags(connection, db, groups):
    """
    Create Stash tags for duplicate groups and assign them to member images.

    Handles merging when new groups overlap with existing ones.
    """
    if not groups:
        return 0

    processed_tag_id = find_or_create_tag(connection, PROCESSED_TAG)
    new_groups = 0

    for group_image_ids in groups:
        # Check if any members already belong to existing groups
        existing_group_ids = set()
        for img_id in group_image_ids:
            gid = get_image_group(db, img_id)
            if gid is not None:
                existing_group_ids.add(gid)

        if len(existing_group_ids) == 1:
            # All matched members in one existing group — add new members to it
            target_group_id = existing_group_ids.pop()
            existing_members = set(get_group_members(db, target_group_id))
            new_members = group_image_ids - existing_members

            if not new_members:
                continue  # Group already fully assigned

            # Get tag name for the existing group
            row = db.execute(
                "SELECT tag_name FROM groups WHERE group_id=?", (target_group_id,)
            ).fetchone()
            tag_name = row[0]
            tag_id = find_or_create_tag(connection, tag_name)

            for img_id in new_members:
                add_group_member(db, target_group_id, img_id)
                _apply_tag_to_image(connection, img_id, tag_id)

            log_info(f"Extended group {tag_name}: added {len(new_members)} images")

        elif len(existing_group_ids) > 1:
            # Members span multiple groups — merge into one
            _merge_groups(connection, db, existing_group_ids, group_image_ids)

        else:
            # No existing group — create new
            group_num = get_next_group_number(db)
            tag_name = f"{GROUP_TAG_PREFIX}{group_num:04d}"
            group_id = create_group(db, tag_name)
            tag_id = find_or_create_tag(connection, tag_name)

            for img_id in group_image_ids:
                add_group_member(db, group_id, img_id)
                _apply_tag_to_image(connection, img_id, tag_id)

            log_info(f"Created group {tag_name} with {len(group_image_ids)} images")
            new_groups += 1

    return new_groups


def _apply_tag_to_image(connection, image_id, tag_id):
    """Add a tag to an image, preserving existing tags."""
    try:
        image = get_image(connection, image_id)
        if not image:
            return
        existing_tag_ids = [t["id"] for t in image.get("tags", [])]
        if tag_id not in existing_tag_ids:
            existing_tag_ids.append(tag_id)
            update_image(connection, image_id, tag_ids=existing_tag_ids)
    except Exception as exc:
        log_warning(f"Image {image_id}: failed to apply tag: {exc}")


def _remove_tag_from_image(connection, image_id, tag_name):
    """Remove a tag (by name) from an image."""
    try:
        image = get_image(connection, image_id)
        if not image:
            return
        new_tag_ids = [t["id"] for t in image.get("tags", [])
                       if t["name"] != tag_name]
        update_image(connection, image_id, tag_ids=new_tag_ids)
    except Exception as exc:
        log_warning(f"Image {image_id}: failed to remove tag '{tag_name}': {exc}")


def _merge_groups(connection, db, group_ids, all_image_ids):
    """Merge multiple existing groups into one, reassigning all members."""
    group_ids = sorted(group_ids)
    keeper_group_id = group_ids[0]

    row = db.execute(
        "SELECT tag_name FROM groups WHERE group_id=?", (keeper_group_id,)
    ).fetchone()
    keeper_tag_name = row[0]
    keeper_tag_id = find_or_create_tag(connection, keeper_tag_name)

    # Remove old group tags and reassign members
    for old_group_id in group_ids[1:]:
        old_row = db.execute(
            "SELECT tag_name FROM groups WHERE group_id=?", (old_group_id,)
        ).fetchone()
        old_tag_name = old_row[0]

        old_members = get_group_members(db, old_group_id)
        for img_id in old_members:
            _remove_tag_from_image(connection, img_id, old_tag_name)
            add_group_member(db, keeper_group_id, img_id)
            _apply_tag_to_image(connection, img_id, keeper_tag_id)

        # Try to clean up old tag
        try:
            old_tag_id = find_or_create_tag(connection, old_tag_name)
            destroy_tag(connection, old_tag_id)
        except Exception:
            pass

        delete_group(db, old_group_id)

    # Add any new members not yet in the keeper group
    existing_members = set(get_group_members(db, keeper_group_id))
    for img_id in all_image_ids:
        if img_id not in existing_members:
            add_group_member(db, keeper_group_id, img_id)
            _apply_tag_to_image(connection, img_id, keeper_tag_id)

    log_info(f"Merged {len(group_ids)} groups into {keeper_tag_name} "
             f"({len(all_image_ids)} images)")


# ---------------------------------------------------------------------------
# Mode: scan (US1 — T013)
# ---------------------------------------------------------------------------

def mode_scan(connection):
    """Batch scan: fingerprint unprocessed images, discover and tag groups."""
    magick_cmd = check_magick()
    if not magick_cmd:
        msg = (
            "ImageMagick not found. Install with:\n"
            "  macOS:  brew install imagemagick\n"
            "  Alpine: apk add imagemagick\n"
            "  Debian: apt-get install imagemagick"
        )
        log_error(msg)
        return msg

    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(plugin_dir, "dedup.db")
    db = init_db(db_path)

    try:
        fingerprinted = fingerprint_all_unprocessed(connection, db, magick_cmd)
        groups = discover_groups(db, DEFAULT_THRESHOLD)
        new_groups = assign_group_tags(connection, db, groups)

        msg = (f"Scan complete: {fingerprinted} images fingerprinted, "
               f"{new_groups} new duplicate groups found "
               f"({len(groups)} total groups)")
        log_info(msg)
        return msg
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Mode: hook (US2 — T014, T015)
# ---------------------------------------------------------------------------

def find_matches(db, new_fp, threshold=DEFAULT_THRESHOLD):
    """Compare a new fingerprint against all existing ones. Returns matching image_ids."""
    all_fps = get_all_fingerprints(db)
    matches = []
    for existing in all_fps:
        if existing[0] == new_fp[0]:
            continue  # Skip self
        if is_duplicate(new_fp, existing, threshold):
            matches.append(existing[0])
    return matches


def determine_group_action(db, matching_ids):
    """
    Determine what to do with matching images.

    Returns:
      None                     — no matches
      ("existing", group_id)   — all matches in one existing group
      ("merge", [group_ids])   — matches span multiple groups
      ("new", matching_ids)    — matches have no group yet
    """
    if not matching_ids:
        return None

    group_ids = set()
    ungrouped = []
    for img_id in matching_ids:
        gid = get_image_group(db, img_id)
        if gid is not None:
            group_ids.add(gid)
        else:
            ungrouped.append(img_id)

    if len(group_ids) == 1 and not ungrouped:
        return ("existing", group_ids.pop())
    elif len(group_ids) > 1:
        return ("merge", sorted(group_ids))
    elif len(group_ids) == 1:
        return ("existing", group_ids.pop())
    else:
        return ("new", matching_ids)


def mode_hook(connection, hook_context):
    """Handle Image.Create.Post hook — fingerprint and check for duplicates."""
    hook_type = hook_context.get("type", "")
    hook_id = hook_context.get("id")

    if "Image" not in hook_type:
        log_debug(f"Hook {hook_type}: not an image hook, skipping")
        return "Not an image hook"

    if not hook_id:
        log_debug(f"Hook {hook_type}: no ID in context")
        return "No ID"

    magick_cmd = check_magick()
    if not magick_cmd:
        log_warning("ImageMagick not found — cannot fingerprint")
        return "ImageMagick not available"

    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(plugin_dir, "dedup.db")
    db = init_db(db_path)

    try:
        image = get_image(connection, hook_id)
        if not image:
            return f"Image {hook_id} not found"

        image_id = int(image["id"])
        image_path = get_image_path(image)
        if not image_path or not os.path.exists(image_path):
            return f"Image {hook_id}: file not accessible"

        # Fingerprint
        result = fingerprint_image(image_path, magick_cmd)
        if result is None:
            return f"Image {hook_id}: fingerprinting failed"

        file_hash, dhash_full, dhash_crop = result
        w, h, size = get_image_dimensions(image)
        upsert_fingerprint(db, image_id, file_hash, dhash_full, dhash_crop,
                           width=w, height=h, file_size=size)

        # Tag as processed
        processed_tag_id = find_or_create_tag(connection, PROCESSED_TAG)
        existing_tag_ids = [t["id"] for t in image.get("tags", [])]
        if processed_tag_id not in existing_tag_ids:
            existing_tag_ids.append(processed_tag_id)
            update_image(connection, image_id, tag_ids=existing_tag_ids)

        # Check for matches
        new_fp = (image_id, file_hash, dhash_full, dhash_crop)
        matches = find_matches(db, new_fp, DEFAULT_THRESHOLD)
        action = determine_group_action(db, matches)

        if action is None:
            log_info(f"Image {hook_id}: no duplicates found")
            return f"Image {hook_id}: no duplicates found"

        action_type = action[0]

        if action_type == "existing":
            # Add to existing group
            group_id = action[1]
            row = db.execute(
                "SELECT tag_name FROM groups WHERE group_id=?", (group_id,)
            ).fetchone()
            tag_name = row[0]
            tag_id = find_or_create_tag(connection, tag_name)
            add_group_member(db, group_id, image_id)
            _apply_tag_to_image(connection, image_id, tag_id)
            log_info(f"Image {hook_id}: added to existing group {tag_name}")
            return f"Image {hook_id}: duplicate found in {tag_name}"

        elif action_type == "merge":
            # Merge groups
            group_ids = action[1]
            all_ids = set(matches + [image_id])
            _merge_groups(connection, db, set(group_ids), all_ids)
            return f"Image {hook_id}: bridged {len(group_ids)} groups"

        elif action_type == "new":
            # Create new group
            group_num = get_next_group_number(db)
            tag_name = f"{GROUP_TAG_PREFIX}{group_num:04d}"
            group_id = create_group(db, tag_name)
            tag_id = find_or_create_tag(connection, tag_name)

            all_members = set(matches + [image_id])
            for mid in all_members:
                add_group_member(db, group_id, mid)
                _apply_tag_to_image(connection, mid, tag_id)

            log_info(f"Image {hook_id}: created new group {tag_name} "
                     f"with {len(all_members)} images")
            return f"Image {hook_id}: new duplicate group {tag_name}"

        return f"Image {hook_id}: processed"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Mode: resolve (US3 — T016, T017, T018)
# ---------------------------------------------------------------------------

def select_keeper(images):
    """
    Select the keeper image from a list of image dicts.

    Criteria: highest resolution (width × height), then largest file size,
    then lowest image ID.

    Returns (keeper, [non_keepers]).
    """
    def sort_key(img):
        w, h, s = get_image_dimensions(img)
        return (w * h, s, -int(img["id"]))

    sorted_images = sorted(images, key=sort_key, reverse=True)
    return sorted_images[0], sorted_images[1:]


def merge_metadata(keeper, duplicates, processed_tag_id=None):
    """
    Compute merged metadata from keeper + all duplicates.

    Returns dict with merged performer_ids, tag_ids, rating100, gallery_ids.
    """
    all_images = [keeper] + list(duplicates)

    # Performers — union of all IDs
    performer_ids = set()
    for img in all_images:
        for p in img.get("performers", []):
            performer_ids.add(p["id"])

    # Tags — union excluding dedup tags, then add back processed tag
    tag_ids = set()
    for img in all_images:
        for t in img.get("tags", []):
            name = t["name"]
            if name.startswith(GROUP_TAG_PREFIX) or name == PROCESSED_TAG:
                continue
            tag_ids.add(t["id"])
    if processed_tag_id:
        tag_ids.add(processed_tag_id)

    # Rating — max
    rating = 0
    for img in all_images:
        r = img.get("rating100") or 0
        if r > rating:
            rating = r

    # Galleries — union of all IDs
    gallery_ids = set()
    for img in all_images:
        for g in img.get("galleries", []):
            gallery_ids.add(g["id"])

    return {
        "performer_ids": sorted(performer_ids),
        "tag_ids": sorted(tag_ids),
        "rating100": rating if rating > 0 else None,
        "gallery_ids": sorted(gallery_ids),
    }


def mode_resolve(connection):
    """Resolve all duplicate groups: keep best, merge metadata, delete rest."""
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(plugin_dir, "dedup.db")
    db = init_db(db_path)

    try:
        processed_tag_id = find_or_create_tag(connection, PROCESSED_TAG)
        unresolved = get_unresolved_groups(db)

        if not unresolved:
            msg = "No unresolved duplicate groups"
            log_info(msg)
            return msg

        log_info(f"Resolving {len(unresolved)} duplicate groups...")
        resolved_count = 0
        kept_count = 0
        deleted_count = 0

        for group_id, tag_name in unresolved:
            member_ids = get_group_members(db, group_id)

            # Fetch full image data for each member
            images = []
            for mid in member_ids:
                img = get_image(connection, mid)
                if img:
                    images.append(img)

            if len(images) < 2:
                # Single member or no valid images — clean up
                for mid in member_ids:
                    _remove_tag_from_image(connection, mid, tag_name)
                try:
                    tag_id = find_or_create_tag(connection, tag_name)
                    destroy_tag(connection, tag_id)
                except Exception:
                    pass
                delete_group(db, group_id)
                log_info(f"Group {tag_name}: cleaned up (< 2 valid members)")
                continue

            keeper, duplicates = select_keeper(images)
            merged = merge_metadata(keeper, duplicates, processed_tag_id)

            keeper_id = int(keeper["id"])
            kw, kh, ks = get_image_dimensions(keeper)

            # Apply merged metadata to keeper
            update_image(
                connection, keeper_id,
                tag_ids=merged["tag_ids"],
                performer_ids=merged["performer_ids"],
                rating100=merged["rating100"],
            )

            # Add keeper to galleries from duplicates
            keeper_gallery_ids = {g["id"] for g in keeper.get("galleries", [])}
            for gal_id in merged["gallery_ids"]:
                if gal_id not in keeper_gallery_ids:
                    try:
                        add_images_to_gallery(connection, gal_id, [keeper_id])
                    except Exception as exc:
                        log_warning(f"Failed to add image {keeper_id} "
                                    f"to gallery {gal_id}: {exc}")

            # Delete duplicates
            dup_ids = []
            for dup in duplicates:
                dup_id = int(dup["id"])
                try:
                    destroy_image(connection, dup_id)
                    delete_fingerprint(db, dup_id)
                    dup_ids.append(dup_id)
                    deleted_count += 1
                except Exception as exc:
                    log_warning(f"Failed to delete image {dup_id}: {exc}")

            mark_group_resolved(db, group_id)
            resolved_count += 1
            kept_count += 1

            n_perfs = len(merged["performer_ids"])
            n_tags = len(merged["tag_ids"])
            n_gals = len(merged["gallery_ids"])
            log_info(f"Group {tag_name}: kept image {keeper_id} ({kw}x{kh}), "
                     f"deleted {len(dup_ids)} duplicates, "
                     f"merged {n_perfs} performers/{n_tags} tags/{n_gals} galleries")

        msg = (f"Resolved {resolved_count} groups: "
               f"kept {kept_count} images, deleted {deleted_count} duplicates")
        log_info(msg)
        return msg
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Mode: resolve_dry_run (US3 — T019)
# ---------------------------------------------------------------------------

def mode_resolve_dry_run(connection):
    """Preview resolve actions without making changes."""
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(plugin_dir, "dedup.db")
    db = init_db(db_path)

    try:
        processed_tag_id = find_or_create_tag(connection, PROCESSED_TAG)
        unresolved = get_unresolved_groups(db)

        if not unresolved:
            msg = "No unresolved duplicate groups"
            log_info(msg)
            return msg

        log_info(f"[DRY RUN] Previewing resolution of {len(unresolved)} groups...")

        for group_id, tag_name in unresolved:
            member_ids = get_group_members(db, group_id)
            images = []
            for mid in member_ids:
                img = get_image(connection, mid)
                if img:
                    images.append(img)

            if len(images) < 2:
                log_info(f"[DRY RUN] Group {tag_name}: would clean up "
                         f"(< 2 valid members)")
                continue

            keeper, duplicates = select_keeper(images)
            merged = merge_metadata(keeper, duplicates, processed_tag_id)

            kw, kh, ks = get_image_dimensions(keeper)
            log_info(f"[DRY RUN] Group {tag_name}:")
            log_info(f"  Would keep: image {keeper['id']} "
                     f"({kw}x{kh}, {ks} bytes)")

            for dup in duplicates:
                dw, dh, ds = get_image_dimensions(dup)
                log_info(f"  Would delete: image {dup['id']} "
                         f"({dw}x{dh}, {ds} bytes)")

            perf_names = [p["name"] for img in [keeper] + list(duplicates)
                          for p in img.get("performers", [])]
            if perf_names:
                log_info(f"  Would merge performers: {', '.join(set(perf_names))}")

            tag_names = [t["name"] for t in keeper.get("tags", [])
                         if not t["name"].startswith(GROUP_TAG_PREFIX)
                         and t["name"] != PROCESSED_TAG]
            for dup in duplicates:
                tag_names.extend(t["name"] for t in dup.get("tags", [])
                                 if not t["name"].startswith(GROUP_TAG_PREFIX)
                                 and t["name"] != PROCESSED_TAG)
            if tag_names:
                log_info(f"  Would merge tags: {', '.join(set(tag_names))}")

            if merged["rating100"]:
                log_info(f"  Would set rating: {merged['rating100']}")

            gal_titles = [g["title"] for img in [keeper] + list(duplicates)
                          for g in img.get("galleries", []) if g.get("title")]
            if gal_titles:
                log_info(f"  Would merge galleries: {', '.join(set(gal_titles))}")

        msg = f"[DRY RUN] {len(unresolved)} groups would be resolved"
        log_info(msg)
        return msg
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Mode: dry_run (US4 — T020)
# ---------------------------------------------------------------------------

def mode_dry_run(connection):
    """Preview scan results without making any changes."""
    magick_cmd = check_magick()
    if not magick_cmd:
        msg = (
            "ImageMagick not found. Install with:\n"
            "  macOS:  brew install imagemagick\n"
            "  Alpine: apk add imagemagick\n"
            "  Debian: apt-get install imagemagick"
        )
        log_error(msg)
        return msg

    # Use in-memory database — nothing persisted
    db = init_db(":memory:")
    processed_tag_id = find_or_create_tag(connection, PROCESSED_TAG)

    try:
        # Fingerprint all unprocessed images into memory (no Stash mutations)
        page = 1
        total_count = None
        fingerprinted = 0
        processed_so_far = 0

        while True:
            images, count = find_unprocessed_images(
                connection, processed_tag_id, page=page, per_page=BATCH_PAGE_SIZE
            )
            if total_count is None:
                total_count = count
                log_info(f"[DRY RUN] Found {total_count} unprocessed images")

            if not images:
                break

            for image in images:
                image_id = int(image["id"])
                image_path = get_image_path(image)

                if not image_path or not os.path.exists(image_path):
                    processed_so_far += 1
                    continue

                result = fingerprint_image(image_path, magick_cmd)
                if result is None:
                    processed_so_far += 1
                    continue

                file_hash, dhash_full, dhash_crop = result
                w, h, size = get_image_dimensions(image)
                upsert_fingerprint(db, image_id, file_hash, dhash_full, dhash_crop,
                                   width=w, height=h, file_size=size)
                fingerprinted += 1
                processed_so_far += 1
                if total_count > 0:
                    log_progress(processed_so_far / total_count)

            page += 1

        # Also load existing fingerprints from persistent DB if it exists
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        persistent_path = os.path.join(plugin_dir, "dedup.db")
        if os.path.exists(persistent_path):
            persistent_db = sqlite3.connect(persistent_path)
            existing = persistent_db.execute(
                "SELECT image_id, file_hash, dhash_full, dhash_crop, "
                "width, height, file_size FROM fingerprints"
            ).fetchall()
            for row in existing:
                db.execute(
                    "INSERT OR IGNORE INTO fingerprints "
                    "(image_id, file_hash, dhash_full, dhash_crop, "
                    "width, height, file_size) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
            db.commit()
            persistent_db.close()

        # Discover groups
        groups = discover_groups(db, DEFAULT_THRESHOLD)

        if not groups:
            msg = f"[DRY RUN] No duplicates found ({fingerprinted} images analysed)"
            log_info(msg)
            return msg

        for i, group in enumerate(groups):
            log_info(f"[DRY RUN] Potential duplicate group {i + 1}:")
            for img_id in sorted(group):
                fp = db.execute(
                    "SELECT width, height, file_size FROM fingerprints WHERE image_id=?",
                    (img_id,)
                ).fetchone()
                w, h, s = fp if fp else (0, 0, 0)
                log_info(f"  Image {img_id}: {w}x{h}, {s or '?'} bytes")

        msg = (f"[DRY RUN] Found {len(groups)} potential duplicate groups "
               f"across {fingerprinted} newly analysed images")
        log_info(msg)
        return msg
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Mode: cleanup (Phase 7 — T021)
# ---------------------------------------------------------------------------

def mode_cleanup(connection):
    """Remove orphaned fingerprints and empty/single-member groups."""
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(plugin_dir, "dedup.db")

    if not os.path.exists(db_path):
        return "No database found — nothing to clean up"

    db = init_db(db_path)

    try:
        # Clean orphaned fingerprints
        orphaned = 0
        image_ids = get_all_fingerprint_image_ids(db)
        for img_id in image_ids:
            try:
                img = get_image(connection, img_id)
                if img is None:
                    delete_fingerprint(db, img_id)
                    orphaned += 1
                    log_debug(f"Removed orphaned fingerprint for image {img_id}")
            except Exception:
                pass

        # Clean empty and single-member groups
        empty_groups = 0
        single_groups = 0
        all_groups = get_all_groups(db)

        for group_id, tag_name, resolved in all_groups:
            members = get_group_members(db, group_id)

            if len(members) == 0:
                try:
                    tid = find_or_create_tag(connection, tag_name)
                    destroy_tag(connection, tid)
                except Exception:
                    pass
                delete_group(db, group_id)
                empty_groups += 1

            elif len(members) == 1:
                _remove_tag_from_image(connection, members[0], tag_name)
                try:
                    tid = find_or_create_tag(connection, tag_name)
                    destroy_tag(connection, tid)
                except Exception:
                    pass
                delete_group(db, group_id)
                single_groups += 1

        msg = (f"Cleanup: removed {orphaned} orphaned fingerprints, "
               f"{empty_groups} empty groups, {single_groups} single-member groups")
        log_info(msg)
        return msg
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    raw_input = sys.stdin.read()
    try:
        json_input = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Failed to parse input: {exc}"}))
        sys.exit(1)

    connection = json_input.get("server_connection", {})
    args = json_input.get("args", {})
    mode = args.get("mode", "")
    hook_context = args.get("hookContext")

    try:
        log_info("Image Deduplication starting")

        if hook_context:
            result = mode_hook(connection, hook_context)
        elif mode == "scan":
            result = mode_scan(connection)
        elif mode == "dry_run":
            result = mode_dry_run(connection)
        elif mode == "resolve":
            result = mode_resolve(connection)
        elif mode == "resolve_dry_run":
            result = mode_resolve_dry_run(connection)
        elif mode == "cleanup":
            result = mode_cleanup(connection)
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
