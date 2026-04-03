# Plugin Stdin/Stdout Protocol Contract

Stash communicates with raw-interface plugins via JSON over stdin/stdout.

## Input (stdin)

### Task Invocation
```json
{
  "server_connection": {
    "Scheme": "http",
    "Port": 9999,
    "SessionCookie": { "Name": "session", "Value": "<cookie>" }
  },
  "args": {
    "mode": "scan | dry_run | resolve | resolve_dry_run | cleanup"
  }
}
```

### Hook Invocation
```json
{
  "server_connection": { ... },
  "args": {
    "hookContext": {
      "type": "Image.Create.Post",
      "id": "12345"
    }
  }
}
```

## Output (stdout)

### Success
```json
{
  "output": "Scan complete: 150 images fingerprinted, 12 duplicate groups found"
}
```

### Error
```json
{
  "error": "ImageMagick not found. Install with: apk add imagemagick"
}
```

## Logging (stderr)

Uses Stash's SOH protocol for structured log levels:

| Level | Prefix | Example |
|-------|--------|---------|
| Trace | `\x01t\x02` | Low-level hash computation details |
| Debug | `\x01d\x02` | Individual image processing steps |
| Info | `\x01i\x02` | Progress updates, group discovery |
| Warning | `\x01w\x02` | Corrupt files skipped, orphaned data |
| Error | `\x01e\x02` | Fatal errors, missing dependencies |
| Progress | `\x01p\x02` | Float 0.0–1.0 for task progress bar |

## Task Modes

| Mode | Modifies Data | Description |
|------|--------------|-------------|
| `scan` | Yes (tags, SQLite) | Fingerprint unprocessed images, identify groups, apply tags |
| `dry_run` | No | Report what scan would find |
| `resolve` | Yes (images deleted, metadata merged) | Keep best, merge metadata, delete rest |
| `resolve_dry_run` | No | Report what resolve would do |
| `cleanup` | Yes (SQLite, tags) | Remove orphaned fingerprints and empty groups |
