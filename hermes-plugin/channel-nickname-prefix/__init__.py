from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

_PROFILE_NICKNAMES: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("weixin", "weixin-wuhang"): ("\u5434\u822a\uff0c", ("\u5434\u822a",)),
    ("weixin", "weixin-suzong"): ("\u5bbf\u603b\uff0c", ("\u5bbf\u603b",)),
}

_GATEWAY_NOTICE = (
    "\u6211\u53bb\u5347\u7ea7\u4e00\u4e0b\u88c5\u5907\uff0c"
    "\u624b\u4e0a\u7684\u4efb\u52a1\u5148\u6682\u505c\u4e00\u5c0f\u4f1a\u513f\uff0c"
    "\u9a6c\u4e0a\u56de\u6765\uff5e"
)
_GATEWAY_ONLINE_NOTICE = (
    "\u5347\u7ea7\u5b8c\u6210\u5566\uff0c\u6211\u5df2\u7ecf\u56de\u6765\uff0c"
    "\u73b0\u5728\u53ef\u4ee5\u7ee7\u7eed\u5b89\u6392\u4efb\u52a1\u548c\u5de5\u4f5c\u4e86\uff5e"
)
_NOTICE_TARGETS: set[tuple[str, str, str, str]] = set()


def _gateway_notice_kind(content: Any) -> str | None:
    if not isinstance(content, str):
        return None
    if "Gateway shutting down" in content or "Gateway restarting" in content:
        return "shutdown"
    if "Gateway restarted successfully" in content or "Gateway online" in content:
        return "online"
    return None


def _rewrite_gateway_notice(content: Any) -> Any:
    kind = _gateway_notice_kind(content)
    if kind == "shutdown":
        return _GATEWAY_NOTICE
    if kind == "online":
        return _GATEWAY_ONLINE_NOTICE
    return content


def _decorate(content: Any, prefix: str, accepted_names: tuple[str, ...]) -> Any:
    if not isinstance(content, str) or not content.strip():
        return content
    probe = content.lstrip().lstrip("*_#>~` ")
    if any(probe.startswith(name) for name in accepted_names):
        return content
    return prefix + content


