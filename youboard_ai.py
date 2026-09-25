#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""YouBoard AI 就地处理模块（配置 + OpenAI 兼容协议 + 流式输出）。

设计要点：
- 只走「OpenAI 兼容」协议：一套代码覆盖 DeepSeek / 通义千问 / 智谱 GLM /
  OpenAI / Ollama 本地 / 自建网关；服务商差异只体现在 Base URL 与模型名
- 只发送用户在列表里选中的那一条，不会自动上传任何内容
- API Key 用 youboard_sync.protect_secret()（Windows 走 DPAPI）加密后写进配置，
  不落明文、不写日志
- 超长内容按字符上限截断后再发送，界面会标注「已截断」
- 网络请求支持超时 / 重试 / 代理，都能在设置里改
- 不依赖 PyQt：界面层用 QThread 调用 stream_chat()，逐段回调即可
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

from youboard_core import load_config, save_config
from youboard_sync import protect_secret, unprotect_secret
from youboard_version import APP_NAME, APP_VERSION

# ===========================================================================
# 常量
# ===========================================================================

# 单次送入模型的正文字符上限：约 2.4 万字（中英混排），够用又不会让费用失控
AI_MAX_INPUT_CHARS = 24000
AI_DEFAULT_MAX_TOKENS = 1200
AI_DEFAULT_TIMEOUT = 60
AI_DEFAULT_RETRIES = 2
# 多轮对话只带最近这么多轮（一问一答算一轮），避免 token 越滚越多
AI_CHAT_MAX_TURNS = 6
# 图片先压到这个长边再发（体积/费用可控；1280 对识图与 OCR 都够用）
AI_IMAGE_MAX_SIDE = 1280
AI_IMAGE_QUALITY = 82
AI_ACTIONS = ("summarize", "translate", "rewrite", "extract", "custom")
# 图片 / 文件分类下的动作
AI_IMAGE_ACTIONS = ("describe", "ocr", "img_translate", "custom")
AI_FILE_ACTIONS = ("file_summary", "file_suggest", "custom")
# 设置里的服务商展示顺序：国内能直连的放前面
AI_PROVIDER_ORDER = ("deepseek", "dashscope", "zhipu", "openai", "ollama",
                     "custom")

