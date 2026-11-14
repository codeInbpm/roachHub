"""数学与向量辅助函数库。"""

from __future__ import annotations
import math
import random


class Vector2D:
    """轻量级二维向量类。"""

    __slots__ = ("x", "y")

    def __init__(self, x: float = 0.0, y: float = 0.0) -> None:
        self.x = float(x)
        self.y = float(y)

    def __repr__(self) -> str:
        return f"Vector2D({self.x:.2f}, {self.y:.2f})"

    def __add__(self, other: Vector2D) -> Vector2D:
        return Vector2D(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vector2D) -> Vector2D:
        return Vector2D(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> Vector2D:
        return Vector2D(self.x * scalar, self.y * scalar)

    def __rmul__(self, scalar: float) -> Vector2D:
        return self.__mul__(scalar)

    def __truediv__(self, scalar: float) -> Vector2D:
        if scalar == 0:
            return Vector2D(0.0, 0.0)
        return Vector2D(self.x / scalar, self.y / scalar)

    def length_squared(self) -> float:
        """模长的平方。"""
        return self.x * self.x + self.y * self.y

    def length(self) -> float:
        """模长。"""
        return math.hypot(self.x, self.y)

    def normalized(self) -> Vector2D:
        """返回归一化单位向量。"""
        l = self.length()
        if l < 1e-6:
            return Vector2D(0.0, 0.0)
        return Vector2D(self.x / l, self.y / l)

    def angle(self) -> float:
        """获取当前向量在平面上的弧度角 (-pi 到 pi)。"""
        return math.atan2(self.y, self.x)

    def distance_to(self, other: Vector2D) -> float:
        """计算两点间欧式距离。"""
        return math.hypot(self.x - other.x, self.y - other.y)

    @classmethod
    def from_polar(cls, length: float, angle_rad: float) -> Vector2D:
        """根据极坐标（长度和弧度）构造向量。"""
        return cls(length * math.cos(angle_rad), length * math.sin(angle_rad))

    @classmethod
    def random_direction(cls) -> Vector2D:
        """生成随机方向的单位向量。"""
        theta = random.uniform(-math.pi, math.pi)
        return cls.from_polar(1.0, theta)


def clamp(val: float, min_val: float, max_val: float) -> float:
    """将数值限制在 [min_val, max_val] 区间内。"""
    return max(min_val, min(val, max_val))


def lerp(a: float, b: float, t: float) -> float:
    """线性插值。"""
    return a + (b - a) * clamp(t, 0.0, 1.0)


def normalize_angle(angle: float) -> float:
    """将任意角度规范化至 [-pi, pi] 区间。"""
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def angle_diff(from_angle: float, to_angle: float) -> float:
    """计算从 from_angle 旋转至 to_angle 的最短角位移 (-pi 到 pi)。"""
    diff = normalize_angle(to_angle - from_angle)
    return diff


def lerp_angle(from_angle: float, to_angle: float, t: float) -> float:
    """平滑角度插值，走最短路径。"""
    diff = angle_diff(from_angle, to_angle)
    return normalize_angle(from_angle + diff * clamp(t, 0.0, 1.0))