def _nickname_for_adapter(
    adapter: Any,
    platform_name: str,
    default_prefix: str,
    default_accepted_names: tuple[str, ...],
    chat_id: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Resolve a profile-specific nickname without relying on process globals."""
    profile_home = Path(str(getattr(adapter, "_hermes_home", "")))
    personal = profile_home / 'personal-onboarding.json'
    try:
        if chat_id and not personal.is_symlink() and personal.is_file():
            state = json.loads(personal.read_text('utf-8'))
            profile = profile_home.name if profile_home.parent.name == 'profiles' else 'default'
            owner = hashlib.sha256(json.dumps([profile, platform_name, str(chat_id)], ensure_ascii=False).encode()).hexdigest()
            if state.get('schema') == 1 and state.get('owner') == owner:
                address = state.get('user_address')
                if address:
                    return address + '，', (address,)
                if state.get('step') != 'done':
                    return '', ()
    except (OSError, ValueError, TypeError):
        logger.warning('个人称呼暂不可读取，沿用既有称呼')
    if profile_home.parent.name == "profiles":
        configured = _PROFILE_NICKNAMES.get((platform_name, profile_home.name))
        if configured is not None:
            return configured
    return default_prefix, default_accepted_names


def _patch_send(
    adapter_cls: type,
    platform_name: str,
    prefix: str,
    accepted_names: tuple[str, ...],
) -> None:
    original = adapter_cls.send
    original_connect = adapter_cls.connect
    if getattr(adapter_cls, "_channel_nickname_prefix_patched", False):
        # Native hot reload retains adapter classes. Replace only this plugin's wrapper.
        if not all('_PROFILE_NICKNAMES' in getattr(method, '__globals__', {}) and hasattr(method, '__wrapped__')
                   for method in (original, original_connect)):
            raise RuntimeError('称呼插件热加载发现未知包装，请重启对应网关后再验证')
        original, original_connect = original.__wrapped__, original_connect.__wrapped__

    @functools.wraps(original)
    async def wrapped(self, chat_id, content, *args, **kwargs):
        notice_kind = _gateway_notice_kind(content)
        effective_prefix, effective_accepted_names = _nickname_for_adapter(
            self,
            platform_name,
            prefix,
            accepted_names,
            str(chat_id),
        )
        result = await original(
            self,
            chat_id,
            _decorate(
                _rewrite_gateway_notice(content),
                effective_prefix,
                effective_accepted_names,
            ),
            *args,
            **kwargs,
        )
        if notice_kind and (result is None or getattr(result, "success", True)):
            metadata = kwargs.get("metadata") or {}
            thread_id = metadata.get("thread_id") if isinstance(metadata, dict) else None
            _NOTICE_TARGETS.add(
                (notice_kind, platform_name, str(chat_id), str(thread_id or ""))
            )
        return result

    adapter_cls.send = wrapped

    @functools.wraps(original_connect)
    async def connect_wrapped(self, *args, **kwargs):
        _patch_gateway_lifecycle()
        return await original_connect(self, *args, **kwargs)

    adapter_cls.connect = connect_wrapped
    adapter_cls._channel_nickname_prefix_patched = True


def _notice_target(kind: str, platform: Any, home: Any) -> tuple[str, str, str, str]:
    platform_name = getattr(platform, "value", str(platform))
    return (kind, platform_name, str(home.chat_id), str(home.thread_id or ""))


async def _send_secondary_home_notice(runner: Any, kind: str, content: str) -> bool:
    """向复用网关中的独立 Profile 补发生命周期消息。"""
    try:
        from gateway.config import load_gateway_config
        from gateway.run import _profile_runtime_scope
        from hermes_cli.profiles import get_active_profile_name, profiles_to_serve
    except ImportError:
        return False

    pending = False
    active = get_active_profile_name() or "default"
    profile_adapters = getattr(runner, "_profile_adapters", {})
    for profile_name, profile_home in profiles_to_serve(multiplex=True):
        if profile_name == active:
            continue
        try:
            with _profile_runtime_scope(Path(profile_home)):
                profile_cfg = load_gateway_config()
        except Exception:
            logger.debug("Failed to load lifecycle config for profile %s", profile_name, exc_info=True)
            pending = True
            continue

        adapters = profile_adapters.get(profile_name, {})
        for platform, platform_cfg in profile_cfg.platforms.items():
            if not platform_cfg.enabled or not platform_cfg.gateway_restart_notification:
                continue
            home = platform_cfg.home_channel
            if not home or not home.chat_id:
                continue
            target = _notice_target(kind, platform, home)
            if target in _NOTICE_TARGETS:
                continue
            adapter = adapters.get(platform)
            if adapter is None:
                pending = True
                continue
            try:
                metadata = runner._thread_metadata_for_target(
                    platform,
                    home.chat_id,
                    home.thread_id,
                    adapter=adapter,
                )
                with _profile_runtime_scope(Path(profile_home)):
                    result = await adapter.send(
                        str(home.chat_id),
                        content,
                        metadata=metadata,
                    )
                if result is not None and getattr(result, "success", True) is False:
                    pending = True
                else:
                    _NOTICE_TARGETS.add(target)
            except Exception:
                logger.warning(
                    "Lifecycle notification failed for profile %s platform %s",
                    profile_name,
                    getattr(platform, "value", platform),
                    exc_info=True,
                )
                pending = True
    return await _send_profile_env_home_notice(runner, kind, content) or pending


async def _send_profile_env_home_notice(runner: Any, kind: str, content: str) -> bool:
    """补发“适配器归主进程、home 目标归独立 Profile”的通知。"""
    try:
        from dotenv import dotenv_values
        from gateway.run import _hermes_home
    except ImportError:
        return False

    pending = False
    profile_targets = (("wecom", "WECOM_HOME_CHANNEL"),)
    adapters = list(runner._iter_gateway_adapters())
    for platform_name, env_name in profile_targets:
        env_path = Path(_hermes_home) / "profiles" / platform_name / ".env"
        if not env_path.exists():
            continue
        chat_id = str(dotenv_values(env_path).get(env_name) or "").strip()
        if not chat_id:
            continue
        adapter = next(
            (
                item
                for item in adapters
                if getattr(getattr(item, "platform", None), "value", "") == platform_name
            ),
            None,
        )
        if adapter is None:
            pending = True
            continue
        platform = adapter.platform
        platform_cfg = runner.config.platforms.get(platform)
        if platform_cfg is not None and not platform_cfg.gateway_restart_notification:
            continue
        home = SimpleNamespace(chat_id=chat_id, thread_id=None)
        target = _notice_target(kind, platform, home)
        if target in _NOTICE_TARGETS:
            continue
        try:
            metadata = runner._thread_metadata_for_target(
                platform,
                chat_id,
                None,
                adapter=adapter,
            )
            result = await adapter.send(chat_id, content, metadata=metadata)
            if result is not None and getattr(result, "success", True) is False:
                pending = True
            else:
                _NOTICE_TARGETS.add(target)
                logger.info(
                    "Sent %s lifecycle notification to profile-owned home target %s",
                    kind,
                    platform_name,
                )
        except Exception:
            logger.warning(
                "Lifecycle notification failed for profile-owned home target %s",
                platform_name,
                exc_info=True,
            )
            pending = True
    return pending


async def _retry_secondary_online_notice(runner: Any) -> None:
    for _ in range(120):
        if not await _send_secondary_home_notice(runner, "online", _GATEWAY_ONLINE_NOTICE):
            return
        await asyncio.sleep(5)
    logger.warning("Lifecycle online notification retry window expired")


def _patch_gateway_lifecycle() -> None:
    gateway_run = sys.modules.get("gateway.run")
    GatewayRunner = getattr(gateway_run, "GatewayRunner", None)
    if GatewayRunner is None:
        return

    if getattr(GatewayRunner, "_channel_lifecycle_notice_patched", False):
        return

    original_shutdown = GatewayRunner._notify_active_sessions_of_shutdown
    original_online = GatewayRunner._send_home_channel_startup_notifications

    @functools.wraps(original_shutdown)
    async def shutdown_wrapped(self, *args, **kwargs):
        result = await original_shutdown(self, *args, **kwargs)
        await _send_secondary_home_notice(self, "shutdown", _GATEWAY_NOTICE)
        return result

    @functools.wraps(original_online)
    async def online_wrapped(self, *args, **kwargs):
        result = await original_online(self, *args, **kwargs)
        pending = await _send_secondary_home_notice(self, "online", _GATEWAY_ONLINE_NOTICE)
        if pending:
            task = asyncio.create_task(_retry_secondary_online_notice(self))
            background = getattr(self, "_background_tasks", None)
            if isinstance(background, set):
                background.add(task)
                task.add_done_callback(background.discard)
        return result

    GatewayRunner._notify_active_sessions_of_shutdown = shutdown_wrapped
    GatewayRunner._send_home_channel_startup_notifications = online_wrapped
    GatewayRunner._channel_lifecycle_notice_patched = True
    logger.info("Gateway lifecycle notice hooks installed")


def register(ctx: Any) -> None:
    try:
        from plugins.platforms.qqbot.adapter import QQAdapter
    except ImportError:
        from gateway.platforms.qqbot.adapter import QQAdapter
    try:
        from plugins.platforms.weixin.adapter import WeixinAdapter
    except ImportError:
        from gateway.platforms.weixin import WeixinAdapter

    _patch_send(
        QQAdapter,
        "qqbot",
        "\u9753\u4ed4\uff0c",
        ("\u9753\u4ed4", "\u6770\u54e5"),
    )
    _patch_send(
        WeixinAdapter,
        "weixin",
        "\u59d0\uff0c",
        ("\u59d0", "\u654f\u603b"),
    )

    try:
        from plugins.platforms.wecom.adapter import WeComAdapter
    except ImportError:
        WeComAdapter = None
    if WeComAdapter is not None:
        _patch_send(
            WeComAdapter,
            "wecom",
            "\u632f\u534e\uff0c",
            ("\u632f\u534e",),
        )

    _patch_gateway_lifecycle()

    logger.info("Channel nickname prefixes and lifecycle notices enabled for qqbot, weixin, and wecom")
