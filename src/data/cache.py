"""
本地数据缓存模块。

将 Bloomberg 下载的历史数据按 ticker + fields + 日期范围缓存为 pickle，
避免重复拉取，提高回测迭代速度。
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


class DataCache:
    """基于本地文件系统的历史数据缓存。"""

    def __init__(self, cache_dir: Path | str) -> None:
        """
        Args:
            cache_dir: 缓存目录路径。
        """
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, ticker: str, fields: List[str], start: str, end: str) -> str:
        """生成缓存 key（hash）。"""
        content = json.dumps(
            {"ticker": ticker, "fields": sorted(fields), "start": start, "end": end},
            sort_keys=True,
        )
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.pkl"

    def exists(self, ticker: str, fields: List[str], start: str, end: str) -> bool:
        """检查缓存是否存在。"""
        return self._path(self._key(ticker, fields, start, end)).exists()

    def get(
        self,
        ticker: str,
        fields: List[str],
        start: str,
        end: str,
    ) -> Optional[pd.DataFrame]:
        """
        读取缓存数据。

        Returns:
            缓存命中返回 DataFrame，否则返回 None。
        """
        path = self._path(self._key(ticker, fields, start, end))
        if not path.exists():
            return None
        with open(path, "rb") as f:
            df = pickle.load(f)
        # 确保索引为日期
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        return df

    def set(
        self,
        ticker: str,
        fields: List[str],
        start: str,
        end: str,
        df: pd.DataFrame,
    ) -> None:
        """写入缓存。"""
        path = self._path(self._key(ticker, fields, start, end))
        df_out = df.copy()
        if not isinstance(df_out.index, pd.DatetimeIndex):
            df_out.index = pd.to_datetime(df_out.index)
        with open(path, "wb") as f:
            pickle.dump(df_out, f)

    def clear(self) -> None:
        """清空所有缓存。"""
        for f in self.cache_dir.glob("*.pkl"):
            f.unlink()

    def summary(self) -> Dict[str, Any]:
        """返回缓存摘要信息。"""
        files = list(self.cache_dir.glob("*.pkl"))
        total_size = sum(f.stat().st_size for f in files)
        return {
            "cache_dir": str(self.cache_dir),
            "file_count": len(files),
            "total_size_bytes": total_size,
        }
