"""OpenAI 兼容接口客户端：流式 chat completions + 连通性测试。

只依赖 httpx，不引 openai SDK；任何 base_url/api_key/model 组合都能用。
"""
from __future__ import annotations

import json
from typing import Dict, Iterator, List, Optional

import httpx


class LLMError(Exception):
    """接口调用失败（网络/鉴权/限流/响应格式）。message 面向用户可读。"""


def _chat_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def _friendly_http_error(status: int, body: str) -> str:
    hints = {
        401: "API Key 无效或未填写",
        402: "账户余额不足",
        403: "无权限访问该模型（403）",
        404: "接口地址或模型名不存在（404）",
        429: "触发限流，请稍后重试（429）",
    }
    hint = hints.get(status, f"接口返回 HTTP {status}")
    detail = body.strip()[:200]
    return f"{hint}" + (f"：{detail}" if detail else "")


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.3,
        timeout: float = 60.0,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def stream_chat(self, messages: List[Dict[str, str]]) -> Iterator[str]:
        """流式返回增量文本片段。异常统一抛 LLMError。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,
        }
        try:
            with httpx.stream(
                "POST",
                _chat_url(self.base_url),
                headers=self._headers(),
                json=payload,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
            ) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", errors="replace")
                    raise LLMError(_friendly_http_error(resp.status_code, body))
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
        except LLMError:
            raise
        except httpx.ConnectTimeout:
            raise LLMError("连接接口超时，请检查接口地址和网络")
        except httpx.ReadTimeout:
            raise LLMError("等待模型响应超时")
        except httpx.HTTPError as e:
            raise LLMError(f"网络错误：{e.__class__.__name__}: {e}")

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """非流式，一次拿完整回复（用于连通性测试等短请求）。"""
        return "".join(self.stream_chat(messages))

    def test_connection(self) -> str:
        """连通性测试：让模型回一个词。成功返回回复内容，失败抛 LLMError。"""
        reply = self.chat(
            [
                {"role": "system", "content": "You are a ping responder. Reply with the single word: pong"},
                {"role": "user", "content": "ping"},
            ]
        )
        if not reply.strip():
            raise LLMError("接口连通但返回了空内容")
        return reply.strip()


def client_from_config(cfg) -> LLMClient:
    """从 Config 对象构建客户端。cfg: config.Config"""
    provider = cfg.get("provider", {})
    base_url = (provider.get("base_url") or "").strip()
    api_key = (provider.get("api_key") or "").strip()
    model = (provider.get("model") or "").strip()
    if not base_url or not model:
        raise LLMError("请先在设置里填写接口地址和模型名")
    if not api_key:
        raise LLMError("请先在设置里填写 API Key")
    return LLMClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=float(provider.get("temperature", 0.3)),
        timeout=float(provider.get("timeout", 60)),
    )
