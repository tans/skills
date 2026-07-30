#!/usr/bin/env python3
"""Generate a project image through the fixed gpt-image-2 gateway.

Call order:
1. POST /v1/images/generations
2. If that route still fails, POST /v1/responses with image_generation

Both routes retry transient failures with exponential backoff. The API key is read
from ~/.codex/auth.json and is never printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

OPENAI_BASE_URL = "https://token.minapp.xin/v1"
MODEL = "gpt-image-2"
DEFAULT_AUTH_FILE = Path.home() / ".codex" / "auth.json"
RETRYABLE_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class RequestFailure(RuntimeError):
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def load_api_key(auth_file: Path) -> str:
    try:
        payload = json.loads(auth_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Codex auth file not found: {auth_file}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read Codex auth file: {auth_file}") from exc

    key = payload.get("OPENAI_API_KEY") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError(f"OPENAI_API_KEY is missing from: {auth_file}")
    return key.strip()


def post_json(endpoint: str, api_key: str, body: dict[str, Any], timeout: int) -> Any:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        retryable = exc.code in RETRYABLE_HTTP_CODES or 500 <= exc.code <= 599
        raise RequestFailure(
            f"HTTP {exc.code}: {details[:800]}", retryable=retryable
        ) from exc
    except urllib.error.URLError as exc:
        raise RequestFailure(f"Connection error: {exc.reason}", retryable=True) from exc
    except TimeoutError as exc:
        raise RequestFailure(f"Request timed out: {exc}", retryable=True) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RequestFailure(
            f"Invalid JSON response: {raw[:300]}", retryable=True
        ) from exc


def run_with_retries(
    route_name: str,
    operation: Callable[[], bytes],
    retries: int,
    base_delay: float,
) -> bytes:
    total_attempts = retries + 1
    last_error: RequestFailure | None = None

    for attempt in range(1, total_attempts + 1):
        try:
            return operation()
        except RequestFailure as exc:
            last_error = exc
            print(
                f"[{route_name}] attempt {attempt}/{total_attempts} failed: {exc}",
                file=sys.stderr,
            )
            if not exc.retryable or attempt >= total_attempts:
                break

            jitter = random.uniform(0, min(1.0, base_delay / 2))
            delay = base_delay * (2 ** (attempt - 1)) + jitter
            print(f"[{route_name}] retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)

    if last_error is None:
        raise RequestFailure(f"{route_name} failed without an error response")
    raise last_error


def decode_base64_image(encoded: str, source: str) -> bytes:
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise RequestFailure(f"{source} returned invalid base64 image data") from exc


def download_image(image_url: str, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(image_url, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RequestFailure(
            f"Generated image download returned HTTP {exc.code}",
            retryable=exc.code in RETRYABLE_HTTP_CODES or 500 <= exc.code <= 599,
        ) from exc
    except urllib.error.URLError as exc:
        raise RequestFailure(
            f"Cannot download generated image: {exc.reason}", retryable=True
        ) from exc
    except TimeoutError as exc:
        raise RequestFailure(
            f"Generated image download timed out: {exc}", retryable=True
        ) from exc


def extract_image_item(item: Any, timeout: int, source: str) -> bytes | None:
    if not isinstance(item, dict):
        return None

    for key in ("b64_json", "result", "image_base64", "base64"):
        encoded = item.get(key)
        if isinstance(encoded, str) and encoded:
            return decode_base64_image(encoded, source)

    image_url = item.get("url") or item.get("image_url")
    if isinstance(image_url, str) and image_url:
        if image_url.startswith("data:image/") and "," in image_url:
            return decode_base64_image(image_url.split(",", 1)[1], source)
        return download_image(image_url, timeout)

    content = item.get("content")
    if isinstance(content, list):
        for content_item in content:
            image = extract_image_item(content_item, timeout, source)
            if image is not None:
                return image

    return None


def extract_images_api_bytes(result: Any, timeout: int) -> bytes:
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list):
            for item in data:
                image = extract_image_item(item, timeout, "Images API")
                if image is not None:
                    return image

        image = extract_image_item(result, timeout, "Images API")
        if image is not None:
            return image

    raise RequestFailure("Images API response does not contain image data", retryable=True)


def extract_responses_api_bytes(result: Any, timeout: int) -> bytes:
    if not isinstance(result, dict):
        raise RequestFailure("Responses API returned an invalid response", retryable=True)

    output = result.get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and item.get("type") == "image_generation_call":
                image = extract_image_item(item, timeout, "Responses API")
                if image is not None:
                    return image

        for item in output:
            image = extract_image_item(item, timeout, "Responses API")
            if image is not None:
                return image

    image = extract_image_item(result, timeout, "Responses API")
    if image is not None:
        return image

    error = result.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            raise RequestFailure(f"Responses API error: {message}", retryable=True)

    raise RequestFailure(
        "Responses API response does not contain an image_generation_call result",
        retryable=True,
    )


def request_via_images_api(
    api_key: str,
    prompt: str,
    size: str,
    quality: str,
    timeout: int,
) -> bytes:
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/images/generations"
    body = {
        "model": MODEL,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
        "output_format": "png",
    }
    result = post_json(endpoint, api_key, body, timeout)
    return extract_images_api_bytes(result, timeout)


def request_via_responses_api(
    api_key: str,
    prompt: str,
    size: str,
    quality: str,
    timeout: int,
) -> bytes:
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/responses"
    body = {
        "model": MODEL,
        "input": prompt,
        "tools": [
            {
                "type": "image_generation",
                "model": MODEL,
                "size": size,
                "quality": quality,
                "output_format": "png",
            }
        ],
        "tool_choice": {"type": "image_generation"},
    }
    result = post_json(endpoint, api_key, body, timeout)
    return extract_responses_api_bytes(result, timeout)


def generate_image(
    api_key: str,
    prompt: str,
    size: str,
    quality: str,
    timeout: int,
    retries: int,
    retry_delay: float,
) -> tuple[bytes, str]:
    try:
        image = run_with_retries(
            "Images API",
            lambda: request_via_images_api(api_key, prompt, size, quality, timeout),
            retries,
            retry_delay,
        )
        return image, "images"
    except RequestFailure as images_error:
        print(
            f"[fallback] Images API unavailable; switching to Responses API: {images_error}",
            file=sys.stderr,
        )

    image = run_with_retries(
        "Responses API",
        lambda: request_via_responses_api(api_key, prompt, size, quality, timeout),
        retries,
        retry_delay,
    )
    return image, "responses"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate an image with gpt-image-2. Uses Images API first and "
            "Responses API as fallback."
        )
    )
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Image generation prompt")
    prompt_group.add_argument("--prompt-file", type=Path, help="UTF-8 prompt file")
    parser.add_argument("--output", type=Path, required=True, help="Output image path")
    parser.add_argument(
        "--size",
        default="1024x1024",
        choices=("1024x1024", "1024x1536", "1536x1024", "auto"),
    )
    parser.add_argument(
        "--quality",
        default="high",
        choices=("low", "medium", "high", "auto"),
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retries per API route after the first attempt; default: 2",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=2.0,
        help="Initial exponential backoff delay in seconds; default: 2",
    )
    parser.add_argument("--auth-file", type=Path, default=DEFAULT_AUTH_FILE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.retries < 0:
            raise RuntimeError("--retries cannot be negative")
        if args.retry_delay < 0:
            raise RuntimeError("--retry-delay cannot be negative")

        prompt = args.prompt
        if args.prompt_file is not None:
            prompt = args.prompt_file.read_text(encoding="utf-8").strip()
        if not prompt or not prompt.strip():
            raise RuntimeError("Prompt cannot be empty")

        api_key = load_api_key(args.auth_file.expanduser())
        image_bytes, route = generate_image(
            api_key=api_key,
            prompt=prompt.strip(),
            size=args.size,
            quality=args.quality,
            timeout=args.timeout,
            retries=args.retries,
            retry_delay=args.retry_delay,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(image_bytes)
        print(json.dumps({"output": str(args.output.resolve()), "route": route}, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, RequestFailure) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
