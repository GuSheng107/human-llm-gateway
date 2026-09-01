"""平台工具持久化。"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.platform_tools import DEFAULT_PLATFORM_TOOLS
from .models import ToolWhitelist


class ToolRepository:
    """平台工具白名单查询与默认数据写入。"""

    def seed_default_platform_tools(self, session: Session) -> None:
        existing_names = set(session.scalars(select(ToolWhitelist.name)).all())
        for definition in DEFAULT_PLATFORM_TOOLS:
            if definition.name in existing_names:
                continue
            session.add(
                ToolWhitelist(
                    name=definition.name,
                    description=definition.description,
                    command_template=definition.command_template,
                    arguments_schema_json=json.dumps(
                        definition.arguments_schema,
                        ensure_ascii=False,
                    ),
                    timeout_seconds=definition.timeout_seconds,
                    is_enabled=True,
                )
            )
