"""路由依赖：数据库会话与请求上下文。"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Iterator[Session]:
    """请求级会话；不在这里开启长事务，也不在事务中等待网络响应。"""

    factory = request.app.state.session_factory
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()
