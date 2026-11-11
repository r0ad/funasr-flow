"""识别引擎插件层。

新增引擎：在自己的模块里 `@register` 一个带 `transcribe/embed` 的类，
然后在 `registry.load_builtin()` 里 import 一次即可，无需改其他代码。
"""
