---
name: image
description: Generate new images and edit one or more existing images through a configurable OpenAI-compatible HTTP API, always downloading the image bytes to stable local files. Use when Codex needs general text-to-image generation, image-to-image editing, reference-image transformation, inpainting with a mask, or local image assets from a user-supplied API address and API key.
---

# Image

Run the bundled client for every image request. It configures itself through a local browser form on first use, reads connection settings from environment variables, requests Base64 image data, retries transient failures three times, and saves successful files under `.internal/outputs/` beside this skill.

## Generate

```powershell
python scripts/image_api.py generate --prompt "A clean pixel-art forest shrine" --size 1024x1024
```

Add provider-specific fields without changing the script:

```powershell
python scripts/image_api.py generate --prompt "A product photo" --extra-json '{"quality":"high","background":"transparent"}'
```

## Edit

Inspect every input image before editing. Then pass one or more local paths:

```powershell
python scripts/image_api.py edit --image C:\path\input.png --prompt "Remove the background"
python scripts/image_api.py edit --image C:\path\front.png --image C:\path\style.png --prompt "Use the second image as the visual style"
python scripts/image_api.py edit --image C:\path\input.png --mask C:\path\mask.png --prompt "Replace the masked area with flowers"
```

## Configure

When the API Key environment variable is absent, any generation or edit command starts the fixed local configuration page at `http://localhost:50000/` automatically. The server binds only to `127.0.0.1`, so configuration does not travel over the public internet. To change settings later, run:

```powershell
python scripts/image_api.py configure
```

After saving, the browser switches to a local chat interface for real generation and editing tests. Allow multiple reference images to be uploaded, previewed, removed, and sent through the configured editing route. Keep the configuration service running until the user finishes testing. The form writes persistent Windows user environment variables and updates the running process. The client also reads the persistent Windows user environment store directly when its process environment is stale. Other applications may need to restart before they inherit changed values. The defaults are `https://token.minapp.xin` and `gpt-image-2`.

## Environment Variables

- `UNIVERSAL_IMAGE_API_BASE_URL`: API base address.
- `UNIVERSAL_IMAGE_API_KEY`: API Key. Its absence triggers browser configuration.
- `UNIVERSAL_IMAGE_API_MODEL`: Default image model.
- `UNIVERSAL_IMAGE_API_GENERATION_PATH`: Text-to-image route.
- `UNIVERSAL_IMAGE_API_EDIT_PATH`: Image editing route.
- `UNIVERSAL_IMAGE_API_AUTH_HEADER`: Authentication header name.
- `UNIVERSAL_IMAGE_API_AUTH_PREFIX`: Prefix before the API Key, normally `Bearer`.
- `UNIVERSAL_IMAGE_API_TIMEOUT_SECONDS`: Request timeout in seconds.
- `UNIVERSAL_IMAGE_API_RETRIES`: Retry count after the initial request; values below `3` are raised to `3`.

## Output Contract

Read the JSON printed by the command and return every path in `files`. Never return a provider URL as the final result. The client requests `response_format=b64_json`; if a provider ignores that and returns a URL, the client downloads it immediately and still returns only local files.

Use `--output-name` for a stable filename prefix and `--output-dir` only when the user explicitly requests another destination. By default all successful images remain discoverable under this skill's `.internal/outputs/` directory.
