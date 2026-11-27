"""Route modules, each owning one resource.

这里只声明包，不在导入期再导入子模块，避免与 ``main`` 形成循环依赖。
"""

__all__: list[str] = []