# 各服务商预设：Base URL / 默认模型 / 可选模型（2026-09 核对过各家官方文档）
PROVIDERS = {
    "deepseek": {
        "zh": "DeepSeek 官方（国内直连）",
        "en": "DeepSeek",
        # 官方「模型 & 价格」页：BASE URL (OpenAI 格式) = https://api.deepseek.com
        # 模型 id：deepseek-flash（= DeepSeek-V4.1-Flash）、deepseek-v4-pro（= V4-Pro-0813）
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "model_label": "DeepSeek-V4.1-Flash",
        "models": ["deepseek-flash", "deepseek-v4-pro", "deepseek-v4-pro-0813"],
        "needs_key": True,
    },
    "dashscope": {
        "zh": "通义千问（阿里云百炼）",
        "en": "Qwen (DashScope)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.8-flash",
        "model_label": "Qwen3.8-Flash",
        "models": ["qwen3.8-flash", "qwen3.7-plus", "qwen3.5-omni-plus"],
        "needs_key": True,
    },
    "zhipu": {
        "zh": "智谱 GLM",
        "en": "Zhipu GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5.3-flash",
        "model_label": "GLM-5.3-Flash",
        "models": ["glm-5.3-flash", "glm-5.3", "glm-5.2"],
        "needs_key": True,
    },
    "openai": {
        "zh": "OpenAI（需要能访问的网络或代理）",
        "en": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.6",
        "model_label": "GPT-5.6",
        "models": ["gpt-5.6", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-4o-mini"],
        "needs_key": True,
    },
    "ollama": {
        "zh": "Ollama 本地模型（不联网、不花钱）",
        "en": "Ollama (local)",
        # 用 127.0.0.1 而不是 localhost：Windows 上 localhost 会先解析成 IPv6 ::1，
        # 而 Ollama 默认只监听 IPv4 的 127.0.0.1，于是报 WinError 10061"积极拒绝"。
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen3.5:4b",
        "model_label": "Qwen3.5（本地）",
        "models": ["qwen3.5:4b", "qwen3.5:2b", "qwen3.5:0.8b", "qwen3.8",
                   "minicpm-v4.5", "gemma4", "glm-5.3-flash"],
        # 能看图的本地模型：图片请求会自动换成它（留空 = 还是用上面那个）
        "vision_model": "qwen3.5:4b",
        "needs_key": False,
        "local": True,
    },
    "custom": {
        "zh": "自定义（任意 OpenAI 兼容接口）",
        "en": "Custom (OpenAI-compatible)",
        "base_url": "",
        "model": "",
        "model_label": "",
        "models": [],
        "needs_key": True,
    },
}

# 面向用户的中英文提示（模块自带一份，界面层直接复用 ai_text()）
AI_TEXT = {
    "need_base_url": {"zh": "还没填接口地址（Base URL）",
                      "en": "Base URL is empty"},
    "need_model": {"zh": "还没填模型名", "en": "Model name is empty"},
    "need_key": {"zh": "还没填 API Key", "en": "API Key is empty"},
    "err_auth": {"zh": "API Key 无效或没有权限（HTTP 401 / 403）",
                 "en": "API Key is invalid or not allowed (HTTP 401 / 403)"},
    "err_notfound": {"zh": "接口地址或模型名不对（HTTP 404），检查 Base URL 与模型名",
                     "en": "Endpoint or model not found (HTTP 404) — check Base URL and model name"},
    "err_rate": {"zh": "被限流或额度不足（HTTP 429），稍后再试",
                 "en": "Rate limited or out of quota (HTTP 429), try again later"},
    "err_server": {"zh": "服务端错误（HTTP {code}），稍后再试",
                   "en": "Server error (HTTP {code}), try again later"},
    "err_bad_request": {"zh": "请求被拒绝（HTTP {code}）：{detail}",
                        "en": "Request rejected (HTTP {code}): {detail}"},
    "err_bad_response": {"zh": "返回内容看不懂：{detail}",
                         "en": "Unreadable response: {detail}"},
    "err_network": {"zh": "连不上服务器：{detail}（若需要代理，请在设置里填代理地址）",
                    "en": "Cannot reach the server: {detail} (set a proxy in Settings if needed)"},
    "err_local_offline": {
        "zh": "连不上本机模型服务：{detail}\n"
              "本地免费 AI 要先装 Ollama（https://ollama.com/download），"
              "装好后在命令行跑一次：ollama pull qwen3.5:4b"
              "（这个模型能识图；小机器可以换 qwen3.5:2b 或 qwen3.5:0.8b），"
              "然后回到这里点「检测本地模型」。"
              "如果服务跑在别的端口，把上面的接口地址改成实际地址即可。",
        "en": "Cannot reach the local model service: {detail}\n"
              "For free local AI, install Ollama (https://ollama.com/download), "
              "then run: ollama pull qwen3.5:4b "
              "(this one can see images; use qwen3.5:2b / qwen3.5:0.8b on small "
              "machines), and click \"Detect local models\" back here. "
              "If your server uses another port, point the Base URL at it.",
    },
    "err_timeout": {"zh": "请求超时（{seconds}s），可以在设置里把超时调大",
                    "en": "Request timed out after {seconds}s — raise the timeout in Settings"},
    "err_empty": {"zh": "模型没有返回内容", "en": "The model returned nothing"},
    "err_cancelled": {"zh": "已停止生成", "en": "Generation stopped"},
    "err_provider": {"zh": "服务商返回错误：{detail}",
                     "en": "Provider error: {detail}"},
    "truncated": {"zh": "内容过长，只发送了前 {n} 个字符",
                  "en": "Content too long — only the first {n} characters were sent"},
    "err_image_missing": {"zh": "图片文件不在了，没法分析",
                          "en": "The image file is gone — cannot analyse it"},
    "image_sent": {"zh": "已按 {w}×{h} 压缩后发送（{size}）",
                   "en": "Sent as {w}×{h} after compression ({size})"},
    "need_vision": {"zh": "图片分析需要支持视觉的模型，例如 deepseek-v4-flash-vision-exp、"
                          "gpt-5.6、qwen3.5-omni-plus、glm-5v 或本地视觉模型",
                    "en": "Image analysis needs a vision model, e.g. "
                          "deepseek-v4-flash-vision-exp, gpt-5.6, "
                          "qwen3.5-omni-plus, glm-5v or a local vision model"},
    "need_vision_local": {
        "zh": "本地模型 {model} 看不了图：在设置里把「识图模型」填成能识图的本地模型"
              "（例如 qwen3.5:4b / minicpm-v4.5 / llava），"
              "没有的话先在命令行跑：ollama pull qwen3.5:4b",
        "en": "The local model {model} cannot read images: set Vision model to a "
              "vision-capable local model (e.g. qwen3.5:4b / minicpm-v4.5 / "
              "llava), or run: ollama pull qwen3.5:4b",
    },
}


def ai_text(key, lang="zh", **kw):
    """取一条模块内置提示（中英双份）。"""
    lang = "en" if str(lang or "").lower().startswith("en") else "zh"
    entry = AI_TEXT.get(key) or {}
    text = entry.get(lang) or entry.get("zh") or key
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def provider_info(pid):
    return PROVIDERS.get(pid) or PROVIDERS["custom"]


# ---------------------------------------------------------------------------
# 本地模型（v3.3.5）：Ollama / LM Studio / llama.cpp 这类跑在本机的服务
# —— 免费、不联网，而且很多模型还能看图（识图）。这里提供三件小事：
#    1) 认得出"本机地址"（localhost / 127.0.0.1 / ::1），
#    2) localhost 连不上时自动换成 127.0.0.1 再试一次（Windows 上常见坑），
#    3) 问一下本机服务装了哪些模型，顺便标出哪些能识图。
# ---------------------------------------------------------------------------

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

# 名字里带这些片段的本地模型基本都能识图（用户装了别的也可以自己填）
VISION_MODEL_HINTS = (
    "qwen3.5", "qwen3.6", "qwen3.8", "qwen3-vl", "qwen2.5vl", "qwen2.5-vl",
    "qwen2-vl", "minicpm-v", "llava", "bakllava", "moondream", "vision",
    "gemma3", "gemma4", "medgemma", "glm-4v", "glm-5", "internvl", "pixtral",
    "cogvlm", "mistral-small3", "mistral-medium", "omni", "ocr",
)


def is_local_url(url):
    """这个接口地址是不是指向本机（本地模型服务）。"""
    text = str(url or "").strip().lower()
    if not text:
        return False
    for scheme in ("http://", "https://"):
        if text.startswith(scheme):
            text = text[len(scheme):]
    host = text.split("/", 1)[0].split("@")[-1]
    if host.startswith("["):                      # [::1]:11434
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host in LOCAL_HOSTS


def alternate_local_url(url):
    """本机地址的"另一种写法"：localhost ↔ 127.0.0.1（连不上时拿它再试一次）。"""
    text = str(url or "").strip()
    if not text:
        return ""
    for old, new in (("//localhost", "//127.0.0.1"),
                     ("//127.0.0.1", "//localhost")):
        if old in text:
            return text.replace(old, new, 1)
    return ""


def local_service_root(url):
    """从接口地址推出本机服务的根地址（去掉结尾的 /v1）。"""
    text = str(url or "").strip().rstrip("/")
    if text.endswith("/chat/completions"):
        text = text[:-len("/chat/completions")].rstrip("/")
    if text.endswith("/v1"):
        text = text[:-3].rstrip("/")
    return text


def looks_like_vision_model(name):
    """本地模型名看着像不像"能识图"的那种（用户也可以自己填别的）。"""
    low = str(name or "").lower()
    return any(hint in low for hint in VISION_MODEL_HINTS)


def probe_local_models(base_url, timeout=4):
    """问一下本机服务装了哪些模型；返回 (ok, 模型名列表, 说明)。

    先试 Ollama 的 /api/tags，再试通用的 /v1/models（LM Studio / llama.cpp 等）。
    """
    root = local_service_root(base_url) or "http://127.0.0.1:11434"
    candidates = [root + "/api/tags",
                  (str(base_url or "").rstrip("/") or root + "/v1") + "/models"]
    alt = alternate_local_url(root)
    if alt:
        candidates.append(alt + "/api/tags")
    detail = ""
    for url in candidates:
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "%s/%s" % (APP_NAME, APP_VERSION))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            data = json.loads(raw.decode("utf-8", "replace"))
        except Exception as err:                   # noqa: BLE001 - 探测失败就换下一个
            detail = str(err)[:160]
            continue
        names = []
        if isinstance(data, dict):
            for item in (data.get("models") or []):
                if isinstance(item, dict) and item.get("name"):
                    names.append(str(item["name"]).strip())
            for item in (data.get("data") or []):
                if isinstance(item, dict) and item.get("id"):
                    names.append(str(item["id"]).strip())
        names = sorted({n for n in names if n})
        return True, names, ""
    return False, [], detail or "no response"


