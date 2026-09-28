"""注册通过明确出站策略访问的公开来源。"""

from .base import get_adapter, registered_adapters
from . import ccgp_national, sx_jk_ecai, shxjkjt, csg_bidding, qinyuan, public_energy, yuneng

__all__ = ['get_adapter', 'registered_adapters']
