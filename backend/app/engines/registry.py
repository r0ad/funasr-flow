"""引擎注册表：按 id 查找可用实现。"""

from __future__ import annotations

from .base import AsrEngine

_ENGINES: dict[str, type] = {}
# 引擎实例缓存：模型加载一次要十几秒，worker 串行处理，复用实例即可
_INSTANCES: dict[str, AsrEngine] = {}


def register(engine_cls: type) -> type:
    _ENGINES[engine_cls.name] = engine_cls
    return engine_cls


def get_engine(engine_id: str) -> AsrEngine:
    """取引擎实例（同一 id 复用，避免每个任务都重新加载模型）。"""
    inst = _INSTANCES.get(engine_id)
    if inst is not None:
        return inst
    cls = _ENGINES.get(engine_id)
    if cls is None:
        raise KeyError(f"未知引擎: {engine_id}（可用: {', '.join(_ENGINES)}）")
    inst = cls()
    _INSTANCES[engine_id] = inst
    return inst


def release_engine(engine_id: str) -> None:
    """卸载某个引擎（释放显存）；不存在时静默返回。"""
    inst = _INSTANCES.pop(engine_id, None)
    if inst is not None:
        try:
            inst.release()
        except Exception:  # noqa: BLE001 - 卸载失败不该影响调用方
            pass


def reset_engines() -> None:
    """测试用：清空实例缓存。"""
    _INSTANCES.clear()


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