def provider_label(pid, lang="zh"):
    info = provider_info(pid)
    lang = "en" if str(lang or "").lower().startswith("en") else "zh"
    return info.get(lang) or info.get("zh") or pid


def model_display_name(settings):
    """界面里展示用的模型名：优先用户自己填的显示名，其次真实模型 id。"""
    settings = settings or {}
    label = str(settings.get("model_label") or "").strip()
    return label or str(settings.get("model") or "").strip()


# ===========================================================================
# 设置读写（配置里的 ai 段）
# ===========================================================================

def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def default_ai_settings():
    info = PROVIDERS["deepseek"]
    return {
        "enabled": True,
        "provider": "deepseek",
        "base_url": info["base_url"],
        "model": info["model"],
        "model_label": info.get("model_label", ""),
        # 识图模型：留空 = 用上面那个模型（本地模型常是"文本一个、识图一个"）
        "vision_model": "",
        "api_key": "",
        "api_key_saved": False,
        "temperature": 0.3,
        "max_tokens": AI_DEFAULT_MAX_TOKENS,
        "timeout": AI_DEFAULT_TIMEOUT,
        "retries": AI_DEFAULT_RETRIES,
        "proxy": "",
    }


def load_ai_settings(config=None):
    """读 AI 设置；返回的 api_key 是明文（只在内存里用）。"""
    cfg = config if isinstance(config, dict) else load_config()
    raw = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    out = default_ai_settings()
    provider = str(raw.get("provider") or out["provider"])
    if provider not in PROVIDERS:
        provider = "custom"
    out["provider"] = provider
    info = PROVIDERS[provider]
    out["base_url"] = str(raw.get("base_url") or info["base_url"] or "")
    out["model"] = str(raw.get("model") or info["model"] or "")
    out["model_label"] = str(raw.get("model_label") or "")
    if "vision_model" in raw:
        out["vision_model"] = str(raw.get("vision_model") or "").strip()
    elif is_local_url(out["base_url"]):
        out["vision_model"] = str(info.get("vision_model") or "").strip()
    else:
        out["vision_model"] = ""
    out["temperature"] = min(2.0, max(0.0, _as_float(
        raw.get("temperature"), out["temperature"])))
    out["max_tokens"] = max(0, _as_int(raw.get("max_tokens"),
                                       out["max_tokens"]))
    out["timeout"] = max(10, _as_int(raw.get("timeout"), out["timeout"]))
    out["retries"] = max(0, min(5, _as_int(raw.get("retries"),
                                           out["retries"])))
    out["proxy"] = str(raw.get("proxy") or "")
    stored = str(raw.get("api_key") or "")
    out["api_key"] = unprotect_secret(stored) if stored else ""
    out["api_key_saved"] = bool(stored)
    out["enabled"] = bool(raw.get("enabled", True))
    # 每个服务商各自存过的内容（给界面用：切回某家时把这家上次的地址/模型放回来）
    out["providers"] = provider_saved_settings(cfg)
    return out


