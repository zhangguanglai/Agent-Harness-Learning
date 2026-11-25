"""租户配额：并发 / 每日运行数 / 每日 token。

配额判断只读数据库，不依赖内存计数——多副本部署时才能一致。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


class QuotaExceeded(RuntimeError):
    def __init__(self, message: str, *, retry_after_s: int = 60):
        super().__init__(message)
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class QuotaPolicy:
    max_concurrent: int = 3
    daily_runs: int = 200
    daily_tokens: int = 2_000_000

    def describe(self) -> dict:
        return {
            "max_concurrent": self.max_concurrent,
            "daily_runs": self.daily_runs,
            "daily_tokens": self.daily_tokens,
        }


def _day_start() -> float:
    now = time.localtime()
    start = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    return start


def usage(store, tenant: str) -> dict:
    since = _day_start()
    return {
        "tenant": tenant,
        "active": store.count_active(tenant),
        "runs_today": store.count_runs_since(tenant, since),
        "tokens_today": store.sum_tokens_since(tenant, since),
        "since": since,
    }


def check(store, tenant: str, policy: QuotaPolicy) -> dict:
    current = usage(store, tenant)
    if current["active"] >= policy.max_concurrent:
        raise QuotaExceeded(
            f"租户 {tenant} 并发运行已达上限 {policy.max_concurrent}，请等待已有任务结束",
            retry_after_s=30,
        )
    if current["runs_today"] >= policy.daily_runs:
        raise QuotaExceeded(
            f"租户 {tenant} 今日运行数已达上限 {policy.daily_runs}",
            retry_after_s=3600,
        )
    if current["tokens_today"] >= policy.daily_tokens:
        raise QuotaExceeded(
            f"租户 {tenant} 今日 token 已达上限 {policy.daily_tokens}",
            retry_after_s=3600,
        )
    return current
