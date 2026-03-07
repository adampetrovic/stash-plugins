"""
Username Extractor for Stash

Scans all scenes and images, detects the source platform (TikTok, Instagram)
from watermarks via OCR, sets the studio accordingly, extracts the primary
username, and tags items as processed.

Strategy:
  - Extract multiple frames spread across the video
  - OCR each frame with three preprocessing variants:
      1. Original (good for dark text / high-contrast watermarks)
      2. Negated  (white text → black)
      3. White-text threshold (best for semi-transparent watermarks)
  - Detect platform from keywords (TikTok, AO VIVO, In diretta, …)
  - Extract @username / username patterns
  - Vote across all frames × variants — highest-scoring wins
  - Set studio to TikTok / Instagram only when confidently detected
  - Tag scene/image with "auto:ocr" so it is not re-scanned

Requirements:
  - python3 (stdlib only, no pip packages)
  - ffmpeg (included with Stash)
  - tesseract OCR (apk add tesseract-ocr tesseract-ocr-data-eng)
"""

import collections
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROCESSED_TAG = "auto:ocr"
USERNAME_MARKER = "Extracted Username:"
NUM_FRAMES = 5
MIN_USERNAME_LEN = 3
MAX_USERNAME_LEN = 30

# Platform display names (must match Stash studio names exactly)
PLATFORM_TIKTOK = "TikTok"
PLATFORM_INSTAGRAM = "Instagram"

# Minimum total platform score across all frames to set the studio.
# Each strong keyword hit scores 10, so ≥10 means at least one clear match.
PLATFORM_CONFIDENCE_THRESHOLD = 10

# Instagram UI noise — words that appear in the app UI across languages and
# should never be treated as usernames.
NOISE_WORDS = {
    # English UI
    "live", "share", "send", "follow", "following", "followers",
    "comment", "comments", "likes", "like", "reply", "search",
    "explore", "reels", "home", "profile", "saved", "settings",
    "viewers", "viewer", "watching",
    # Portuguese UI
    "ao", "vivo", "entrou", "procurar", "ver", "traducao",
    "compartilhar", "seguir", "seguindo", "seguidores", "curtir",
    # Italian UI
    "diretta", "cerca", "condividi", "segui",
    # Spanish UI
    "buscar", "compartir", "gusta",
    # French UI
    "direct", "rechercher", "partager", "suivre",
    # German UI
    "suchen", "teilen", "folgen",
    # Platform names (not usernames)
    "tiktok", "instagram", "reels", "reel",
    # Common short words OCR picks up
    "the", "and", "for", "you", "your", "with", "this", "that",
    "from", "have", "has", "had", "not", "are", "was", "were",
    "been", "can", "how", "long", "hold", "show", "some", "well",
    "done", "always", "enjoying", "time", "together", "our",
    "bye", "hey", "omg", "lol", "wow",
}


# ---------------------------------------------------------------------------
# Logging – external plugins log via stderr with SOH <level> STX prefix
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

def graphql_request(connection, query, variables=None):
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

    # Create it
    mutation = """
    mutation TagCreate($input: TagCreateInput!) {
        tagCreate(input: $input) { id }
    }
    """
    data = graphql_request(connection, mutation, {"input": {"name": tag_name}})
    tag_id = data["tagCreate"]["id"]
    log_info(f"Created tag '{tag_name}' (id={tag_id})")
    return tag_id


def find_or_create_studio(connection, studio_name):
    """Find a studio by name, creating it if it doesn't exist. Returns studio ID."""
    query = """
    query FindStudios($filter: FindFilterType, $studio_filter: StudioFilterType) {
        findStudios(filter: $filter, studio_filter: $studio_filter) {
            studios { id name }
        }
    }
    """
    data = graphql_request(connection, query, {
        "filter": {"per_page": 5},
        "studio_filter": {"name": {"value": studio_name, "modifier": "EQUALS"}},
    })
    studios = data.get("findStudios", {}).get("studios", [])
    for s in studios:
        if s["name"].lower() == studio_name.lower():
            return s["id"]

    # Create it
    mutation = """
    mutation StudioCreate($input: StudioCreateInput!) {
        studioCreate(input: $input) { id }
    }
    """
    data = graphql_request(connection, mutation, {"input": {"name": studio_name}})
    studio_id = data["studioCreate"]["id"]
    log_info(f"Created studio '{studio_name}' (id={studio_id})")
    return studio_id


