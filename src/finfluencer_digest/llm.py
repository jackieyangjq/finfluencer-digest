"""调用 Gemini：按顺序尝试备选模型，繁忙时等待重试，并记下每个模型的用量。
所有对 Gemini 的请求都经过 Gemini._call(model, **kwargs)，测试可以替换它。
"""
from __future__ import annotations

import functools
import logging
import time
from collections import defaultdict

from google import genai

logging.getLogger("google_genai.models").setLevel(logging.ERROR)  # 屏蔽与本程序无关的库提示


def short_error(e: Exception) -> str:
    code = getattr(e, "code", None)
    if code == 429:
        return "超出 Gemini 的额度或频率限制（429）"
    if code in (500, 502, 503, 504):
        return f"Gemini 服务暂时繁忙（{code}）"
    if code:
        return f"Gemini 拒绝处理（{code}）：{getattr(e, 'message', '') or str(e)[:150]}"
    return f"{type(e).__name__}: {str(e)[:150]}"


def with_retry(fn, attempts: int = 4, *, sleep=time.sleep):
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            code = getattr(e, "code", None)
            if i == attempts - 1 or code not in (None, 500, 502, 503, 504):
                raise
            wait = 30 * 2 ** i
            print(f"    出错（{code or type(e).__name__}），{wait} 秒后重试…", flush=True)
            sleep(wait)


class Gemini:
    def __init__(self, api_key: str, *, sleep=time.sleep):
        self._client = genai.Client(api_key=api_key)
        self._call = lambda model, **kw: self._client.models.generate_content(model=model, **kw)
        self._sleep = sleep
        self.usage = defaultdict(lambda: [0, 0, 0])  # 模型 → [调用次数, 输入 tokens, 输出 tokens（含思考）]

    def generate(self, models: list[str], *, label: str = "", **kwargs):
        """按顺序尝试模型：繁忙时重试 2 次，额度用完或仍失败就换下一个。返回（结果，实际用的模型）。
        label 标明是哪一次调用（如 video:<视频 id>），只有录制回放用得到，这里不用。"""
        last_error = None
        for model in dict.fromkeys(models):
            try:
                return with_retry(functools.partial(self._call, model, **kwargs), attempts=3, sleep=self._sleep), model
            except Exception as e:
                last_error = e
                print(f"    模型 {model} 失败：{short_error(e)}", flush=True)
        raise last_error

    def ask(self, models: list[str], *, label: str = "", **kwargs):
        """同 generate，并自动记用量；只返回结果。"""
        resp, model = self.generate(models, label=label, **kwargs)
        self.add_usage(model, resp.usage_metadata)
        return resp

    def add_usage(self, model: str, meta) -> None:
        u = self.usage[model]
        u[0] += 1
        u[1] += meta.prompt_token_count or 0
        u[2] += (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)

    def usage_text(self) -> str:
        parts = [f"{m} 调用 {n} 次（输入约 {i / 1e4:.1f} 万、输出约 {o / 1e4:.1f} 万 tokens）"
                 for m, (n, i, o) in self.usage.items()]
        return "今日 Gemini 用量：" + "；".join(parts) if parts else ""
