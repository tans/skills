#!/usr/bin/env python3
"""Generate and edit images through a configurable OpenAI-compatible API."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
INTERNAL_DIR = SKILL_DIR / ".internal"
CONFIG_PATH = INTERNAL_DIR / "config.json"
OUTPUT_DIR = INTERNAL_DIR / "outputs"
CONFIG_BIND_HOST = "127.0.0.1"
CONFIG_PUBLIC_HOST = "localhost"
CONFIG_PORT = 50000
CONFIG_PAGE_PATH = "/"
STARTUP_VALUE_NAME = "CodexImageSkill"

DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "https://token.minapp.xin",
    "api_key": "",
    "model": "gpt-image-2",
    "generation_path": "/v1/images/generations",
    "edit_path": "/v1/images/edits",
    "auth_header": "Authorization",
    "auth_prefix": "Bearer",
    "timeout_seconds": 180,
    "retries": 3,
    "retry_base_delay_seconds": 1.0,
}

ENVIRONMENT_KEYS = {
    "base_url": "UNIVERSAL_IMAGE_API_BASE_URL",
    "api_key": "UNIVERSAL_IMAGE_API_KEY",
    "model": "UNIVERSAL_IMAGE_API_MODEL",
    "generation_path": "UNIVERSAL_IMAGE_API_GENERATION_PATH",
    "edit_path": "UNIVERSAL_IMAGE_API_EDIT_PATH",
    "auth_header": "UNIVERSAL_IMAGE_API_AUTH_HEADER",
    "auth_prefix": "UNIVERSAL_IMAGE_API_AUTH_PREFIX",
    "timeout_seconds": "UNIVERSAL_IMAGE_API_TIMEOUT_SECONDS",
    "retries": "UNIVERSAL_IMAGE_API_RETRIES",
}


class ImageApiError(RuntimeError):
    pass


@dataclass
class HttpResult:
    body: bytes
    content_type: str


def ensure_internal_dirs() -> None:
    INTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def environment_config_values() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, environment_key in ENVIRONMENT_KEYS.items():
        value = os.environ.get(environment_key)
        if value is not None:
            values[key] = value
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment:
                for key, environment_key in ENVIRONMENT_KEYS.items():
                    if key in values:
                        continue
                    try:
                        values[key] = winreg.QueryValueEx(environment, environment_key)[0]
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass
    return values


def save_config_to_environment(config: dict[str, Any]) -> None:
    values = {environment_key: str(config[key]) for key, environment_key in ENVIRONMENT_KEYS.items()}
    os.environ.update(values)
    if os.name != "nt":
        raise ImageApiError("Automatic persistent environment variables are currently supported on Windows only.")

    import ctypes
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as environment:
        for key, value in values.items():
            winreg.SetValueEx(environment, key, 0, winreg.REG_SZ, value)
    try:
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
    except (AttributeError, OSError, TypeError):
        pass


def validate_config(values: dict[str, Any]) -> dict[str, Any]:
    config = {**DEFAULT_CONFIG, **values}
    parsed = urllib.parse.urlparse(str(config["base_url"]).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImageApiError("API address must be a complete http:// or https:// URL.")
    if not str(config["api_key"]).strip():
        raise ImageApiError("API key is required.")
    for key in ("generation_path", "edit_path"):
        value = str(config[key]).strip()
        route = urllib.parse.urlparse(value)
        if not value.startswith("/") and not (route.scheme in {"http", "https"} and route.netloc):
            raise ImageApiError(f"{key} must be an absolute URL or start with '/'.")
        config[key] = value
    config["base_url"] = str(config["base_url"]).strip().rstrip("/")
    config["api_key"] = str(config["api_key"]).strip()
    config["model"] = str(config["model"]).strip()
    config["auth_header"] = str(config["auth_header"]).strip() or "Authorization"
    config["auth_prefix"] = str(config["auth_prefix"]).strip()
    config["timeout_seconds"] = max(1, int(config["timeout_seconds"]))
    config["retries"] = max(3, int(config["retries"]))
    config["retry_base_delay_seconds"] = max(0.0, float(config["retry_base_delay_seconds"]))
    return config


def load_config() -> dict[str, Any]:
    environment_values = environment_config_values()
    if environment_values.get("api_key"):
        return validate_config(environment_values)
    ensure_configuration_service(open_browser=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        environment_values = environment_config_values()
        if environment_values.get("api_key"):
            try:
                return validate_config(environment_values)
            except (ImageApiError, ValueError) as error:
                raise ImageApiError(f"Environment configuration is invalid: {error}") from error
        if not configuration_service_is_running():
            ensure_configuration_service(open_browser=False)
        time.sleep(0.5)
    raise ImageApiError("Timed out waiting for image API configuration at http://localhost:50000/.")


def config_page(current: dict[str, Any], error: str = "") -> str:
    def esc(value: Any) -> str:
        import html

        return html.escape(str(value), quote=True)

    error_html = f'<div class="error">{esc(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>配置通用生图 API</title><style>
:root{{--ink:#172033;--muted:#64748b;--line:#d9e1ec;--accent:#0f766e;--paper:#fff}}
*{{box-sizing:border-box}} body{{margin:0;min-height:100vh;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:linear-gradient(135deg,#f7fafc,#e8f3f0);display:grid;place-items:center;padding:24px}}
main{{width:min(720px,100%);background:var(--paper);border:1px solid rgba(15,118,110,.18);border-radius:16px;box-shadow:0 24px 70px rgba(23,32,51,.14);overflow:hidden}}
form{{padding:34px}} label{{display:block;margin:0 0 16px;font-weight:650}} input{{display:block;width:100%;margin-top:7px;border:1px solid var(--line);border-radius:8px;padding:11px 12px;font:inherit;color:var(--ink);background:#fff;outline:none}} input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(15,118,110,.12)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}} details{{margin:4px 0 22px;border-top:1px solid var(--line);padding-top:15px}} summary{{cursor:pointer;font-weight:700;color:var(--accent)}} .advanced{{padding-top:17px}}
button{{width:100%;border:0;border-radius:8px;padding:13px 18px;background:var(--accent);color:white;font:700 16px inherit;cursor:pointer}} button:hover{{background:#0b615b}} .error{{background:#fff1f2;color:#be123c;padding:11px 12px;border-radius:8px;margin-bottom:16px}}
@media(max-width:600px){{form{{padding-left:22px;padding-right:22px}}.grid{{grid-template-columns:1fr}}}}
</style></head><body><main><form method="post">{error_html}
<label>API 地址<input name="base_url" type="url" required value="{esc(current.get('base_url',''))}"></label>
<label>API Key<input name="api_key" type="password" required autocomplete="off" placeholder="sk-..." value="{esc(current.get('api_key',''))}"></label>
<label>默认模型<input name="model" value="{esc(current.get('model',''))}"></label>
<details><summary>高级连接设置</summary><div class="advanced">
<div class="grid"><label>生成接口<input name="generation_path" value="{esc(current.get('generation_path'))}"></label><label>编辑接口<input name="edit_path" value="{esc(current.get('edit_path'))}"></label></div>
<div class="grid"><label>鉴权 Header<input name="auth_header" value="{esc(current.get('auth_header'))}"></label><label>Key 前缀<input name="auth_prefix" value="{esc(current.get('auth_prefix'))}"></label></div>
<div class="grid"><label>超时（秒）<input name="timeout_seconds" type="number" min="1" value="{esc(current.get('timeout_seconds'))}"></label><label>失败重试次数<input name="retries" type="number" min="3" value="{esc(current.get('retries'))}"></label></div>
</div></details><button type="submit">保存</button></form></main></body></html>"""