def save_ai_settings(settings, config=None):
    """写回配置：API Key 加密存；留空且原来存过 Key 就保留原 Key。"""
    cfg = dict(config) if isinstance(config, dict) else load_config()
    old = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    settings = settings or {}
    provider = str(settings.get("provider") or "deepseek")
    if provider not in PROVIDERS:
        provider = "custom"
    out = {
        "enabled": bool(settings.get("enabled", True)),
        "provider": provider,
        "base_url": str(settings.get("base_url") or ""),
        "model": str(settings.get("model") or ""),
        "model_label": str(settings.get("model_label") or "").strip(),
        "vision_model": str(settings.get("vision_model") or "").strip(),
        "temperature": round(min(2.0, max(0.0, _as_float(
            settings.get("temperature"), 0.3))), 2),
        "max_tokens": max(0, _as_int(settings.get("max_tokens"),
                                     AI_DEFAULT_MAX_TOKENS)),
        "timeout": max(10, _as_int(settings.get("timeout"),
                                   AI_DEFAULT_TIMEOUT)),
        "retries": max(0, min(5, _as_int(settings.get("retries"),
                                         AI_DEFAULT_RETRIES))),
        "proxy": str(settings.get("proxy") or ""),
    }
    key = str(settings.get("api_key") or "").strip()
    # 每个服务商各存一份：这次保存只动"当前这家"的 Key，别家的原样留着
    blocks = dict(_provider_blocks(cfg))
    prev = blocks.get(provider) if isinstance(blocks.get(provider), dict) else {}
    if key and not key.startswith(("dpapi:", "b64:")):
        stored_key = protect_secret(key)
    elif settings.get("api_key_saved") is False:
        stored_key = ""
    else:
        # 没动过 Key：保留"这家"原来那把；这家还没有（老配置升级上来）就沿用当前激活那家的
        stored_key = str(prev.get("api_key") or "")
        if not stored_key and provider == str(old.get("provider") or ""):
            stored_key = str(old.get("api_key") or "")
    out["api_key"] = stored_key
    block = {name: str(out.get(name) or "") for name in AI_PROVIDER_FIELDS}
    block["api_key"] = stored_key
    blocks[provider] = block
    out["providers"] = blocks
    cfg["ai"] = out
    save_config(cfg)
    return out


def ai_api_key(settings):
    """内存里的明文 Key（配置里存的是密文，这里自动解开）。"""
    key = str((settings or {}).get("api_key") or "").strip()
    if key.startswith(("dpapi:", "b64:")):
        return unprotect_secret(key)
    return key


# ---------------------------------------------------------------------------
# 每个服务商各存一份（v3.3.5）：以前配置里只有"当前这一家"，
# 换一家服务商保存后，上一家的地址 / 模型 / Key 就没了。
# 现在配置里多一个 ai.providers 表，每家的内容都留着，切回去就恢复。
# ---------------------------------------------------------------------------

AI_PROVIDER_FIELDS = ("base_url", "model", "model_label", "vision_model")


def _provider_blocks(cfg=None):
    """配置里的 ai.providers 原始表（Key 是密文，不外传）。

    老配置（3.3.4 及更早）只存了"当前这一家"的顶层字段：这里顺手把那家补成一条
    存档，免得升级后第一次换服务商保存就把它的地址 / 模型 / Key 顶掉。
    """
    cfg = cfg if isinstance(cfg, dict) else load_config()
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    saved = ai.get("providers") if isinstance(ai.get("providers"), dict) else {}
    saved = dict(saved) if isinstance(saved, dict) else {}
    legacy_pid = str(ai.get("provider") or "")
    if legacy_pid in PROVIDERS and legacy_pid not in saved:
        block = {key: str(ai.get(key) or "") for key in AI_PROVIDER_FIELDS}
        block["api_key"] = str(ai.get("api_key") or "")
        if block.get("base_url") or block.get("model") or block.get("api_key"):
            saved[legacy_pid] = block
    return saved


def provider_saved_settings(cfg=None):
    """给界面用：每家服务商上次存过的内容 + 有没有存过 Key（不含明文 Key）。"""
    out = {}
    for pid, item in _provider_blocks(cfg).items():
        if pid not in PROVIDERS or not isinstance(item, dict):
            continue
        block = {key: str(item.get(key) or "") for key in AI_PROVIDER_FIELDS
                 if key in item}
        block["has_key"] = bool(str(item.get("api_key") or ""))
        out[str(pid)] = block
    return out


def provider_api_key(provider, cfg=None):
    """取某一家存过的明文 Key（切到那家做「测试连接」时用）。"""
    item = _provider_blocks(cfg).get(str(provider))
    stored = str((item or {}).get("api_key") or "")
    return unprotect_secret(stored) if stored else ""


# ===========================================================================
# 提示词与请求组装
# ===========================================================================

AI_SYSTEM = {
    "zh": "你是 YouBoard（剪贴板工具）里的文本助手。直接给出结果，不要寒暄、"
          "不要复述要求、不要以「以下是」这类话开头。",
    "en": "You are the text assistant inside YouBoard, a clipboard tool. "
          "Output the result only: no greetings, no restating the request, "
          "no \"Here is ...\" opener.",
}

AI_ACTION_PROMPT = {
    "summarize": {
        "zh": "请阅读下面的内容，用简洁的要点总结（3-6 条，每条以「- 」开头），"
              "保留关键数字、结论与待办。",
        "en": "Summarize the content below as 3-6 concise bullet points "
              "(each starting with \"- \"), keeping key numbers, conclusions "
              "and action items.",
    },
    "translate": {
        "zh": "请把下面的内容翻译成{target}。只输出译文，保留原有分段与格式，"
              "不要解释。",
        "en": "Translate the content below into {target}. Output the "
              "translation only, keep the original paragraphing, no notes.",
    },
    "rewrite": {
        "zh": "请把下面的内容改写成更通顺、书面化的表达：保持原意与信息量，"
              "长度与原文相当，只输出改写后的正文。",
        "en": "Rewrite the content below to be clearer and more polished: "
              "keep the meaning and the information, similar length, output "
              "the rewritten text only.",
    },
    "extract": {
        "zh": "请从下面的内容中提取关键信息：人名 / 机构 / 时间 / 地点 / 数字 / "
              "待办事项。按类别用「- 」列点；原文没有的类别直接跳过，不要编造。",
        "en": "Extract the key information from the content below: people / "
              "organizations / dates / places / numbers / action items. List "
              "them by category with \"- \"; skip missing categories and "
              "never invent facts.",
    },
    "custom": {
        "zh": "请按下面的要求处理这段内容：{instruction}\n只输出处理结果。",
        "en": "Process the content below following this instruction: "
              "{instruction}\nOutput the result only.",
    },
}

