"""Storage layer — Redis dedup/cache, SQL audit + approvals."""

from kairos.storage.approvals import ApprovalStore, make_approval_id
from kairos.storage.audit_store import AuditStore, JSONLogAuditStore, audit_store_from_settings
from kairos.storage.db import Database
from kairos.storage.dedup import DedupKind, DedupStore, dedup_key
from kairos.storage.redis_client import RedisClient, get_redis
from kairos.storage.sql_audit_store import SQLAuditStore

__all__ = [
    "ApprovalStore",
    "AuditStore",
    "Database",
    "DedupKind",
    "DedupStore",
    "JSONLogAuditStore",
    "RedisClient",
    "SQLAuditStore",
    "audit_store_from_settings",
    "dedup_key",
    "get_redis",
    "make_approval_id",
]
