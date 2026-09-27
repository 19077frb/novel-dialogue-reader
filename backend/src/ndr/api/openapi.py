"""把契约中的通用模型注册进 OpenAPI components。

前端类型由 ``docs/openapi.json`` 生成，因此 ``DataEnvelope``/``ErrorEnvelope``/``CursorPage``
这些目前还没有具体路由引用的模型也必须出现在契约里。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from ..domain.common import CursorPage, DataEnvelope, ErrorBody, ErrorEnvelope

GENERIC_MODELS: tuple[type[Any], ...] = (DataEnvelope, ErrorEnvelope, ErrorBody, CursorPage)


def install_openapi(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        components: dict[str, Any] = schema.setdefault("components", {}).setdefault("schemas", {})

        for model in GENERIC_MODELS:
            model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, definition in model_schema.pop("$defs", {}).items():
                components.setdefault(name, definition)
            components.setdefault(model.__name__, model_schema)

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
