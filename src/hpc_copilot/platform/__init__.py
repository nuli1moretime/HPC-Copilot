"""平台适配层。"""

from .base import PlatformAdapter, JobInfo
from .rest import RestPlatformAdapter

__all__ = ["PlatformAdapter", "JobInfo", "RestPlatformAdapter"]
