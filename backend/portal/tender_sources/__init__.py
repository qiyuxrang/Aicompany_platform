"""仅注册四个已冻结的公开来源。"""

from .base import get_adapter, registered_adapters
from . import ccgp_national, sx_jk_ecai, shxjkjt, csg_bidding

__all__ = ['get_adapter', 'registered_adapters']
