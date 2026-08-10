from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("image_api.py")
SPEC = importlib.util.spec_from_file_location("image_api", MODULE_PATH)
assert SPEC and SPEC.loader
image_api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = image_api
SPEC.loader.exec_module(image_api)

PNG = b"\x89PNG\r\n\x1a\nmock-image"


class MockServer:
    def __init__(self, handler: type[BaseHTTPRequestHandler]):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def config(base_url: str) -> dict[str, object]:
    return image_api.validate_config({
        **image_api.DEFAULT_CONFIG,
        "base_url": base_url,
        "api_key": "test-key",
        "retry_base_delay_seconds": 0,
    })


class ImageApiTests(unittest.TestCase):
    def test_first_run_form_saves_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            internal = Path(directory) / ".internal"
            reference = Path(directory) / "reference.png"
            reference.write_bytes(PNG)
            submitted = threading.Event()
            persisted: dict[str, object] = {}

            def open_form(url: str) -> bool:
                def submit() -> None:
                    page = urllib.request.urlopen(url).read().decode("utf-8")
                    self.assertIn("API Key", page)
                    self.assertIn("https://token.minapp.xin", page)
                    self.assertIn("gpt-image-2", page)
                    self.assertNotIn("连接你的图片 API", page)
                    self.assertNotIn("配置仅保存在本机技能目录", page)
                    self.assertNotIn("页面由本机 127.0.0.1 临时服务提供", page)
                    data = urllib.parse.urlencode({
                        "base_url": "https://images.example.test",
                        "api_key": "secret-test-key",
                        "model": "image-model",
                        "generation_path": "/v1/images/generations",
                        "edit_path": "/v1/images/edits",
                        "auth_header": "Authorization",
                        "auth_prefix": "Bearer",
                        "timeout_seconds": "90",
                        "retries": "3",
                    }).encode()
                    chat = urllib.request.urlopen(url, data=data).read().decode("utf-8")
                    self.assertIn("图片测试", chat)
                    self.assertIn('type="file"', chat)
                    self.assertIn("multiple", chat)
                    self.assertIn('class="compose-row"', chat)
                    self.assertIn('<svg viewBox="0 0 24 24"', chat)
                    test_request = urllib.request.Request(
                        urllib.parse.urljoin(url, "/api/test"),
                        data=json.dumps({"prompt": "test chat image"}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    test_response = json.loads(urllib.request.urlopen(test_request).read())
                    self.assertTrue(test_response["ok"])
                    self.assertTrue(test_response["images"][0].startswith("data:image/png;base64,"))
                    edit_body, edit_content_type = image_api.encode_multipart({"prompt": "edit chat image"}, [("image", reference)])
                    edit_request = urllib.request.Request(
                        urllib.parse.urljoin(url, "/api/test"),
                        data=edit_body,
                        headers={"Content-Type": edit_content_type},
                        method="POST",
                    )
                    edit_response = json.loads(urllib.request.urlopen(edit_request).read())
                    self.assertTrue(edit_response["ok"])
                    urllib.request.urlopen(urllib.parse.urljoin(url, "/api/finish"), data=b"").read()
                    submitted.set()

                threading.Thread(target=submit, daemon=True).start()
                return True

            with (
                mock.patch.object(image_api, "INTERNAL_DIR", internal),
                mock.patch.object(image_api, "CONFIG_PATH", internal / "config.json"),
                mock.patch.object(image_api, "OUTPUT_DIR", internal / "outputs"),
                mock.patch.object(image_api, "CONFIG_PUBLIC_HOST", "127.0.0.1"),
                mock.patch.object(image_api, "CONFIG_PORT", 0),
                mock.patch.object(image_api.webbrowser, "open", side_effect=open_form),
                mock.patch.object(image_api, "save_config_to_environment", side_effect=lambda value: persisted.update(value)),
                mock.patch.object(image_api, "environment_config_values", return_value={}),
                mock.patch.object(image_api, "generate_request", return_value=image_api.HttpResult(json.dumps({"data": [{"b64_json": base64.b64encode(PNG).decode()}]}).encode(), "application/json")),
                mock.patch.object(image_api, "edit_request", return_value=image_api.HttpResult(json.dumps({"data": [{"b64_json": base64.b64encode(PNG).decode()}]}).encode(), "application/json")) as edit_request_mock,
            ):
                configured = image_api.configure_in_browser()
                self.assertTrue(submitted.wait(1))
                self.assertEqual(configured["base_url"], "https://images.example.test")
                self.assertEqual(configured["api_key"], "secret-test-key")
                self.assertEqual(persisted["model"], "image-model")
                self.assertFalse((internal / "config.json").exists())
                self.assertTrue(any((internal / "outputs").iterdir()))
                edit_request_mock.assert_called_once()

    def test_configuration_uses_fixed_local_address(self) -> None:
        self.assertEqual(image_api.CONFIG_BIND_HOST, "127.0.0.1")
        self.assertEqual(image_api.CONFIG_PUBLIC_HOST, "localhost")
        self.assertEqual(image_api.CONFIG_PORT, 50000)
        self.assertEqual(image_api.CONFIG_PAGE_PATH, "/")

    def test_load_config_reads_environment_variables(self) -> None:
        values = {
            "UNIVERSAL_IMAGE_API_BASE_URL": "https://environment.example.test",
            "UNIVERSAL_IMAGE_API_KEY": "environment-key",
            "UNIVERSAL_IMAGE_API_MODEL": "environment-model",
        }
        with (
            mock.patch.dict(image_api.os.environ, values, clear=True),
            mock.patch.object(image_api, "configure_in_browser") as configure,
        ):
            loaded = image_api.load_config()
        configure.assert_not_called()
        self.assertEqual(loaded["base_url"], "https://environment.example.test")
        self.assertEqual(loaded["api_key"], "environment-key")
        self.assertEqual(loaded["model"], "environment-model")

    def test_generation_requests_base64_and_saves_bytes(self) -> None:
        seen: dict[str, object] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                seen.update(json.loads(self.rfile.read(length)))
                body = json.dumps({"data": [{"b64_json": base64.b64encode(PNG).decode()}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        with MockServer(Handler) as url, tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(prompt="test", count=1, model=None, size="512x512", extra_json=None)
            result = image_api.generate_request(args, config(url))
            images = image_api.extract_images(result, config(url))
            paths = image_api.save_images(images, Path(directory), "result")
            self.assertEqual(seen["response_format"], "b64_json")
            self.assertEqual(paths[0].read_bytes(), PNG)

    def test_retries_three_times_before_success(self) -> None:
        calls = 0

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                nonlocal calls
                calls += 1
                if calls <= 3:
                    self.send_response(500)
                    self.end_headers()
                    return
                body = json.dumps({"data": [{"b64_json": base64.b64encode(PNG).decode()}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        with MockServer(Handler) as url:
            args = argparse.Namespace(prompt="test", count=1, model=None, size=None, extra_json=None)
            image_api.generate_request(args, config(url))
        self.assertEqual(calls, 4)

    def test_url_response_is_downloaded_to_bytes(self) -> None:
        base = ""

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.dumps({"data": [{"url": f"{base}/result.png"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(PNG)))
                self.end_headers()
                self.wfile.write(PNG)

            def log_message(self, *_args: object) -> None:
                pass

        with MockServer(Handler) as url:
            base = url
            args = argparse.Namespace(prompt="test", count=1, model=None, size=None, extra_json=None)
            result = image_api.generate_request(args, config(url))
            images = image_api.extract_images(result, config(url))
        self.assertEqual(images[0].body, PNG)

    def test_edit_uses_multipart_with_image_and_base64_format(self) -> None:
        received = b""
        content_type = ""

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                nonlocal received, content_type
                content_type = self.headers["Content-Type"]
                received = self.rfile.read(int(self.headers["Content-Length"]))
                body = json.dumps({"data": [{"b64_json": base64.b64encode(PNG).decode()}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        with MockServer(Handler) as url, tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(PNG)
            args = argparse.Namespace(image=[str(source)], mask=None, prompt="edit", count=1, model=None, size=None, extra_json=None)
            image_api.edit_request(args, config(url))
        self.assertIn("multipart/form-data", content_type)
        self.assertIn(b'name="image"', received)
        self.assertIn(b'name="response_format"', received)
        self.assertIn(b"b64_json", received)
        self.assertIn(PNG, received)


if __name__ == "__main__":
    unittest.main()
