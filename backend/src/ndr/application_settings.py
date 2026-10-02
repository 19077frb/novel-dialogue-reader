"""Restart-only application preferences, independent of the selected library path."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from pydantic_settings import EnvSettingsSource

from .capacity_settings import BYTES_PER_MB, CAPACITY_KEYS, capacity_source, mb_to_bytes

if TYPE_CHECKING:
    from .config import Settings

_lock = threading.Lock()


class SettingDefinition(BaseModel):
    key: str
    label: str
    description: str
    group: str
    kind: str = "number"
    minimum: float | None = None
    maximum: float | None = None
    options: list[str] = []


def _field(key, label, description, group, kind="number", minimum=None, maximum=None, options=()):
    return SettingDefinition(
        key=key,
        label=label,
        description=description,
        group=group,
        kind=kind,
        minimum=minimum,
        maximum=maximum,
        options=list(options),
    )


DEFINITIONS = [
    _field(
        "environment",
        "运行环境名称",
        "用于健康检查的环境标识，不改变模型或阅读行为。",
        "服务与书库",
        "text",
    ),
    _field(
        "host",
        "监听地址",
        "本地阅读使用127.0.0.1或localhost；界面不允许开放公网监听。",
        "服务与书库",
        "text",
    ),
    _field(
        "port",
        "服务端口",
        "重启后需打开新端口的网址；若端口被占用，程序会提示启动失败。",
        "服务与书库",
        minimum=1,
        maximum=65535,
    ),
    _field(
        "cors_origins",
        "允许访问接口的网页来源",
        "每行一个http或https网址（只含协议、主机、端口）。一般保持默认，不要添加不可信网站。",
        "服务与书库",
        "lines",
    ),
    _field(
        "data_dir",
        "书库数据目录",
        "书籍、数据库和日志的目录。修改不搬迁数据，重启可能显示空书架；请先备份，或指定已有书库目录。",
        "服务与书库",
        "text",
    ),
    _field(
        "static_dir",
        "网页文件目录",
        "源码版可指定已构建的前端目录，留空不提供网页。免安装版使用内置页面，不可修改。",
        "服务与书库",
        "text",
    ),
    _field(
        "auto_migrate",
        "启动时自动升级数据库",
        "源码版可自动执行数据库升级。免安装版由启动器负责备份和升级，此项固定关闭。",
        "服务与书库",
        "boolean",
    ),
    _field(
        "credential_backend",
        "模型密钥保存方式",
        "system：系统凭据库（推荐）；session：仅本次进程，重启后需要重新填写密钥。切换不会搬迁旧密钥。",
        "凭据",
        "select",
        options=("system", "session"),
    ),
    _field(
        "llm_timeout_seconds",
        "模型请求超时（秒）",
        "单次模型请求的等待时间。推理较慢的模型可适当调大；不等同于Token额度。",
        "模型调用与恢复",
        minimum=0.1,
        maximum=86400,
    ),
    _field(
        "rate_limit_max_retries",
        "限流或临时不可用的重试次数",
        "不包含首次请求；不是模型输出校验失败重试或对白复核次数。",
        "模型调用与恢复",
        minimum=0,
        maximum=20,
    ),
    _field(
        "rate_limit_backoff_base_seconds",
        "重试初始等待（秒）",
        "遇到限流等临时错误后按退避规则等待，避免连续请求。",
        "模型调用与恢复",
        minimum=1,
        maximum=86400,
    ),
    _field(
        "rate_limit_backoff_max_seconds",
        "重试最大等待（秒）",
        "退避等待的上限，不能小于初始等待时间。",
        "模型调用与恢复",
        minimum=1,
        maximum=86400,
    ),
    _field(
        "stale_run_lease_seconds",
        "未完成请求的恢复等待（秒）",
        "重启时，超过这个时间仍没有保存结果的尝试会标为结果未知，避免误当成成功。",
        "模型调用与恢复",
        minimum=1,
        maximum=604800,
    ),
    _field(
        "recover_on_startup",
        "启动时检查遗留任务",
        "修复上次退出后遗留的任务状态，不代表自动重新调用模型。推荐开启。",
        "模型调用与恢复",
        "boolean",
    ),
    _field(
        "max_import_bytes",
        "导入文件大小上限（字节）",
        "单个上传文件的最大大小；1048576字节等于1 MiB。过大文件会占用更多内存和磁盘。",
        "导入与EPUB安全限制",
        minimum=1,
        maximum=10737418240,
    ),
    _field(
        "max_epub_entries",
        "EPUB内部文件数上限",
        "限制压缩包内的文件数量，防止异常或恶意文件消耗资源。",
        "导入与EPUB安全限制",
        minimum=1,
        maximum=1000000,
    ),
    _field(
        "max_epub_total_uncompressed_bytes",
        "EPUB解压总大小上限（字节）",
        "限制全部解压内容的总大小，不是原始上传文件大小。",
        "导入与EPUB安全限制",
        minimum=1,
        maximum=53687091200,
    ),
    _field(
        "max_epub_entry_bytes",
        "EPUB单个内部文件大小上限（字节）",
        "限制解压后每个内部文件的大小，不能超过解压总大小上限。",
        "导入与EPUB安全限制",
        minimum=1,
        maximum=10737418240,
    ),
    _field(
        "max_epub_spine_items",
        "EPUB正文文档数上限",
        "限制书籍阅读顺序中的正文文档数量，并非最终拆分出的章节数。",
        "导入与EPUB安全限制",
        minimum=1,
        maximum=1000000,
    ),
    _field(
        "allow_fake_provider",
        "启用离线测试提供方",
        "仅用于测试，不发送真实模型请求，也不代表模型能力。普通阅读请保持关闭。",
        "离线测试（高级）",
        "boolean",
    ),
    _field(
        "fake_provider_labels",
        "离线测试标注方式",
        "unknown：全部待确认；deterministic：生成确定性测试标注。仅对已启用的测试提供方生效。",
        "离线测试（高级）",
        "select",
        options=("unknown", "deterministic"),
    ),
    _field(
        "fake_provider_script",
        "离线测试错误脚本",
        "空白：不模拟错误；其他选项只模拟一次对应错误，用于验证重试和提示。",
        "离线测试（高级）",
        "select",
        options=("", "rate_limited_once", "unavailable_once", "timeout_once", "auth_failed_once"),
    ),
]


class ApplicationField(SettingDefinition):
    value: JsonValue
    current_value: JsonValue
    default_value: JsonValue
    locked_reason: str | None = None


class ApplicationSettingsOut(BaseModel):
    fields: list[ApplicationField]
    revision: str
    config_path: str
    restart_required: list[str]
    restart_blocked_reason: str | None = "当前启动方式需要手动重启服务。"


class ApplicationSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, JsonValue]
    revision: str


def settings_path() -> Path:
    from .config import default_data_dir

    # Never follow data_dir: a directory change must not hide its own configuration.
    return default_data_dir() / "application-settings.json"


def _read() -> tuple[dict[str, JsonValue], str]:
    path = settings_path()
    if not path.exists():
        return {}, "missing"
    if path.stat().st_size > 65536:
        raise ValueError(f"应用配置文件过大，请检查：{path}")
    try:
        raw = path.read_bytes()
        values = json.loads(raw)
        if not isinstance(values, dict) or set(values) - (
            {field.key for field in DEFINITIONS} | set(CAPACITY_KEYS.values())
        ):
            raise ValueError("包含未知配置项")
        for byte_key, mb_key in CAPACITY_KEYS.items():
            if mb_key in values:
                values[byte_key] = mb_to_bytes(values.pop(mb_key), mb_key)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"应用配置文件损坏，请检查或还原：{path}") from exc
    return values, hashlib.sha256(raw).hexdigest()


def read_saved_settings() -> dict[str, JsonValue]:
    return _read()[0]


def _locks(settings: Settings) -> dict[str, str]:
    locks = {}
    env_source = EnvSettingsSource(type(settings))
    source = capacity_source(env_source)()
    for key in type(settings).model_fields:
        if key in source and key not in settings._internal_environment_keys:
            mb_key = CAPACITY_KEYS.get(key)
            variable = mb_key if mb_key and f"ndr_{mb_key}" in env_source.env_vars else key
            locks[key] = (
                f"由启动环境变量 NDR_{variable.upper()} 指定；移除该变量并重启后才能在此修改。"
            )
    locks.update(settings._startup_locks)
    return locks


def describe_settings(settings: Settings) -> ApplicationSettingsOut:
    saved, revision = _read()
    current = settings.model_dump(mode="json")
    # Construct defaults without reading environment variables, .env or saved preferences.
    defaults = type(settings).model_construct().model_dump(mode="json")
    locks = _locks(settings)
    fields = [
        ApplicationField(
            **definition.model_dump(),
            current_value=current[definition.key],
            default_value=defaults[definition.key],
            value=current[definition.key]
            if definition.key in locks
            else saved.get(
                definition.key,
                current[definition.key],
            ),
            locked_reason=locks.get(definition.key),
        )
        for definition in DEFINITIONS
    ]
    return ApplicationSettingsOut(
        fields=fields,
        revision=revision,
        config_path=str(settings_path()),
        restart_required=[field.key for field in fields if field.value != field.current_value],
    )


def save_settings(settings: Settings, patch: ApplicationSettingsPatch) -> ApplicationSettingsOut:
    from .api.errors import ApiError
    from .config import Settings
    from .domain.enums import ErrorCode

    with _lock:
        saved, revision = _read()
        if revision != patch.revision:
            raise ApiError(ErrorCode.VERSION_CONFLICT, "配置已在其他页面修改，请重新读取后再保存。")
        definitions = {field.key: field for field in DEFINITIONS}
        locks = _locks(settings)
        for key, value in patch.values.items():
            if key not in definitions:
                raise ApiError.validation("包含未知配置项。")
            if key in locks:
                raise ApiError.validation(locks[key])
            field = definitions[key]
            if isinstance(value, str) and (len(value) > 4096 or "\x00" in value):
                raise ApiError.validation(f"{field.label}过长或包含非法字符。")
            if field.kind == "number":
                if (
                    type(value) not in (int, float)
                    or not (field.minimum <= value <= field.maximum)
                    or (key != "llm_timeout_seconds" and type(value) is not int)
                ):
                    raise ApiError.validation(f"{field.label}超出范围或不是有效数字。")
            elif field.kind == "boolean" and type(value) is not bool:
                raise ApiError.validation(f"{field.label}必须选择开启或关闭。")
            elif field.kind == "select" and value not in field.options:
                raise ApiError.validation(f"{field.label}的选项无效。")
            elif field.kind == "text" and (
                (value is not None and not isinstance(value, str))
                or (key != "static_dir" and (not value or not str(value).strip()))
            ):
                raise ApiError.validation(f"{field.label}不能为空或使用错误类型。")
            elif field.kind == "lines":
                if not isinstance(value, list) or len(value) > 100:
                    raise ApiError.validation("网页来源必须是最多100个网址的列表。")
                for origin in value:
                    try:
                        parsed = urlsplit(origin) if isinstance(origin, str) else None
                        valid = (
                            parsed
                            and parsed.scheme in {"http", "https"}
                            and (
                                parsed.hostname
                                and parsed.path == ""
                                and not parsed.query
                                and not parsed.fragment
                                and not parsed.username
                                and not parsed.password
                                and parsed.port != 0
                                and "*" not in origin
                            )
                        )
                    except ValueError:
                        valid = False
                    if not valid:
                        raise ApiError.validation(
                            "网页来源需为完整的http或https来源网址，不含路径。"
                        )
        merged = {**settings.model_dump(), **saved, **patch.values}
        if "host" in patch.values and merged["host"] not in {"127.0.0.1", "localhost", "::1"}:
            raise ApiError.validation("界面只允许本地监听地址，不能开放公网服务。")
        for key in ("data_dir", "static_dir"):
            if key in patch.values and merged[key]:
                path = Path(str(merged[key])).expanduser()
                if not path.is_absolute():
                    raise ApiError.validation(f"{definitions[key].label}必须使用绝对路径。")
                if path.exists() and not path.is_dir():
                    raise ApiError.validation(f"{definitions[key].label}不是目录。")
                if key == "static_dir" and not (path / "index.html").is_file():
                    raise ApiError.validation("网页目录中没有index.html，请选择已构建的前端目录。")
        if merged["rate_limit_backoff_max_seconds"] < merged["rate_limit_backoff_base_seconds"]:
            raise ApiError.validation("重试最大等待不能小于初始等待。")
        if merged["max_epub_entry_bytes"] > merged["max_epub_total_uncompressed_bytes"]:
            raise ApiError.validation("EPUB单文件大小上限不能大于解压总大小上限。")
        try:
            validated = Settings(**merged).model_dump(mode="json")
        except ValidationError as exc:
            raise ApiError.validation("配置值无效，请检查格式。") from exc
        output = {**saved, **{key: validated[key] for key in patch.values}}
        stored = {
            CAPACITY_KEYS.get(key, key): value / BYTES_PER_MB if key in CAPACITY_KEYS else value
            for key, value in output.items()
        }
        encoded = json.dumps(stored, ensure_ascii=False, indent=2, allow_nan=False).encode()
        if len(encoded) > 65536:
            raise ApiError.validation("应用配置内容过大，请缩短文字或减少网页来源。")
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return describe_settings(settings)