def chat_page() -> str:
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>图片创作</title><style>
:root{--ink:#172033;--muted:#64748b;--line:#d9e1ec;--accent:#0f766e;--paper:#fff;--wash:#f3f7f6;--danger:#be123c}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:#edf3f2;display:grid;place-items:center;padding:18px}
main{width:min(860px,100%);height:min(780px,calc(100vh - 36px));background:var(--paper);border:1px solid #cad8d5;box-shadow:0 20px 60px rgba(23,32,51,.14);display:grid;grid-template-rows:56px 1fr auto;overflow:hidden;border-radius:8px}
header{display:flex;align-items:center;justify-content:space-between;padding:0 18px;border-bottom:1px solid var(--line)}h1{font-size:16px;margin:0;letter-spacing:0}button{font:inherit;cursor:pointer}
#settings{border:1px solid var(--line);background:white;color:var(--muted);padding:7px 12px;border-radius:6px;text-decoration:none}#settings:hover{border-color:#a7c7c1;color:var(--accent)}#messages{overflow:auto;padding:22px;display:flex;flex-direction:column;gap:16px;background:var(--wash)}
.message{max-width:min(680px,88%);padding:11px 13px;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere}.assistant{align-self:flex-start;background:white;border:1px solid var(--line)}.user{align-self:flex-end;background:var(--accent);color:white}.error{color:var(--danger)}
.result{display:grid;gap:10px}.result img{display:block;max-width:100%;max-height:480px;border-radius:6px;border:1px solid var(--line)}.path{font:12px ui-monospace,Consolas,monospace;color:var(--muted);word-break:break-all}
form{padding:12px 14px 14px;border-top:1px solid var(--line);background:white}.compose-row{display:flex;align-items:center;gap:6px;min-height:54px;border:1px solid var(--line);border-radius:12px;padding:6px;background:white;transition:border-color .15s,box-shadow .15s}.compose-row:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px rgba(15,118,110,.1)}.attach{width:38px;height:38px;flex:0 0 38px;border:1px solid #dce5e3;border-radius:50%;display:grid;place-items:center;cursor:pointer;background:#f7faf9;color:#5f6f6c;transition:background .15s,border-color .15s,color .15s,transform .15s}.attach:hover{background:#e8f3f0;border-color:#a7c7c1;color:var(--accent)}.attach:active{transform:scale(.96)}.attach svg{width:19px;height:19px;stroke:currentColor}textarea{flex:1;height:40px;min-height:40px;max-height:120px;resize:none;overflow-y:auto;border:0;padding:9px 7px;line-height:22px;font:inherit;outline:none;background:transparent}#send{height:40px;flex:0 0 auto;border:0;background:var(--accent);color:white;border-radius:9px;padding:0 18px;font-weight:700}button:disabled{opacity:.55;cursor:wait}#previews{display:none;gap:10px;flex-wrap:wrap;padding:2px 2px 10px}.preview{position:relative;width:58px;height:58px}.preview img{width:58px;height:58px;object-fit:cover;border:1px solid var(--line);border-radius:8px;background:#f8faf9}.remove{position:absolute;right:-6px;top:-6px;width:20px;height:20px;padding:0;border:2px solid white;border-radius:50%;background:#172033;color:white;line-height:16px}
@media(max-width:600px){body{padding:0}main{height:100vh;border:0;border-radius:0}.message{max-width:94%}#send{padding:0 14px}}
</style></head><body><main><header><h1>图片创作</h1><a id="settings" href="/config">返回配置</a></header><section id="messages"><div class="message assistant">描述你想生成的画面，或上传参考图片进行编辑。</div></section>
<form id="composer"><div id="previews"></div><div class="compose-row"><label class="attach" title="添加参考图片" aria-label="添加参考图片"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="14" height="16" rx="2"/><circle cx="8" cy="9" r="1.5"/><path d="m4 17 3.5-3.5a2 2 0 0 1 2.8 0L13 16l1.2-1.2a2 2 0 0 1 2.8 0"/><path d="M20 7v6M17 10h6"/></svg><input id="files" type="file" accept="image/*" multiple hidden></label><textarea id="prompt" required placeholder="描述画面或修改内容" rows="1"></textarea><button id="send" type="submit">生成</button></div></form></main><script>
const messages=document.querySelector('#messages'),form=document.querySelector('#composer'),prompt=document.querySelector('#prompt'),send=document.querySelector('#send'),picker=document.querySelector('#files'),previews=document.querySelector('#previews');let attachments=[];
function message(kind,text){const node=document.createElement('div');node.className=`message ${kind}`;node.textContent=text;messages.appendChild(node);messages.scrollTop=messages.scrollHeight;return node}
function renderPreviews(){previews.replaceChildren();previews.style.display=attachments.length?'flex':'none';send.textContent=attachments.length?'编辑':'生成';attachments.forEach((file,index)=>{const item=document.createElement('div');item.className='preview';const image=document.createElement('img');image.src=URL.createObjectURL(file);image.onload=()=>URL.revokeObjectURL(image.src);const remove=document.createElement('button');remove.type='button';remove.className='remove';remove.textContent='×';remove.title='移除图片';remove.onclick=()=>{attachments.splice(index,1);renderPreviews()};item.append(image,remove);previews.appendChild(item)})}
picker.addEventListener('change',()=>{attachments.push(...picker.files);picker.value='';renderPreviews()});
function resizePrompt(){prompt.style.height='40px';prompt.style.height=`${Math.min(prompt.scrollHeight,120)}px`}
prompt.addEventListener('input',resizePrompt);
form.addEventListener('submit',async event=>{event.preventDefault();const value=prompt.value.trim();if(!value)return;message('user',attachments.length?`${value}\n[${attachments.length} 张参考图]`:value);const request=new FormData();request.append('prompt',value);attachments.forEach(file=>request.append('image',file,file.name));prompt.value='';resizePrompt();send.disabled=true;const pending=message('assistant',attachments.length?'编辑中...':'生成中...');try{const response=await fetch('/api/test',{method:'POST',body:request});const data=await response.json();pending.remove();if(!response.ok||!data.ok){message('assistant error',data.error||'请求失败');return}const result=document.createElement('div');result.className='message assistant result';for(let i=0;i<data.images.length;i++){const image=document.createElement('img');image.src=data.images[i];image.alt=value;result.appendChild(image);const path=document.createElement('div');path.className='path';path.textContent=data.files[i];result.appendChild(path)}messages.appendChild(result);attachments=[];renderPreviews();messages.scrollTop=messages.scrollHeight}catch(error){pending.className='message assistant error';pending.textContent=String(error)}finally{send.disabled=false;prompt.focus()}});
prompt.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();form.requestSubmit()}});prompt.focus();
</script></body></html>"""


def configuration_url(path: str = CONFIG_PAGE_PATH) -> str:
    return f"http://{CONFIG_PUBLIC_HOST}:{CONFIG_PORT}{path}"


def configuration_service_is_running() -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(configuration_url("/api/health"), timeout=0.8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("service") == "image"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def register_windows_startup() -> bool:
    if os.name != "nt":
        return False
    import winreg

    python_path = Path(sys.executable).resolve()
    pythonw_path = python_path.with_name("pythonw.exe")
    executable = pythonw_path if pythonw_path.exists() else python_path
    command = subprocess.list2cmdline([str(executable), str(Path(__file__).resolve()), "serve"])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as startup:
        winreg.SetValueEx(startup, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, command)
    return True


def start_configuration_service() -> None:
    command = [sys.executable, str(Path(__file__).resolve()), "serve"]
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)


def ensure_configuration_service(open_browser: bool = False) -> str:
    register_windows_startup()
    if not configuration_service_is_running():
        start_configuration_service()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not configuration_service_is_running():
            time.sleep(0.1)
        if not configuration_service_is_running():
            raise ImageApiError(f"Could not start the configuration service at {configuration_url()}.")
    url = configuration_url()
    if open_browser:
        webbrowser.open(url)
    return url


def configure_in_browser(open_browser: bool = True, persistent: bool = True) -> dict[str, Any] | None:
    ensure_internal_dirs()
    current = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            current.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    current.update(environment_config_values())
    configured: dict[str, Any] | None = None
    if current.get("api_key"):
        try:
            configured = validate_config(current)
        except (ImageApiError, ValueError):
            configured = None
    state: dict[str, Any] = {"saved": configured is not None, "error": "", "config": configured}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/api/health":
                self.respond_json({"ok": True, "service": "image", "configured": state["saved"]})
                return
            if self.path.startswith("/config"):
                self.respond(config_page(current, state["error"]))
                return
            self.respond(chat_page() if state["saved"] else config_page(current, state["error"]))

        def do_POST(self) -> None:
            if self.path == "/api/test":
                self.test_generation()
                return
            length = min(int(self.headers.get("Content-Length", "0")), 64 * 1024)
            values = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            submitted = {key: items[-1] for key, items in values.items()}
            try:
                submitted["timeout_seconds"] = int(submitted.get("timeout_seconds", 180))
                submitted["retries"] = int(submitted.get("retries", 3))
                config = validate_config(submitted)
                save_config_to_environment(config)
                current.update(config)
                state["saved"] = True
                state["config"] = config
                state["error"] = ""
                self.respond(chat_page())
            except (ImageApiError, ValueError) as error:
                current.update(submitted)
                state["error"] = str(error)
                self.respond(config_page(current, state["error"]), status=400)

        def test_generation(self) -> None:
            if not state["saved"] or not state["config"]:
                self.respond_json({"ok": False, "error": "请先保存配置。"}, status=400)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 50 * 1024 * 1024:
                    raise ImageApiError("上传内容不能超过 50 MB。")
                content_type = self.headers.get("Content-Type", "")
                prompt = ""
                with tempfile.TemporaryDirectory(prefix="universal-image-edit-") as temporary:
                    image_paths: list[Path] = []
                    if content_type.startswith("multipart/form-data"):
                        raw = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + self.rfile.read(length)
                        message = BytesParser(policy=policy.default).parsebytes(raw)
                        for part in message.iter_parts():
                            name = part.get_param("name", header="content-disposition")
                            if name == "prompt":
                                prompt = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8").strip()
                            elif name == "image" and part.get_filename():
                                if not part.get_content_type().startswith("image/"):
                                    raise ImageApiError("只支持图片文件。")
                                suffix = Path(part.get_filename()).suffix.lower()
                                if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
                                    suffix = ".bin"
                                path = Path(temporary) / f"image-{len(image_paths) + 1}{suffix}"
                                path.write_bytes(part.get_payload(decode=True))
                                image_paths.append(path)
                    else:
                        payload = json.loads(self.rfile.read(length).decode("utf-8"))
                        prompt = str(payload.get("prompt", "")).strip()
                    if not prompt:
                        raise ImageApiError("提示词不能为空。")
                    args = argparse.Namespace(prompt=prompt, count=1, model=None, size=None, extra_json=None, image=[str(path) for path in image_paths], mask=None)
                    response = edit_request(args, state["config"]) if image_paths else generate_request(args, state["config"])
                    images = extract_images(response, state["config"])
                paths = save_images(images, OUTPUT_DIR, "web")
                encoded = [f"data:{image.content_type if image.content_type.startswith('image/') else 'image/png'};base64,{base64.b64encode(image.body).decode('ascii')}" for image in images]
                self.respond_json({"ok": True, "files": [str(path) for path in paths], "images": encoded})
            except Exception as error:
                self.respond_json({"ok": False, "error": str(error)}, status=502)

        def respond_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def respond(self, html: str, status: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    try:
        server = ThreadingHTTPServer((CONFIG_BIND_HOST, CONFIG_PORT), Handler)
    except OSError as error:
        url = f"http://{CONFIG_PUBLIC_HOST}:{CONFIG_PORT}{CONFIG_PAGE_PATH}"
        raise ImageApiError(f"The fixed configuration page is unavailable at {url}: {error}") from error
    server.timeout = 0.5
    url = f"http://{CONFIG_PUBLIC_HOST}:{server.server_address[1]}{CONFIG_PAGE_PATH}"
    print(f"Open the configuration page: {url}", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        while persistent or not state["saved"]:
            server.handle_request()
    except KeyboardInterrupt as error:
        raise ImageApiError("Configuration was cancelled.") from error
    finally:
        server.server_close()
    return state["config"]


def endpoint_url(config: dict[str, Any], key: str) -> str:
    path = config[key]
    if urllib.parse.urlparse(path).scheme in {"http", "https"}:
        return path
    return f"{config['base_url']}{path}"


def auth_headers(config: dict[str, Any]) -> dict[str, str]:
    value = config["api_key"]
    if config["auth_prefix"]:
        value = f"{config['auth_prefix']} {value}"
    return {config["auth_header"]: value, "Accept": "application/json, image/*"}


def encode_multipart(fields: dict[str, Any], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = f"----UniversalImageApi{secrets.token_hex(12)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        serialized = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value)
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            serialized.encode("utf-8"),
            b"\r\n",
        ])
    for name, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(),
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def request_with_retries(request_factory: Any, config: dict[str, Any]) -> HttpResult:
    attempts = int(config["retries"]) + 1
    for attempt in range(attempts):
        try:
            request = request_factory()
            with urllib.request.urlopen(request, timeout=float(config["timeout_seconds"])) as response:
                return HttpResult(response.read(), response.headers.get_content_type())
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[:2000]
            retryable = error.code in {408, 409, 425, 429} or error.code >= 500
            if attempt == attempts - 1 or not retryable:
                raise ImageApiError(f"Image API returned HTTP {error.code}: {body}") from error
            print(f"Request failed with HTTP {error.code}; retry {attempt + 1}/{attempts - 1}.", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == attempts - 1:
                raise ImageApiError(f"Image API request failed after {attempts} attempts: {error}") from error
            print(f"Request failed: {error}; retry {attempt + 1}/{attempts - 1}.", file=sys.stderr)
        delay = float(config["retry_base_delay_seconds"]) * (2**attempt)
        if delay:
            time.sleep(delay)
    raise AssertionError("retry loop exited unexpectedly")


def parse_extra_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ImageApiError(f"--extra-json is invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ImageApiError("--extra-json must contain a JSON object.")
    return value


def generate_request(args: argparse.Namespace, config: dict[str, Any]) -> HttpResult:
    payload: dict[str, Any] = {
        "prompt": args.prompt,
        "n": args.count,
        "response_format": "b64_json",
    }
    model = args.model or config["model"]
    if model:
        payload["model"] = model
    if args.size:
        payload["size"] = args.size
    payload.update(parse_extra_json(args.extra_json))
    payload["response_format"] = "b64_json"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {**auth_headers(config), "Content-Type": "application/json"}
    return request_with_retries(
        lambda: urllib.request.Request(endpoint_url(config, "generation_path"), body, headers, method="POST"),
        config,
    )


def edit_request(args: argparse.Namespace, config: dict[str, Any]) -> HttpResult:
    image_paths = [Path(item).expanduser().resolve() for item in args.image]
    mask_path = Path(args.mask).expanduser().resolve() if args.mask else None
    for path in [*image_paths, *([mask_path] if mask_path else [])]:
        if not path.is_file():
            raise ImageApiError(f"Input image does not exist: {path}")
    fields: dict[str, Any] = {
        "prompt": args.prompt,
        "n": args.count,
        "response_format": "b64_json",
    }
    model = args.model or config["model"]
    if model:
        fields["model"] = model
    if args.size:
        fields["size"] = args.size
    fields.update(parse_extra_json(args.extra_json))
    fields["response_format"] = "b64_json"
    image_field = "image" if len(image_paths) == 1 else "image[]"
    files = [(image_field, path) for path in image_paths]
    if mask_path:
        files.append(("mask", mask_path))
    body, content_type = encode_multipart(fields, files)
    headers = {**auth_headers(config), "Content-Type": content_type}
    return request_with_retries(
        lambda: urllib.request.Request(endpoint_url(config, "edit_path"), body, headers, method="POST"),
        config,
    )


def decode_base64(value: str) -> tuple[bytes, str | None]:
    if value.startswith("data:"):
        header, encoded = value.split(",", 1)
        mime = header[5:].split(";", 1)[0] or None
        return base64.b64decode(encoded, validate=False), mime
    return base64.b64decode(value, validate=False), None


def collect_candidates(value: Any, base64_values: list[str], urls: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                if key in {"b64_json", "base64", "image_base64", "imageBase64"}:
                    base64_values.append(item)
                elif key == "result" and value.get("type") == "image_generation_call":
                    base64_values.append(item)
                elif key in {"image", "data", "output"} and item.startswith("data:image/"):
                    base64_values.append(item)
                elif key in {"image", "data", "output"} and item.startswith(("http://", "https://")):
                    urls.append(item)
                elif key in {"url", "image_url", "imageUrl"} and item.startswith(("http://", "https://", "data:image/")):
                    urls.append(item)
            else:
                collect_candidates(item, base64_values, urls)
    elif isinstance(value, list):
        for item in value:
            collect_candidates(item, base64_values, urls)


def download_image(url: str, config: dict[str, Any]) -> HttpResult:
    if url.startswith("data:image/"):
        body, mime = decode_base64(url)
        return HttpResult(body, mime or "application/octet-stream")
    api_origin = urllib.parse.urlsplit(config["base_url"]).netloc
    image_origin = urllib.parse.urlsplit(url).netloc
    headers = {"Accept": "image/*"}
    if api_origin == image_origin:
        headers.update(auth_headers(config))
    return request_with_retries(lambda: urllib.request.Request(url, headers=headers), config)


def extract_images(result: HttpResult, config: dict[str, Any]) -> list[HttpResult]:
    if result.content_type.startswith("image/"):
        return [result]
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImageApiError("Image API returned neither image bytes nor valid JSON.") from error
    base64_values: list[str] = []
    urls: list[str] = []
    collect_candidates(payload, base64_values, urls)
    images: list[HttpResult] = []
    for value in base64_values:
        try:
            body, mime = decode_base64(value)
            images.append(HttpResult(body, mime or "application/octet-stream"))
        except (ValueError, base64.binascii.Error):
            continue
    for url in urls:
        images.append(download_image(url, config))
    if not images:
        message = payload.get("error", payload) if isinstance(payload, dict) else payload
        raise ImageApiError(f"No image data found in API response: {str(message)[:2000]}")
    return images


def detect_extension(body: bytes, content_type: str) -> str:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if body.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return ".webp"
    if body.lstrip().startswith(b"<svg"):
        return ".svg"
    return mimetypes.guess_extension(content_type.split(";", 1)[0]) or ".bin"


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned[:80] or "image"


def save_images(images: list[HttpResult], output_dir: Path, prefix: str | None) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = safe_name(prefix or datetime.now().strftime("image-%Y%m%d-%H%M%S"))
    paths: list[Path] = []
    for index, image in enumerate(images, start=1):
        suffix = detect_extension(image.body, image.content_type)
        numbered = f"-{index}" if len(images) > 1 else ""
        candidate = output_dir / f"{base}{numbered}{suffix}"
        sequence = 2
        while candidate.exists():
            candidate = output_dir / f"{base}{numbered}-{sequence}{suffix}"
            sequence += 1
        candidate.write_bytes(image.body)
        paths.append(candidate.resolve())
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure = subparsers.add_parser("configure", help="Open the local configuration form")
    configure.add_argument("--no-browser", action="store_true", help="Print the form URL without opening it")
    subparsers.add_parser("serve", help=argparse.SUPPRESS)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--prompt", required=True)
        subparser.add_argument("--model")
        subparser.add_argument("--size")
        subparser.add_argument("--count", type=int, default=1)
        subparser.add_argument("--extra-json", help="Provider-specific JSON object merged into the request")
        subparser.add_argument("--output-name")
        subparser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)

    generate = subparsers.add_parser("generate", help="Generate images from text")
    add_common(generate)
    edit = subparsers.add_parser("edit", help="Edit images from local references")
    add_common(edit)
    edit.add_argument("--image", action="append", required=True, help="Local input image; repeat for multiple images")
    edit.add_argument("--mask", help="Optional local mask image")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "serve":
            configure_in_browser(open_browser=False, persistent=True)
            return 0
        if args.command == "configure":
            url = ensure_configuration_service(open_browser=not args.no_browser)
            print(json.dumps({"configured": True, "url": url, "storage": "user_environment", "variables": list(ENVIRONMENT_KEYS.values())}, ensure_ascii=False))
            return 0
        if args.count < 1:
            raise ImageApiError("--count must be at least 1.")
        ensure_configuration_service(open_browser=False)
        config = load_config()
        response = generate_request(args, config) if args.command == "generate" else edit_request(args, config)
        images = extract_images(response, config)
        paths = save_images(images, args.output_dir.expanduser().resolve(), args.output_name)
        print(json.dumps({"ok": True, "files": [str(path) for path in paths]}, ensure_ascii=False))
        return 0
    except ImageApiError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
