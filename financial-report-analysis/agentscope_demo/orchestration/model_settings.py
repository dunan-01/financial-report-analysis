"""OpenAI兼容模型配置；企业接口与旧OpenRouter配置按整组选择。"""
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    stream: bool = False


def normalize_base_url(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("模型接口地址须为http/https地址，且不包含查询参数或片段")
    if parsed.username or parsed.password:
        raise ValueError("模型接口地址不能包含用户名或密码；请使用API Key配置")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def load_model_settings():
    # 一旦设置任意企业配置，就不能从旧配置借用地址、Key或模型。
    enterprise = any(name in os.environ for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_STREAM"))
    if enterprise:
        values = {name: os.getenv(name, "").strip() for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError("企业模型配置缺少 " + "、".join(missing) + "。请在.env中补齐。")
        base_url, model, api_key = (values[name] for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"))
        stream_value = os.getenv("LLM_STREAM", "false").strip().lower()
    else:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("请配置LLM_BASE_URL、LLM_MODEL、LLM_API_KEY；旧配置缺少 OPENROUTER_API_KEY。或使用 --mode offline。")
        base_url = os.getenv("OPENROUTER_BASE_URL", "").strip() or "https://openrouter.ai/api/v1"
        model = os.getenv("OPENROUTER_MODEL", "").strip() or "openrouter/free"
        stream_value = "true"
    if stream_value not in {"true", "false"}:
        raise ValueError("LLM_STREAM只能设置为true或false")
    return ModelSettings(normalize_base_url(base_url), model, api_key, stream_value == "true")
