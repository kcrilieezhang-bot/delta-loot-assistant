"""Local model preferences and isolated Windows-encrypted DeepSeek credentials."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .secrets import DpapiTokenStore

DEEPSEEK_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}


class DeepSeekTokenStore(DpapiTokenStore):
    ENVIRONMENT_VARIABLE = "DELTA_LOOT_DEEPSEEK_KEY"


class LlmSettings:
    def __init__(self, root: Path, token_store=None):
        self.path = root / "llm-settings.json"
        self.tokens = token_store or DeepSeekTokenStore(root / "deepseek-token.dpapi")
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            config = {"provider": "disabled", "model": "deepseek-v4-flash"}
            if self.path.is_file():
                config.update(json.loads(self.path.read_text(encoding="utf-8")))
            # Existence check only: never decrypt a key merely to render the page.
            config["key_configured"] = self.tokens.path.is_file()
            return {key: config[key] for key in ("provider", "model", "key_configured")}

    def save(self, data):
        provider, model = data.get("provider"), data.get("model", "")
        if provider not in {"disabled", "deepseek", "ollama"}:
            raise ValueError("请选择支持的模型服务")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", model):
            raise ValueError("模型名称无效")
        if provider == "deepseek" and model not in DEEPSEEK_MODELS:
            raise ValueError("请选择支持的 DeepSeek 模型")
        key = data.get("api_key", "")
        if not isinstance(key, str) or (key and not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,200}", key)):
            raise ValueError("密钥格式不正确")
        with self.lock:
            if key:
                self.tokens.save(key)
            config = {"provider": provider, "model": model}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(config), encoding="utf-8")
            temporary.replace(self.path)
            return self.read()
