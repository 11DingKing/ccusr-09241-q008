"""领域与应用的统一错误类型。"""

from __future__ import annotations


class DomainError(Exception):
    """业务规则错误基类，接口层会将其转换为可读的失败响应。"""


class ValidationError(DomainError):
    """输入数据不满足领域约束。"""


class NotFoundError(DomainError):
    """引用的版本、批次、方案或报告不存在。"""


class CyclicDependencyError(ValidationError):
    """前置依赖关系构成环。"""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = list(cycle)
        super().__init__("检测到循环前置依赖: " + " -> ".join(self.cycle))


class ImmutableVersionError(DomainError):
    """试图改写已经存在的假设版本。"""


class CommitmentConflictError(DomainError):
    """已承诺阶段与当前假设版本冲突（例如工程或阶段已被移除）。"""


class SearchSpaceTooLargeError(DomainError):
    """组合搜索空间超出安全上限。"""