def find_unprocessed_scenes(connection, processed_tag_id, page=1, per_page=100):
    """Find scenes NOT tagged with the processed tag."""
    query = """
    query FindScenes($filter: FindFilterType, $scene_filter: SceneFilterType) {
        findScenes(filter: $filter, scene_filter: $scene_filter) {
            count
            scenes {
                id
                title
                details
                files { path duration }
                studio { id name }
                tags { id name }
            }
        }
    }
    """
    variables = {
        "filter": {"page": page, "per_page": per_page},
        "scene_filter": {
            "tags": {
                "value": [processed_tag_id],
                "modifier": "EXCLUDES",
                "depth": 0,
            }
        },
    }
    data = graphql_request(connection, query, variables)
    result = data.get("findScenes", {})
    return result.get("scenes", []), result.get("count", 0)


def find_unprocessed_images(connection, processed_tag_id, page=1, per_page=100):
    """Find images NOT tagged with the processed tag."""
    query = """
    query FindImages($filter: FindFilterType, $image_filter: ImageFilterType) {
        findImages(filter: $filter, image_filter: $image_filter) {
            count
            images {
                id
                title
                details
                visual_files { ... on ImageFile { path } }
                studio { id name }
                tags { id name }
            }
        }
    }
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


def get_scene(connection, scene_id):
    """Get a single scene by ID."""
    query = """
    query FindScene($id: ID!) {
        findScene(id: $id) {
            id title details
            files { path duration }
            studio { id name }
            tags { id name }
        }
    }
    """
    data = graphql_request(connection, query, {"id": str(scene_id)})
    return data.get("findScene")


def get_image(connection, image_id):
    """Get a single image by ID."""
    query = """
    query FindImage($id: ID!) {
        findImage(id: $id) {
            id title details
            visual_files { ... on ImageFile { path } }
            studio { id name }
            tags { id name }
        }
    }
    """
    data = graphql_request(connection, query, {"id": str(image_id)})
    return data.get("findImage")


def update_scene(connection, scene_id, tag_ids, studio_id=None, details=None):
    """Update a scene — set tags and optionally studio / details."""
    mutation = """
    mutation SceneUpdate($input: SceneUpdateInput!) {
        sceneUpdate(input: $input) { id }
    }
    """
    inp = {"id": str(scene_id), "tag_ids": tag_ids}
    if studio_id is not None:
        inp["studio_id"] = studio_id
    if details is not None:
        inp["details"] = details
    graphql_request(connection, mutation, {"input": inp})


def update_image(connection, image_id, tag_ids, studio_id=None, details=None):
    """Update an image — set tags and optionally studio / details."""
    mutation = """
    mutation ImageUpdate($input: ImageUpdateInput!) {
        imageUpdate(input: $input) { id }
    }
    """
    inp = {"id": str(image_id), "tag_ids": tag_ids}
    if studio_id is not None:
        inp["studio_id"] = studio_id
    if details is not None:
        inp["details"] = details
    graphql_request(connection, mutation, {"input": inp})


# ---------------------------------------------------------------------------
# Frame extraction & OCR
# ---------------------------------------------------------------------------

def check_tesseract():
    """Verify tesseract is available."""
    try:
        subprocess.run(
            ["tesseract", "--version"],
            capture_output=True, check=True, timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def get_temp_dir():
    """
    Return a base directory for temporary files.

    In the Stash Kubernetes container /cache is an emptyDir mount.
    On macOS tesseract cannot read from /tmp due to sandboxing.
    """
    if os.path.isdir("/cache"):
        base = "/cache/stash-username-extractor"
    elif sys.platform == "darwin":
        base = os.path.join(
            os.path.expanduser("~"), ".cache", "stash-username-extractor",
        )
    else:
        return None  # system default
    os.makedirs(base, exist_ok=True)
    return base


def get_sample_timestamps(duration, num_frames=NUM_FRAMES):
    """Calculate evenly-spaced timestamps to sample from a video."""
    if duration is None or duration <= 0:
        return [0.5]
    if duration < 3:
        return [duration * 0.5]
    n = min(num_frames, max(3, int(duration / 2)))
    step = duration / (n + 1)
    return [round(step * (i + 1), 2) for i in range(n)]


def _run_ffmpeg(args):
    """Run an ffmpeg command, return True on success."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.returncode == 0