AI_BLOCK = {"zh": ("----- 内容开始 -----", "----- 内容结束 -----"),
            "en": ("----- BEGIN CONTENT -----", "----- END CONTENT -----")}

# 自由对话：把选中的记录作为参考资料，之后用户问什么答什么
AI_CHAT_CONTEXT = {
    "zh": "下面是我从剪贴板里复制的一条记录，作为这次对话的参考资料。"
          "接下来我会继续提问，请结合它回答；回答直接给结果，不要寒暄。\n\n"
          "{begin}\n{body}\n{end}",
    "en": "Below is an entry I copied to my clipboard. Use it as reference "
          "for this conversation. I will keep asking questions — answer with "
          "the result only, no greetings.\n\n{begin}\n{body}\n{end}",
}

# 「图片」分类：让模型看图（需要支持视觉的模型）
AI_IMAGE_PROMPT = {
    "describe": {
        "zh": "请描述这张图片：主体是什么、什么场景、画面里有没有文字、整体风格。"
              "用简洁的要点（3-6 条）。",
        "en": "Describe this image: main subject, scene, any visible text, "
              "overall style. Use 3-6 concise bullets.",
    },
    "ocr": {
        "zh": "请把这张图片里的文字按原样提取出来（保留分行，不要翻译、不要解释）；"
              "如果图里没有文字，就直接回答「没有文字」。",
        "en": "Extract the text in this image verbatim (keep the line breaks; "
              "do not translate or explain). If there is no text, just say "
              "\"no text\".",
    },
    "img_translate": {
        "zh": "请把这张图片里的文字翻译一下：图中是中文就译成英文，否则译成简体中文。"
              "只输出译文，保留原有分行。",
        "en": "Translate the text in this image: if it is Chinese, translate "
              "into English; otherwise into Simplified Chinese. Output the "
              "translation only, keeping the original line breaks.",
    },
    "custom": {
        "zh": "请按下面的要求处理这张图片：{instruction}\n只输出结果。",
        "en": "Handle this image following the instruction below: "
              "{instruction}\nOutput the result only.",
    },
}

# 「文件」分类：模型看不到文件内容，只能看清单（文件名 / 格式 / 大小）
AI_FILE_PROMPT = {
    "file_summary": {
        "zh": "下面是我这次复制的文件清单（只有名称、格式、大小）。请总结："
              "大概是什么内容或项目、有没有明显的重复、体积异常或缺失；用要点回答。",
        "en": "Below is a list of files I copied (names, formats and sizes "
              "only). Summarise what this likely is, and flag obvious "
              "duplicates, oversized or missing items. Use bullets.",
    },
    "file_suggest": {
        "zh": "请根据这份文件清单给出整理建议：怎么归档、要不要重命名、可以怎么分类；"
              "简明列点，能直接照做。",
        "en": "Suggest how to tidy this file list: how to archive it, whether "
              "to rename anything, and how to group it. Keep it short and "
              "actionable.",
    },
    "custom": {
        "zh": "下面是我这次复制的文件清单。请按这个要求处理：{instruction}\n只输出结果。",
        "en": "Below is a list of files I copied. Handle it following this "
              "instruction: {instruction}\nOutput the result only.",
    },
}
AI_TARGET = {"zh": {"zh": "简体中文", "en": "英文"},
             "en": {"zh": "Simplified Chinese", "en": "English"}}


def guess_translate_target(text):
    """中文为主 → 译成英文，否则译成简体中文；返回 'zh' / 'en'。"""
    sample = (text or "")[:2000]
    if not sample:
        return "zh"
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    # 短句也要判得准：中文占比高（且至少有 4 个汉字）就译成英文
    return "en" if cjk >= 4 and cjk / float(len(sample)) >= 0.15 else "zh"


def truncate_text(text, limit=AI_MAX_INPUT_CHARS):
    """按字符上限截断，返回 (文本, 是否被截断)。"""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit], True


def prepare_request(action, text, settings=None, custom_prompt="", lang="zh"):
    """组装这次要送出去的请求（只有选中这一条内容）。"""
    lang = "en" if str(lang or "").lower().startswith("en") else "zh"
    action = action if action in AI_ACTION_PROMPT else "summarize"
    body, truncated = truncate_text(text, AI_MAX_INPUT_CHARS)
    target = ""
    if action == "translate":
        target = AI_TARGET[lang][guess_translate_target(text)]
        instruction = AI_ACTION_PROMPT[action][lang].format(target=target)
    elif action == "custom":
        tip = (custom_prompt or "").strip()
        if not tip:
            tip = AI_ACTION_PROMPT["summarize"][lang]
        instruction = AI_ACTION_PROMPT[action][lang].format(instruction=tip)
    else:
        instruction = AI_ACTION_PROMPT[action][lang]
    begin, end = AI_BLOCK[lang]
    user = "%s\n\n%s\n%s\n%s" % (instruction, begin, body, end)
    return {
        "action": action,
        "messages": [{"role": "system", "content": AI_SYSTEM[lang]},
                     {"role": "user", "content": user}],
        "truncated": truncated,
        "sent_chars": len(body),
        "total_chars": len(text or ""),
        "target": target,
    }


