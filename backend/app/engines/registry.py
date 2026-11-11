"""引擎注册表：按 id 查找可用实现。"""

from __future__ import annotations

from .base import AsrEngine

_ENGINES: dict[str, type] = {}


def register(engine_cls: type) -> type:
    _ENGINES[engine_cls.name] = engine_cls
    return engine_cls


def get_engine(engine_id: str) -> AsrEngine:
    cls = _ENGINES.get(engine_id)
    if cls is None:
        raise KeyError(f"未知引擎: {engine_id}（可用: {', '.join(_ENGINES)}）")
    return cls()


def list_engines() -> list[dict]:
    out = []
    for name, cls in _ENGINES.items():
        ok, reason = cls.available()
        out.append(
            {"id": name, "label": getattr(cls, "label", name), "available": ok, "reason": reason}
        )
    return out


def load_builtin() -> None:
    """导入内置引擎以触发注册。"""
    from . import funasr_engine  # noqa: F401