def extract_frame(video_path, timestamp, output_path):
    """Extract a single frame from a video."""
    return _run_ffmpeg([
        "-ss", str(timestamp), "-i", video_path,
        "-frames:v", "1", "-q:v", "2", output_path,
    ]) and os.path.exists(output_path)


def extract_frame_negated(video_path, timestamp, output_path):
    """Extract a frame with colours negated."""
    return _run_ffmpeg([
        "-ss", str(timestamp), "-i", video_path,
        "-frames:v", "1", "-vf", "format=gray,negate",
        "-q:v", "2", output_path,
    ]) and os.path.exists(output_path)


def extract_frame_threshold(video_path, timestamp, output_path, threshold=200):
    """Extract a frame with white-text isolation (bright → black on white)."""
    vf = f"geq=lum='if(gt(lum(X,Y),{threshold}),0,255)'"
    return _run_ffmpeg([
        "-ss", str(timestamp), "-i", video_path,
        "-frames:v", "1", "-vf", vf,
        "-pix_fmt", "gray", "-update", "1", output_path,
    ]) and os.path.exists(output_path)


def threshold_image(input_path, output_path, threshold=200):
    """Apply white-text threshold to an existing image."""
    vf = f"geq=lum='if(gt(lum(X,Y),{threshold}),0,255)'"
    return _run_ffmpeg([
        "-i", input_path, "-vf", vf,
        "-pix_fmt", "gray", "-update", "1", "-frames:v", "1", output_path,
    ]) and os.path.exists(output_path)


