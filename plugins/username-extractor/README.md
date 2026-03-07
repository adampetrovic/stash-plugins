# Username Extractor

Scans all scenes and images using OCR to:

1. **Detect the source platform** (TikTok / Instagram) from watermarks
2. **Set the studio** accordingly (only when confidently detected)
3. **Extract the primary username** and write it to the details field
4. **Tag processed items** with `auto:ocr` so they aren't re-scanned

## How it works

1. Finds all scenes/images **not** tagged with `auto:ocr`
2. For each video, extracts multiple frames spread across the duration
3. Runs Tesseract OCR on each frame with three preprocessing variants:
   - **Original** — good for dark text and high-contrast watermarks
   - **Negated** — inverts colours to help with white text
   - **White-text threshold** — isolates bright pixels as black on white; most effective for semi-transparent watermarks
4. **Detects platform** from keywords in the OCR text:
   - TikTok: "TikTok" / "Tik Tok"
   - Instagram: "AO VIVO", "In diretta", "En vivo", "En direct", etc.
5. **Extracts usernames** using pattern matching:
   - `@username` patterns (TikTok watermarks, Instagram mentions)
   - Standalone username-like words (Instagram Live/Reels primary poster)
6. Votes across all frames × variants — highest-scoring wins
7. Updates the scene/image:
   - Sets studio to "TikTok" or "Instagram" (created if needed, only if no studio already set)
   - Prepends `Extracted Username: @username` to details
   - Adds `auto:ocr` tag

Handles screen recordings, camera recordings of devices, moving watermarks (TikTok), and multiple languages.

## Requirements

- **ffmpeg** — included with Stash
- **Tesseract OCR**:
  ```bash
  # Alpine (Stash container)
  apk add tesseract-ocr tesseract-ocr-data-eng

  # macOS
  brew install tesseract

  # Debian / Ubuntu
  sudo apt-get install tesseract-ocr
  ```

## Tasks

| Task | Description |
|------|-------------|
| **Extract Usernames** | Process all unscanned scenes and images. Detects platform, sets studio, extracts usernames. |
| **Dry Run** | Shows what would be detected without modifying anything. |

## Hooks

Automatically processes new items on creation:

- `Scene.Create.Post`
- `Image.Create.Post`

## Details format

```
Extracted Username: @jazmynmajors

(any existing details preserved below)
```

## Re-processing

To re-scan items, remove the `auto:ocr` tag (via Stash UI or bulk edit) and run the task again.
