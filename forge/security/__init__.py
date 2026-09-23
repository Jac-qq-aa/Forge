"""Forge 身份认证与租户流量治理。"""

from .auth import Principal, require_admin, require_principal

__all__ = ["Principal", "require_admin", "require_principal"]
