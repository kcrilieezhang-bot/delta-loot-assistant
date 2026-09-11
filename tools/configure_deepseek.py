"""Explicit local credential setup. Never pass a key as a command-line argument.

Interactive terminals use getpass; a non-echoing pipe accepts one line on stdin.
--test-once makes one bounded synthetic request, never uploads a user inventory.
"""
import argparse
import getpass
import json
import sys

from delta_loot_assistant.deepseek_provider import ModelRequestError, deepseek_generate
from delta_loot_assistant.llm_settings import LlmSettings
from delta_loot_assistant.paths import user_data_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-once", action="store_true")
    args = parser.parse_args()
    print("Ready for secret input; no credential will be printed.", flush=True)
    key = getpass.getpass("DeepSeek key: ") if sys.stdin.isatty() else sys.stdin.readline().strip()
    if not key:
        print("No key supplied; no settings changed or API call sent.")
        return 2
    settings = LlmSettings(user_data_dir() / "web-review")
    config = settings.save({"provider": "deepseek", "model": "deepseek-v4-flash", "api_key": key})
    del key
    print(json.dumps(config), flush=True)
    if args.test_once:
        evidence = [{"id": "rule:test", "title": "缺价处理", "text": "没有报价的物品不能当作零元。",
                     "source": "合成联调样例，不含用户数据"}]
        try:
            result = deepseek_generate("用一句话解释缺价怎么处理。", evidence,
                                       config["model"], settings.tokens.load())
        except ModelRequestError as exc:
            print(json.dumps({"test": "failed", "error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps({"test": "passed", "usage": result["usage"],
                          "evidence_ids": result["evidence_ids"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
