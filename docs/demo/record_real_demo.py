"""Record a real AdVista browser walkthrough through Chrome DevTools Protocol."""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import websocket

DEBUG_URL = "http://127.0.0.1:9223"
APP_URL = "http://127.0.0.1:8080"
RUN_ID = "exec_e6f20bc57f5446f1babc96c07fa8510c"
VIDEO = Path("/data/tec-chi/MARS2/MAC/dataset/mars2_videos/0b36f8c9-6dac-49fa-8f2a-89fb7ec07204.mp4")
FRAMES = Path("/tmp/advista-real-frames")


class CDP:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=30)
        self.lock = threading.Lock()
        self.next_id = 1

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            call_id = self.next_id
            self.next_id += 1
            self.ws.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
            while True:
                message = json.loads(self.ws.recv())
                if message.get("id") == call_id:
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    return message.get("result", {})

    def js(self, expression: str, *, await_promise: bool = False) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        return result.get("result", {}).get("value")

    def screenshot(self, path: Path) -> None:
        value = self.call(
            "Page.captureScreenshot",
            {"format": "jpeg", "quality": 82, "captureBeyondViewport": False},
        )
        path.write_bytes(base64.b64decode(value["data"]))


def tab() -> str:
    with urllib.request.urlopen(f"{DEBUG_URL}/json/list", timeout=10) as response:
        pages = json.load(response)
    for page in pages:
        if page.get("type") == "page" and page.get("url", "").startswith(APP_URL):
            return page["webSocketDebuggerUrl"]
    request = urllib.request.Request(f"{DEBUG_URL}/json/new", method="PUT")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)["webSocketDebuggerUrl"]


def wait_for(cdp: CDP, expression: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cdp.js(expression):
            return
        time.sleep(0.2)
    raise TimeoutError(expression)


def hold(cdp: CDP, seconds: float, counter: list[int], fps: int = 4) -> None:
    for _ in range(max(1, round(seconds * fps))):
        cdp.screenshot(FRAMES / f"frame_{counter[0]:05d}.jpg")
        counter[0] += 1
        time.sleep(1 / fps)


def click_effect(cdp: CDP, selector: str) -> None:
    cdp.js(
        f"""(() => {{
          const el=document.querySelector({json.dumps(selector)});
          if(!el) return false;
          const r=el.getBoundingClientRect();
          const dot=document.createElement('span');
          dot.style.cssText=`position:fixed;z-index:9999;left:${{r.left+r.width/2-13}}px;top:${{r.top+r.height/2-13}}px;width:26px;height:26px;border:3px solid #d97745;border-radius:50%;pointer-events:none;background:#fff5;`;
          document.body.appendChild(dot); setTimeout(()=>dot.remove(),700); el.click(); return true;
        }})()"""
    )


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    for frame in FRAMES.glob("frame_*.jpg"):
        frame.unlink()
    cdp = CDP(tab())
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1280, "height": 720, "deviceScaleFactor": 1, "mobile": False},
    )
    cdp.call("Page.navigate", {"url": APP_URL})
    wait_for(cdp, "document.readyState === 'complete'")
    time.sleep(1)
    if not cdp.js("Boolean(document.querySelector('#composer-plus'))"):
        raise RuntimeError(cdp.js("[location.href, document.readyState, document.title, document.body.innerText.slice(0,200)]"))
    counter = [0]

    # Real upload preparation in the actual workbench.
    hold(cdp, 2, counter)
    click_effect(cdp, "#composer-plus")
    hold(cdp, 1.5, counter)
    document = cdp.call("DOM.getDocument", {"depth": -1})
    node = cdp.call(
        "DOM.querySelector",
        {"nodeId": document["root"]["nodeId"], "selector": "#video"},
    )
    cdp.call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(VIDEO)]})
    cdp.js("document.querySelector('#video').dispatchEvent(new Event('change',{bubbles:true}))")
    hold(cdp, 2.5, counter)
    click_effect(cdp, 'input[value="report"]')
    hold(cdp, 1, counter)
    prompt = "分析这条广告的核心卖点、受众线索和转化路径"
    for index in range(4, len(prompt) + 1, 4):
        text = prompt[:index]
        cdp.js(
            f"document.querySelector('#prompt').value={json.dumps(text)};"
            "document.querySelector('#prompt').dispatchEvent(new Event('input',{bubbles:true}))"
        )
        hold(cdp, 0.18, counter)
    hold(cdp, 1.5, counter)

    # Open a real completed execution through the same UI binding used by users.
    cdp.js(f"bindRun({json.dumps(RUN_ID)}, true)", await_promise=True)
    hold(cdp, 5, counter)
    cdp.js("document.querySelector('#conversation').scrollTo({top:document.querySelector('#conversation').scrollHeight,behavior:'smooth'})")
    hold(cdp, 2, counter)

    # Open the real report, then expand evidence and scroll through it.
    cdp.call("Page.navigate", {"url": f"{APP_URL}/api/runs/{RUN_ID}/exports/report-html"})
    wait_for(cdp, "document.readyState === 'complete'")
    hold(cdp, 3, counter)
    cdp.js("document.querySelectorAll('details').forEach(item=>item.open=true)")
    hold(cdp, 2, counter)
    for ratio in (0.18, 0.36, 0.55, 0.74):
        cdp.js(f"window.scrollTo({{top:(document.body.scrollHeight-innerHeight)*{ratio},behavior:'smooth'}})")
        hold(cdp, 2.2, counter)

    # Return to the real run and ask a grounded follow-up question.
    cdp.call("Page.navigate", {"url": APP_URL})
    time.sleep(1.5)
    wait_for(cdp, "document.readyState === 'complete'")
    wait_for(cdp, "Boolean(document.querySelector('#prompt'))")
    cdp.js(f"bindRun({json.dumps(RUN_ID)}, true)", await_promise=True)
    hold(cdp, 2, counter)
    question = "这条广告最需要补充验证的信息是什么？"
    cdp.js(
        f"document.querySelector('#prompt').value={json.dumps(question)};"
        "document.querySelector('#prompt').dispatchEvent(new Event('input',{bubbles:true}))"
    )
    hold(cdp, 2, counter)
    cdp.js("document.querySelector('#prompt-form').requestSubmit()")
    hold(cdp, 18, counter, fps=3)
    hold(cdp, 3, counter)
    print(f"recorded {counter[0]} frames")


if __name__ == "__main__":
    main()
