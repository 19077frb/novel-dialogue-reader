"""模型接入层：适配器契约、凭据服务与配置（T06 定义契约，T07 实现真实适配器）。"""

from __future__ import annotations

from .adapter import (
    PROTOCOL_CAPABILITIES,
    AdapterCapabilities,
    ConnectionTestResult,
    ProviderAdapter,
    TokenEstimate,
    UsageRecord,
    resolve_capabilities,
)
from .credentials import (
    CredentialSaveResult,
    CredentialService,
    CredentialStore,
    CredentialStoreError,
    SessionCredentialStore,
    SystemCredentialStore,
)
from .profiles import (
    ProfileView,
    create_profile,
    delete_profile,
    get_profile_or_404,
    is_profile_referenced,
    list_profiles,
    update_profile,
)

__all__ = [
    "PROTOCOL_CAPABILITIES",
    "AdapterCapabilities",
    "ConnectionTestResult",
    "CredentialSaveResult",
    "CredentialService",
    "CredentialStore",
    "CredentialStoreError",
    "ProfileView",
    "ProviderAdapter",
    "SessionCredentialStore",
    "SystemCredentialStore",
    "TokenEstimate",
    "UsageRecord",
    "create_profile",
    "delete_profile",
    "get_profile_or_404",
    "is_profile_referenced",
    "list_profiles",
    "resolve_capabilities",
    "update_profile",
]
