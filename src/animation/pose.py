"""姿态参数平滑与状态间过渡。

行为状态机是离散跳变的（游走 -> 惊吓 -> 梳理……），如果直接按状态切换绘制参数，
肢体就会「瞬移」。这里用指数平滑（等效于临界阻尼弹簧）把每个姿态参数连续地推向
目标值，使状态切换时肢体是"过渡"过去而不是"跳"过去。
"""

from __future__ import annotations
import math


class Spring:
    """指数平滑通道：value 以时间常数 tau 追赶 target。"""

    __slots__ = ("value", "target", "tau")

    def __init__(self, value: float = 0.0, tau: float = 0.15) -> None:
        self.value = value
        self.target = value
        self.tau = max(1e-3, tau)

    def step(self, dt: float) -> None:
        if dt <= 0.0:
            return
        k = 1.0 - math.exp(-dt / self.tau)
        self.value += (self.target - self.value) * k

    def snap(self, value: float) -> None:
        self.value = value
        self.target = value


class PoseBlender:
    """一组姿态参数，各自独立平滑。"""

    __slots__ = ("_channels",)

    def __init__(self, specs: dict[str, tuple[float, float]]) -> None:
        # specs: 参数名 -> (初始值, 时间常数)
        self._channels = {
            name: Spring(init, tau) for name, (init, tau) in specs.items()
        }

    def set_target(self, targets: dict[str, float]) -> None:
        for name, value in targets.items():
            ch = self._channels.get(name)
            if ch is not None:
                ch.target = value

    def step(self, dt: float) -> None:
        for ch in self._channels.values():
            ch.step(dt)

    def value(self, name: str, default: float = 0.0) -> float:
        ch = self._channels.get(name)
        return ch.value if ch is not None else default

    def target_of(self, name: str, default: float = 0.0) -> float:
        ch = self._channels.get(name)
        return ch.target if ch is not None else default

    def snap_all(self) -> None:
        for ch in self._channels.values():
            ch.snap(ch.target)