def build_messages(action, text, settings=None, **kw):
    return prepare_request(action, text, settings, **kw)["messages"]


def prepare_chat_context(text, settings=None, lang="zh"):
    """对话模式的上下文消息：system + 「这条记录作为参考资料」。"""
    lang = "en" if str(lang or "").lower().startswith("en") else "zh"
    body, truncated = truncate_text(text, AI_MAX_INPUT_CHARS)
    begin, end = AI_BLOCK[lang]
    user = AI_CHAT_CONTEXT[lang].format(begin=begin, body=body, end=end)
    return {
        "messages": [{"role": "system", "content": AI_SYSTEM[lang]},
                     {"role": "user", "content": user}],
        "truncated": truncated,
        "sent_chars": len(body),
        "total_chars": len(text or ""),
    }


def prepare_image_chat_context(image_path, lang="zh"):
    """图片记录的对话上下文：把图片本身作为参考资料。"""
    lang = "en" if str(lang or "").lower().startswith("en") else "zh"
    data_url, info = encode_image_data_url(image_path)
    note = ("下面这张图片是我从剪贴板里复制的，作为这次对话的参考资料。"
            "接下来我会继续提问，请结合它回答。"
            if lang == "zh" else
            "The image below is from my clipboard; use it as reference for "
            "this conversation. I will keep asking questions.")
    return {
        "messages": [
            {"role": "system", "content": AI_SYSTEM[lang]},
            {"role": "user", "content": [
                {"type": "text", "text": note},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "truncated": False, "sent_chars": 0, "total_chars": 0, "image": info,
    }


def build_chat_messages(context_messages, history, user_text,
                        max_turns=AI_CHAT_MAX_TURNS):
    """多轮对话请求：上下文 + 最近若干轮 + 本次提问（限制轮数，token 才可控）。"""
    msgs = list(context_messages or [])
    turns = list(history or [])
    if max_turns and max_turns > 0:
        turns = turns[-2 * int(max_turns):]
    msgs.extend(turns)
    msgs.append({"role": "user", "content": user_text or ""})
    return msgs


# ===========================================================================
# 图片 / 文件：多模态请求与文件清单
# ===========================================================================

def _fmt_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    if n < 0:
        return "?"
    for unit, step in (("GB", 1024.0 ** 3), ("MB", 1024.0 ** 2),
                       ("KB", 1024.0)):
        if n >= step:
            return "%.1f %s" % (n / step, unit)
    return "%d B" % int(n)


def encode_image_data_url(path, max_side=AI_IMAGE_MAX_SIDE,
                          quality=AI_IMAGE_QUALITY):
    """把本地图片压成长边 max_side 的 JPEG data URL；返回 (data_url, info)。"""
    if not path or not os.path.exists(path):
        raise AIError(ai_text("err_image_missing"), "image")
    import base64
    import io
    try:
        from PIL import Image
        with Image.open(path) as raw_img:
            img = raw_img.convert("RGB")
            src_w, src_h = img.size
            scale = min(1.0, float(max_side) / max(1, src_w, src_h))
            if scale < 1.0:
                img = img.resize((max(1, int(src_w * scale)),
                                  max(1, int(src_h * scale))),
                                 Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            out_size = (img.width, img.height)
        return ("data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"),
                {"bytes": len(data), "size": out_size,
                 "original": (src_w, src_h)})
    except AIError:
        raise
    except Exception:
        # 没有 Pillow 或图片异常：原样发送（大图可能被服务商拒，但至少能试）
        import mimetypes
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            data = f.read()
        return ("data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii")),
                {"bytes": len(data), "size": None, "original": None})


def prepare_image_request(action, image_path, settings=None,
                          custom_prompt="", lang="zh"):
    """组装图片分析请求（文本指令 + 图片），返回与 prepare_request 同结构。"""
    lang = "en" if str(lang or "").lower().startswith("en") else "zh"
    action = action if action in AI_IMAGE_PROMPT else "describe"
    if action == "custom":
        tip = (custom_prompt or "").strip() or AI_IMAGE_PROMPT["describe"][lang]
        instruction = AI_IMAGE_PROMPT[action][lang].format(instruction=tip)
    else:
        instruction = AI_IMAGE_PROMPT[action][lang]
    data_url, info = encode_image_data_url(image_path)
    user = instruction
    messages = [
        {"role": "system", "content": AI_SYSTEM[lang]},
        {"role": "user", "content": [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]
    return {"action": action, "messages": messages, "truncated": False,
            "sent_chars": len(user), "total_chars": len(user),
            "image": info, "target": ""}


def file_entries_text(entry, max_files=200):
    """把一条「文件」记录渲染成给模型看的清单文本（只有名称 / 格式 / 大小）。"""
    paths = list((entry or {}).get("file_paths") or [])
    sizes = list((entry or {}).get("file_sizes") or [])
    lines = []
    for i, path in enumerate(paths[:max_files]):
        name = os.path.basename(path) or path
        ext = os.path.splitext(path)[1].lstrip(".").upper() or "无后缀"
        size = sizes[i] if i < len(sizes) else -1
        lines.append("- %s  (%s, %s)" % (name, ext, _fmt_bytes(size)))
    if len(paths) > max_files:
        lines.append("…（共 %d 个文件，只列前 %d 个）" % (len(paths), max_files))
    total = sum(s for s in sizes if isinstance(s, (int, float)) and s > 0)
    head = "共 %d 个文件，合计 %s：" % (len(paths), _fmt_bytes(total) if total else "?")
    return head + "\n" + "\n".join(lines)


# ===========================================================================
# 客户端
# ===========================================================================

class AIError(Exception):
    """AI 请求错误；message 可以直接展示给用户。"""

    def __init__(self, message, kind="error", detail=""):
        super().__init__(message)
        self.kind = kind
        self.detail = detail


def _short_detail(body, limit=200):
    try:
        if isinstance(body, (bytes, bytearray)):
            text = body.decode("utf-8", "ignore")
        else:
            text = str(body or "")
    except Exception:
        text = ""
    return " ".join(text.split())[:limit]


def _extract_text(obj):
    """从一处 choices 里取正文（同时兼容 delta 与 message 两种形状）。"""
    if not isinstance(obj, dict):
        return ""
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    for holder in (choice.get("delta"), choice.get("message"), choice):
        if not isinstance(holder, dict):
            continue
        content = holder.get("content")
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            joined = "".join(part.get("text", "")
                             for part in content if isinstance(part, dict))
            if joined:
                return joined
    return ""


class AIClient:
    """OpenAI 兼容协议的聊天客户端（支持流式 / 非流式 / 取消 / 重试 / 代理）。"""

    def __init__(self, settings=None, lang="zh"):
        self.settings = dict(settings or {})
        self.lang = "en" if str(lang or "").lower().startswith("en") else "zh"
        self._resp = None
        self._resp_lock = threading.Lock()
        self._cancelled = threading.Event()
        self._emitted = 0

    # ---- 基本信息 ----
    def t(self, key, **kw):
        return ai_text(key, self.lang, **kw)

    @property
    def provider(self):
        pid = str(self.settings.get("provider") or "custom")
        return pid if pid in PROVIDERS else "custom"

    @property
    def base_url(self):
        return str(self.settings.get("base_url") or "").strip().rstrip("/")

    @property
    def model(self):
        return str(self.settings.get("model") or "").strip()

    @property
    def vision_model(self):
        return str(self.settings.get("vision_model") or "").strip()

    @property
    def is_local(self):
        """是不是在跟本机上的模型服务说话（Ollama / LM Studio 之类）。"""
        return is_local_url(self.base_url)

    @staticmethod
    def has_image(messages):
        """这轮请求里带没带图片。"""
        for msg in messages or []:
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                for part in content:
                    if (isinstance(part, dict)
                            and part.get("type") == "image_url"):
                        return True
        return False

    def model_for(self, messages):
        """识图请求优先用「识图模型」：本地免费方案经常是文本一个、识图一个。"""
        if self.vision_model and self.has_image(messages):
            return self.vision_model
        return self.model

    @property
    def api_key(self):
        return ai_api_key(self.settings)

    @property
    def needs_key(self):
        return bool(provider_info(self.provider).get("needs_key", True))

    def timeout(self):
        return max(10, _as_int(self.settings.get("timeout"),
                               AI_DEFAULT_TIMEOUT))

    def config_problem(self):
        """配置是否可用；不可用时返回提示 key，可用时返回 ""。"""
        if not self.base_url:
            return "need_base_url"
        if not self.model:
            return "need_model"
        if self.needs_key and not self.api_key:
            return "need_key"
        return ""

    def is_ready(self):
        return not self.config_problem()

    def endpoint(self, base=None):
        base = self.base_url if base is None else str(base or "").strip()
        if not base:
            return ""
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def endpoint_candidates(self):
        """要试的接口地址：本机地址连不上时，再拿 localhost ↔ 127.0.0.1 换个写法试。"""
        out = []
        for base in (self.base_url,
                     alternate_local_url(self.base_url) if self.is_local else ""):
            url = self.endpoint(base) if base else ""
            if url and url not in out:
                out.append(url)
        return out

    # ---- 请求 ----
    def _opener(self):
        proxy = str(self.settings.get("proxy") or "").strip()
        if proxy:
            return urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        return urllib.request.build_opener()

    def _headers(self, stream=True):
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "%s/%s" % (APP_NAME, APP_VERSION),
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        key = self.api_key
        if key:
            headers["Authorization"] = "Bearer " + key
        return headers

    def build_payload(self, messages, stream=True, max_tokens=None):
        body = {"model": self.model_for(messages),
                "messages": list(messages or []),
                "stream": bool(stream)}
        try:
            body["temperature"] = float(self.settings.get("temperature", 0.3))
        except (TypeError, ValueError):
            pass
        if max_tokens is None:
            max_tokens = _as_int(self.settings.get("max_tokens"),
                                 AI_DEFAULT_MAX_TOKENS)
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = AI_DEFAULT_MAX_TOKENS
        if max_tokens > 0:
            body["max_tokens"] = max_tokens
        return body

    @staticmethod
    def _alternate_payload(payload):
        """部分新模型只认 max_completion_tokens、不接受自定义 temperature。"""
        alt = dict(payload)
        if "max_tokens" in alt:
            alt["max_completion_tokens"] = alt.pop("max_tokens")
        alt.pop("temperature", None)
        return alt

    def _error_from_http(self, code, body):
        detail = _short_detail(body)
        if code in (401, 403):
            return AIError(self.t("err_auth"), "auth", detail)
        if code == 404:
            return AIError(self.t("err_notfound"), "notfound", detail)
        if code == 429:
            return AIError(self.t("err_rate"), "rate", detail)
        if code >= 500:
            return AIError(self.t("err_server", code=code), "server", detail)
        return AIError(self.t("err_bad_request", code=code,
                              detail=detail or "-"), "bad_request", detail)

    def _net_error(self, err):
        text = str(err)
        if isinstance(err, TimeoutError) or "timed out" in text.lower():
            return AIError(self.t("err_timeout", seconds=self.timeout()),
                           "timeout", text)
        if self.is_local:
            hint = self.t("err_local_offline", detail=text[:120])
            return AIError(hint, "network", text)
        return AIError(self.t("err_network", detail=text[:160]), "network",
                       text)

    def cancel(self):
        """从其它线程中断正在进行的请求（关掉响应，读取会立刻报错）。"""
        self._cancelled.set()
        with self._resp_lock:
            resp = self._resp
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def _require_ready(self):
        problem = self.config_problem()
        if problem:
            raise AIError(self.t(problem), "config")

    def chat(self, messages, max_tokens=None):
        """非流式请求，返回完整文本（「测试连接」用）。"""
        self._require_ready()
        payload = self.build_payload(messages, stream=False,
                                     max_tokens=max_tokens)
        variants = [payload]
        alt = self._alternate_payload(payload)
        if alt != payload:
            variants.append(alt)
        last = None
        for url in (self.endpoint_candidates() or [self.endpoint()]):
            for index, body in enumerate(variants):
                request = urllib.request.Request(
                    url, data=json.dumps(body).encode("utf-8"),
                    method="POST")
                for key, value in self._headers(stream=False).items():
                    request.add_header(key, value)
                try:
                    with self._opener().open(request,
                                             timeout=self.timeout()) as resp:
                        raw = resp.read()
                except urllib.error.HTTPError as err:
                    detail = err.read()[:400]
                    if err.code in (400, 422) and index + 1 < len(variants):
                        last = self._error_from_http(err.code, detail)
                        continue
                    raise self._error_from_http(err.code, detail)
                except (urllib.error.URLError, TimeoutError, OSError) as err:
                    last = self._net_error(err)
                    break               # 这个地址连不上：换下一个地址写法再试
                try:
                    data = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    raise AIError(self.t("err_bad_response",
                                         detail=_short_detail(raw)),
                                  "bad_response")
                text = _extract_text(data).strip()
                if text:
                    return text
                last = AIError(self.t("err_empty"), "empty")
        raise last or AIError(self.t("err_empty"), "empty")

    def test_connection(self):
        """最小成本验证配置：发一条 1 个字的请求。"""
        text = self.chat([{"role": "user", "content": "ping"}], max_tokens=8)
        return text

    def stream_chat(self, messages, on_delta=None, max_tokens=None):
        """流式请求：每段增量回调 on_delta(str)，返回完整文本。"""
        self._require_ready()
        tries = max(0, min(5, _as_int(self.settings.get("retries"),
                                      AI_DEFAULT_RETRIES)))
        last = None
        for attempt in range(tries + 1):
            if self._cancelled.is_set():
                raise AIError(self.t("err_cancelled"), "cancelled")
            try:
                return self._stream_once(messages, on_delta, max_tokens)
            except AIError as err:
                last = err
                if err.kind == "cancelled":
                    raise
                # 只在"一个字都没出来"且属于可重试错误时才重试，避免重复内容
                if err.kind not in ("network", "timeout", "server", "rate"):
                    raise
                if self._emitted:
                    raise
                if attempt >= tries:
                    raise
                time.sleep(min(3.0, 0.8 * (attempt + 1)))
        raise last or AIError(self.t("err_empty"), "empty")

    def _stream_once(self, messages, on_delta, max_tokens):
        payload = self.build_payload(messages, stream=True,
                                     max_tokens=max_tokens)
        variants = [payload]
        alt = self._alternate_payload(payload)
        if alt != payload:
            variants.append(alt)
        self._emitted = 0
        urls = self.endpoint_candidates() or [self.endpoint()]
        last = None
        for index, body in enumerate(variants):
            resp = None
            for url in urls:
                request = urllib.request.Request(
                    url, data=json.dumps(body).encode("utf-8"),
                    method="POST")
                for key, value in self._headers(stream=True).items():
                    request.add_header(key, value)
                try:
                    resp = self._opener().open(request, timeout=self.timeout())
                except urllib.error.HTTPError as err:
                    detail = err.read()[:400]
                    if err.code in (400, 422) and index + 1 < len(variants):
                        last = self._error_from_http(err.code, detail)
                        break             # 这个变体不行：换下一个 payload 变体
                    raise self._error_from_http(err.code, detail)
                except (urllib.error.URLError, TimeoutError, OSError) as err:
                    last = self._net_error(err)
                    continue              # 这个地址连不上：换下一个地址写法
                break
            if resp is None:
                continue
            with self._resp_lock:
                self._resp = resp
            parts = []
            try:
                for raw_line in resp:
                    if self._cancelled.is_set():
                        break
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line or line.startswith(":") \
                            or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data in ("[DONE]", "DONE"):
                        break
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and obj.get("error"):
                        raise AIError(self.t("err_provider", detail=_short_detail(
                            json.dumps(obj.get("error"), ensure_ascii=False))),
                            "provider")
                    delta = _extract_text(obj)
                    if delta:
                        parts.append(delta)
                        self._emitted += len(delta)
                        if on_delta is not None:
                            try:
                                on_delta(delta)
                            except Exception:
                                pass
            except (ValueError, OSError, urllib.error.URLError) as err:
                # cancel() 关掉响应会让读取抛错：这时按「用户停止」处理
                if not self._cancelled.is_set():
                    raise self._net_error(err)
            finally:
                with self._resp_lock:
                    self._resp = None
                try:
                    resp.close()
                except Exception:
                    pass
            text = "".join(parts).strip()
            if text:
                return text
            if self._cancelled.is_set():
                raise AIError(self.t("err_cancelled"), "cancelled")
            last = AIError(self.t("err_empty"), "empty")
        raise last or AIError(self.t("err_empty"), "empty")
