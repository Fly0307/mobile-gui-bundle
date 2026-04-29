"""
Lightweight LLM client using requests.
Calls any OpenAI-compatible chat completions endpoint.
"""
import base64
import time
from io import BytesIO

import requests


def _encode_image(image_bytes: bytes, resize=None) -> str:
    """Encode PNG bytes to base64 data URL, optionally resizing to [height, width]."""
    try:
        from PIL import Image  # type: ignore[import]
        img = Image.open(BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        if resize:
            img = img.resize((resize[1], resize[0]))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        encoded = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{encoded}"
    except ImportError:
        encoded = base64.b64encode(image_bytes).decode()
        return f"data:image/png;base64,{encoded}"


def image_to_data_url(image_source, resize=None) -> str:
    """
    Convert image source to OpenAI data URL.
    image_source: bytes | base64 str | file path str
    """
    if isinstance(image_source, bytes):
        return _encode_image(image_source, resize=resize)
    if isinstance(image_source, str):
        if image_source.startswith("data:"):
            return image_source
        if image_source.startswith("/") or image_source.startswith("."):
            with open(image_source, "rb") as f:
                return _encode_image(f.read(), resize=resize)
        # assume raw base64
        raw = base64.b64decode(image_source)
        return _encode_image(raw, resize=resize)
    raise ValueError(f"Unsupported image source type: {type(image_source)}")


def ask_llm(messages: list, llm_config: dict) -> str:
    """
    Call the LLM and return the response text.

    llm_config keys: api_base, api_key, model_name, temperature,
                     max_tokens, top_p, image_resize (optional list [h,w])
    """
    api_base = llm_config["api_base"].rstrip("/")
    api_key = llm_config.get("api_key", "EMPTY")
    model_name = llm_config["model_name"]
    resize = llm_config.get("image_resize")  # [height, width] or null

    # Pre-process image_url content blocks
    prep_t0 = time.time()
    processed_messages = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if isinstance(content, list):
            new_parts = []
            for part in content:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if not url.startswith("data:"):
                        url = image_to_data_url(url, resize=resize)
                    elif resize:
                        raw = base64.b64decode(url.split(",", 1)[1])
                        url = _encode_image(raw, resize=resize)
                    new_parts.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    new_parts.append(part)
            processed_messages.append({"role": role, "content": new_parts})
        else:
            processed_messages.append(msg)
    prep_elapsed = time.time() - prep_t0

    payload = {
        "model": model_name,
        "messages": processed_messages,
        "temperature": llm_config.get("temperature", 0.1),
        "max_tokens": llm_config.get("max_tokens", 4096),
        "top_p": llm_config.get("top_p", 0.95),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    t0 = time.time()
    resp = requests.post(
        f"{api_base}/chat/completions",
        headers=headers,
        json=payload,
        timeout=llm_config.get("timeout", 60),
    )
    resp.raise_for_status()
    elapsed = time.time() - t0

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    print(f"[LLM] {model_name}: preprocess={prep_elapsed:.2f}s request={elapsed:.2f}s total={prep_elapsed + elapsed:.2f}s")
    return content
