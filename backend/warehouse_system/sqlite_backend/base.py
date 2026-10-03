"""自定义 SQLite 引擎。

与官方引擎唯一的差异：写事务以 BEGIN IMMEDIATE 启动，
进入事务即获取保留锁（reserved lock）并在需要写时排队升级，
避免 deferred 事务先读后写时两连接互相持有的死锁。
"""
from django.db.backends.sqlite3 import base


class DatabaseWrapper(base.DatabaseWrapper):
    def _start_transaction_under_autocommit(self):
        self.cursor().execute("BEGIN IMMEDIATE")