def ocr_image(image_path, psm=3):
    """Run tesseract OCR on an image."""
    cmd = ["tesseract", image_path, "stdout", "--psm", str(psm)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout
    except subprocess.TimeoutExpired:
        log_warning(f"Tesseract timed out on {image_path}")
    return ""


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_platforms_in_text(text):
    """
    Detect platform indicators in OCR text.

    Returns a Counter mapping platform display names to confidence scores.
    """
    scores = collections.Counter()
    lower = text.lower()

    # --- TikTok ---
    if re.search(r"tik\s*tok", lower):
        scores[PLATFORM_TIKTOK] += 10

    # --- Instagram ---
    # Live badges in various languages
    ig_keywords = [
        "ao vivo", "in diretta", "en vivo", "en direct",
    ]
    for kw in ig_keywords:
        if kw in lower:
            scores[PLATFORM_INSTAGRAM] += 10

    # Weaker Instagram signals (need multiple to be confident)
    ig_weak = ["entrou", "seguidores", "seguindo", "curtir"]
    for kw in ig_weak:
        if kw in lower:
            scores[PLATFORM_INSTAGRAM] += 3

    return scores


# ---------------------------------------------------------------------------
# Username detection
# ---------------------------------------------------------------------------

def is_noise(word):
    """Check if a word is likely UI text rather than a username."""
    w = word.lower().strip("._")
    if not w:
        return True
    if len(w) < MIN_USERNAME_LEN or len(w) > MAX_USERNAME_LEN:
        return True
    if re.match(r"^\d+$", w):
        return True
    if re.match(r"^\d{1,2}:\d{2}", w):
        return True
    if re.match(r"^\d+%$", w):
        return True
    if w in NOISE_WORDS:
        return True
    if word.isupper() and len(word) < 8:
        return True
    return False


def normalize_username(raw):
    """Clean up a raw OCR-extracted username string."""
    u = raw.lstrip("@").strip()
    u = re.sub(r"[.,;:!?]+$", "", u)
    u = u.strip("._")
    return u


def find_usernames_in_text(text):
    """
    Extract potential usernames from OCR text (platform-agnostic).

    Returns list of (username, confidence_score) tuples.
    """
    candidates = []

    # --- @username anywhere (TikTok + Instagram @mentions) ---
    for match in re.finditer(r"@\s{0,2}([\w.]{3,30})", text):
        clean = normalize_username(match.group(1))
        if clean and not is_noise(clean):
            candidates.append((clean, 10))

    # --- @username near "TikTok" keyword — extra confidence ---
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.search(r"tik\s*tok", line, re.IGNORECASE):
            block = "\n".join(lines[i : i + 3])
            for m in re.finditer(r"@\s{0,2}([\w.]{3,30})", block):
                clean = normalize_username(m.group(1))
                if clean and not is_noise(clean):
                    candidates.append((clean, 15))

    # --- Standalone username-like words (Instagram primary poster) ---
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines that are clearly platform chrome
        if re.match(
            r"^(ao vivo|in diretta|live|en vivo|en direct)",
            stripped,
            re.IGNORECASE,
        ):
            continue

        for word in stripped.split():
            word = word.strip(".,;:!?()[]{}\"'")
            if not re.match(r"^[a-zA-Z][\w.]{2,29}$", word):
                continue
            clean = normalize_username(word)
            if is_noise(clean):
                continue
            # Underscores or internal dots — strong username signal
            if "_" in clean or ("." in clean and not clean.endswith(".")):
                candidates.append((clean, 7))
            # Mixed alphanumeric (e.g. miraclegymnast8)
            elif re.search(r"[a-z]\d|\d[a-z]", clean, re.IGNORECASE):
                candidates.append((clean, 5))
            # camelCase-ish
            elif re.search(r"[a-z][A-Z]", clean):
                candidates.append((clean, 5))
            elif len(clean) > 10:
                candidates.append((clean, 2))

    return candidates


# ---------------------------------------------------------------------------
# Core extraction logic
# ---------------------------------------------------------------------------

def _ocr_and_collect(image_path, platform_votes, username_votes, label, psm=3):
    """OCR one image variant, accumulate platform + username votes."""
    text = ocr_image(image_path, psm=psm)
    if not text.strip():
        return
    log_trace(f"{label}: {repr(text[:300])}")

    platform_votes.update(detect_platforms_in_text(text))

    for username, score in find_usernames_in_text(text):
        username_votes[username] += score


def analyze_video(video_path, duration):
    """
    Sample frames, OCR with multiple variants, return aggregated results.

    Returns (platform_votes: Counter, username_votes: Counter)
    """
    timestamps = get_sample_timestamps(duration)
    pv = collections.Counter()
    uv = collections.Counter()

    with tempfile.TemporaryDirectory(prefix="stash_ocr_", dir=get_temp_dir()) as tmpdir:
        for i, ts in enumerate(timestamps):
            tag = f"Frame {i} @ {ts}s"

            # Original
            frame = os.path.join(tmpdir, f"frame_{i}.png")
            if extract_frame(video_path, ts, frame):
                _ocr_and_collect(frame, pv, uv, f"{tag} [original]", psm=3)

            # Negated
            neg = os.path.join(tmpdir, f"frame_{i}_neg.png")
            if extract_frame_negated(video_path, ts, neg):
                _ocr_and_collect(neg, pv, uv, f"{tag} [negated]", psm=3)

            # Threshold variants (most effective for white watermarks)
            for thresh in (200, 180):
                thr = os.path.join(tmpdir, f"frame_{i}_t{thresh}.png")
                if extract_frame_threshold(video_path, ts, thr, threshold=thresh):
                    _ocr_and_collect(
                        thr, pv, uv,
                        f"{tag} [threshold={thresh}]", psm=11,
                    )

    return pv, uv


def analyze_image_file(image_path):
    """OCR an image with multiple variants, return aggregated results."""
    pv = collections.Counter()
    uv = collections.Counter()

    _ocr_and_collect(image_path, pv, uv, "Image [original]", psm=3)

    with tempfile.TemporaryDirectory(prefix="stash_ocr_", dir=get_temp_dir()) as tmpdir:
        for thresh in (200, 180):
            thr = os.path.join(tmpdir, f"thresh_{thresh}.png")
            if threshold_image(image_path, thr, threshold=thresh):
                _ocr_and_collect(thr, pv, uv, f"Image [threshold={thresh}]", psm=11)

    return pv, uv


def pick_winner(votes, threshold=0):
    """Return the top candidate from a Counter if it meets the threshold."""
    if not votes:
        return None
    best, score = votes.most_common(1)[0]
    return best if score >= threshold else None


# ---------------------------------------------------------------------------
# Scene / Image processing
# ---------------------------------------------------------------------------

def build_details(existing_details, username):
    """Prepend the extracted username to the existing details."""
    entry = f"{USERNAME_MARKER} @{username}"
    if not existing_details:
        return entry
    return f"{entry}\n\n{existing_details}"


def _existing_tag_ids(item):
    """Return set of tag IDs already on a scene or image."""
    return {t["id"] for t in (item.get("tags") or [])}


def process_scene(connection, scene, studio_cache, processed_tag_id, dry_run=False):
    """
    Analyze a scene: detect platform, extract username, update Stash.

    Returns a short status string for logging.
    """
    scene_id = scene["id"]
    title = scene.get("title", "")
    details = scene.get("details", "") or ""
    existing_studio = scene.get("studio")
    existing_studio_name = existing_studio.get("name", "") if existing_studio else ""

    files = scene.get("files", [])
    if not files:
        log_debug(f"Scene {scene_id}: no files, skipping")
        return "no_files"

    video_path = files[0].get("path", "")
    duration = files[0].get("duration", 0) or 0

    if not video_path or not os.path.exists(video_path):
        log_warning(f"Scene {scene_id}: file not found: {video_path}")
        return "file_missing"

    log_info(f"Processing scene {scene_id}: {title or os.path.basename(video_path)}")

    # Analyze
    platform_votes, username_votes = analyze_video(video_path, duration)
    platform = pick_winner(platform_votes, threshold=PLATFORM_CONFIDENCE_THRESHOLD)
    username = pick_winner(username_votes, threshold=0)

    if platform_votes:
        log_debug(f"  Platform votes: {dict(platform_votes.most_common(5))}")
    if username_votes:
        log_debug(f"  Username votes: {dict(username_votes.most_common(10))}")

    # Determine what to update
    tag_ids = list(_existing_tag_ids(scene) | {processed_tag_id})

    studio_id = None
    if platform:
        if not existing_studio_name:
            studio_id = studio_cache.setdefault(
                platform, find_or_create_studio(connection, platform),
            )
            log_info(f"  → Platform: {platform}")
        elif existing_studio_name.lower() == platform.lower():
            log_debug(f"  → Platform {platform} matches existing studio")
        else:
            log_info(
                f"  → Detected {platform} but studio already set to "
                f"'{existing_studio_name}', not overriding"
            )
    else:
        log_debug(f"  → No platform detected")

    new_details = None
    if username and USERNAME_MARKER not in details:
        new_details = build_details(details, username)
        log_info(f"  → Username: @{username}")
    elif username:
        log_debug(f"  → Username @{username} (details already has marker)")

    if dry_run:
        parts = []
        if platform:
            parts.append(f"platform={platform}")
        if username:
            parts.append(f"username=@{username}")
        return ", ".join(parts) if parts else "nothing_detected"

    # Apply updates
    update_scene(connection, scene_id, tag_ids, studio_id=studio_id, details=new_details)

    if platform or username:
        return f"platform={platform or '?'}, username=@{username or '?'}"
    return "nothing_detected"


def process_image(connection, image, studio_cache, processed_tag_id, dry_run=False):
    """Analyze an image: detect platform, extract username, update Stash."""
    image_id = image["id"]
    title = image.get("title", "")
    details = image.get("details", "") or ""
    existing_studio = image.get("studio")
    existing_studio_name = existing_studio.get("name", "") if existing_studio else ""

    visual_files = image.get("visual_files", [])
    if not visual_files:
        log_debug(f"Image {image_id}: no files, skipping")
        return "no_files"

    image_path = visual_files[0].get("path", "")
    if not image_path or not os.path.exists(image_path):
        log_warning(f"Image {image_id}: file not found: {image_path}")
        return "file_missing"

    log_info(f"Processing image {image_id}: {title or os.path.basename(image_path)}")

    platform_votes, username_votes = analyze_image_file(image_path)
    platform = pick_winner(platform_votes, threshold=PLATFORM_CONFIDENCE_THRESHOLD)
    username = pick_winner(username_votes, threshold=0)

    if platform_votes:
        log_debug(f"  Platform votes: {dict(platform_votes.most_common(5))}")
    if username_votes:
        log_debug(f"  Username votes: {dict(username_votes.most_common(10))}")

    tag_ids = list(_existing_tag_ids(image) | {processed_tag_id})

    studio_id = None
    if platform:
        if not existing_studio_name:
            studio_id = studio_cache.setdefault(
                platform, find_or_create_studio(connection, platform),
            )
            log_info(f"  → Platform: {platform}")
        elif existing_studio_name.lower() == platform.lower():
            log_debug(f"  → Platform {platform} matches existing studio")
        else:
            log_info(
                f"  → Detected {platform} but studio already set to "
                f"'{existing_studio_name}', not overriding"
            )

    new_details = None
    if username and USERNAME_MARKER not in details:
        new_details = build_details(details, username)
        log_info(f"  → Username: @{username}")

    if dry_run:
        parts = []
        if platform:
            parts.append(f"platform={platform}")
        if username:
            parts.append(f"username=@{username}")
        return ", ".join(parts) if parts else "nothing_detected"

    update_image(connection, image_id, tag_ids, studio_id=studio_id, details=new_details)

    if platform or username:
        return f"platform={platform or '?'}, username=@{username or '?'}"
    return "nothing_detected"


# ---------------------------------------------------------------------------
# Plugin modes
# ---------------------------------------------------------------------------

def mode_batch(connection, dry_run=False):
    """Process all untagged scenes and images."""
    label = "DRY RUN" if dry_run else "EXTRACT"
    log_info(f"=== Username Extractor ({label}) ===")

    processed_tag_id = find_or_create_tag(connection, PROCESSED_TAG)
    studio_cache = {}  # platform name → studio ID (lazy-loaded)

    stats = {
        "detected": 0,
        "not_detected": 0,
        "errors": 0,
        "total": 0,
    }

    # --- Scenes ---
    page = 1
    total_scenes = None
    while True:
        scenes, count = find_unprocessed_scenes(connection, processed_tag_id, page=page)
        if total_scenes is None:
            total_scenes = count
            log_info(f"Scenes to process: {count}")
        if not scenes:
            break

        for scene in scenes:
            stats["total"] += 1
            try:
                result = process_scene(
                    connection, scene, studio_cache, processed_tag_id, dry_run,
                )
                if "nothing_detected" in result or "no_files" in result:
                    stats["not_detected"] += 1
                elif "file_missing" in result:
                    stats["errors"] += 1
                else:
                    stats["detected"] += 1
            except Exception as exc:
                log_error(f"Error processing scene {scene['id']}: {exc}")
                stats["errors"] += 1

        # Always re-fetch page 1 because processed items drop out of the query
        if not dry_run:
            page = 1
        else:
            page += 1

    # --- Images ---
    page = 1
    total_images = None
    while True:
        images, count = find_unprocessed_images(connection, processed_tag_id, page=page)
        if total_images is None:
            total_images = count
            log_info(f"Images to process: {count}")
        if not images:
            break

        for image in images:
            stats["total"] += 1
            try:
                result = process_image(
                    connection, image, studio_cache, processed_tag_id, dry_run,
                )
                if "nothing_detected" in result or "no_files" in result:
                    stats["not_detected"] += 1
                elif "file_missing" in result:
                    stats["errors"] += 1
                else:
                    stats["detected"] += 1
            except Exception as exc:
                log_error(f"Error processing image {image['id']}: {exc}")
                stats["errors"] += 1

        if not dry_run:
            page = 1
        else:
            page += 1

    msg = (
        f"Done: {stats['detected']} detected, "
        f"{stats['not_detected']} no match, "
        f"{stats['errors']} errors "
        f"({stats['total']} total)"
    )
    log_info(msg)
    return msg


def mode_hook(connection, hook_context):
    """Handle a Scene.Create.Post or Image.Create.Post hook."""
    hook_type = hook_context.get("type", "")
    hook_id = hook_context.get("id")

    if not hook_id:
        log_debug(f"Hook {hook_type}: no ID in context")
        return "No ID"

    log_info(f"Hook: {hook_type} → ID {hook_id}")

    processed_tag_id = find_or_create_tag(connection, PROCESSED_TAG)
    studio_cache = {}

    if "Scene" in hook_type:
        scene = get_scene(connection, hook_id)
        if scene:
            result = process_scene(
                connection, scene, studio_cache, processed_tag_id,
            )
            return f"Scene {hook_id}: {result}"
    elif "Image" in hook_type:
        image = get_image(connection, hook_id)
        if image:
            result = process_image(
                connection, image, studio_cache, processed_tag_id,
            )
            return f"Image {hook_id}: {result}"

    return "Nothing to process"


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
        if not check_tesseract():
            msg = (
                "Tesseract OCR not found. Install with:\n"
                "  macOS:  brew install tesseract\n"
                "  Alpine: apk add tesseract-ocr tesseract-ocr-data-eng\n"
                "  Debian: apt-get install tesseract-ocr"
            )
            log_error(msg)
            print(json.dumps({"error": msg}))
            sys.exit(1)

        log_info("Username Extractor starting")

        if hook_context:
            result = mode_hook(connection, hook_context)
        elif mode == "scan":
            result = mode_batch(connection, dry_run=True)
        elif mode == "extract":
            result = mode_batch(connection, dry_run=False)
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
