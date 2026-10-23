"""Small authenticated Hermes client. Credentials only come from environment."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a bearer token to a redirect target.


def identifier(value):
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("需要有效项目/任务 ID")
    return value


def connection():
    base = os.environ.get("HERMES_VIDEO_API_BASE_URL", "").rstrip("/")
    token = os.environ.get("HERMES_VIDEO_ACCESS_TOKEN", "").strip()
    url = urlsplit(base)
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (url.scheme != "https" and not (local and url.scheme == "http")) or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError("请配置 HTTPS 服务或本机 HTTP 服务地址")
    if not token or "\r" in token or "\n" in token:
        raise ValueError("请配置当前用户的 HERMES_VIDEO_ACCESS_TOKEN")
    return base, token


def call(method, route, payload=None):
    base, token = connection()
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(base + "/api/v1/video/projects" + route, data=data, method=method,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=120) as response:
            return json.load(response)
    except HTTPError as exc:
        # The server's structured detail is useful; omit request URLs and headers.
        try:
            detail = json.loads(exc.read(65536)).get("detail", "请求被拒绝")
        except (ValueError, AttributeError):
            detail = "请求被拒绝"
        raise ValueError(f"HTTP {exc.code}: {detail}") from None
    except (URLError, TimeoutError):
        raise ValueError("连接失败；提交超时后先查询原任务，勿重复付费提交") from None


def export_package(project, output):
    base, token = connection()
    destination = Path(output).resolve()
    if destination.suffix.lower() != ".zip" or destination.exists():
        raise ValueError("输出需为尚不存在的 .zip 文件")
    temporary = destination.with_suffix(".zip.part")
    request = Request(base + "/api/v1/video/projects/" + identifier(project) + "/package",
                      headers={"Authorization": "Bearer " + token})
    created = False
    try:
        with build_opener(NoRedirect()).open(request, timeout=120) as response:
            with temporary.open("xb") as handle:
                created = True
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 2 * 1024 ** 3:
                        raise ValueError("发布包超过 2 GB，请在客户端分批下载")
                    handle.write(chunk)
        if not zipfile.is_zipfile(temporary):
            raise ValueError("服务未返回有效发布包")
        temporary.rename(destination)
        return {"saved_to": str(destination), "bytes": size}
    except HTTPError as exc:
        raise ValueError(f"HTTP {exc.code}: 发布包尚未就绪、未验片或当前账号无权限") from None
    except (URLError, TimeoutError):
        raise ValueError("发布包下载未完成，可重新下载；不会重新生成视频") from None
    finally:
        if created and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["capabilities", "list", "new", "get", "compose", "save", "review", "quote", "job", "query", "refresh", "export"])
    parser.add_argument("--project")
    parser.add_argument("--job")
    parser.add_argument("--payload")
    parser.add_argument("--kind", choices=["voice", "scene", "scene_batch", "avatar", "render"])
    parser.add_argument("--scene")
    parser.add_argument("--scenes", nargs="+", help="scene_batch 固定镜头 ID 列表；省略时查询全部未完成镜头")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.command == "export":
        if not args.output:
            parser.error("export 需要 --output 尚不存在的 .zip 路径")
        print(json.dumps(export_package(args.project, args.output), ensure_ascii=False, indent=2))
        return 0
    methods = {"new": "POST", "compose": "POST", "save": "PUT", "review": "POST", "job": "POST", "refresh": "POST"}
    method = methods.get(args.command, "GET")
    payload = None
    if args.command in {"new", "compose", "save", "review", "job"}:
        if not args.payload:
            parser.error("该命令需要 --payload UTF-8 JSON 文件")
        with open(args.payload, encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("请求 JSON 必须是对象")
    if args.command == "capabilities":
        route = "/capabilities"
    elif args.command in {"list", "new"}:
        route = ""
    else:
        route = "/" + identifier(args.project)
        suffix = {"compose": "/compose", "save": "/plan", "review": "/reviews", "job": "/jobs"}.get(args.command, "")
        route += suffix
        if args.command in {"query", "refresh"}:
            route += "/jobs/" + identifier(args.job) + ("/refresh" if args.command == "refresh" else "")
        elif args.command == "quote":
            if not args.kind:
                parser.error("quote 需要 --kind")
            query = {"kind": args.kind}
            if args.scene:
                query["scene_id"] = identifier(args.scene)
            if args.scenes:
                query["scene_ids"] = [identifier(value) for value in args.scenes]
            route += "/quote?" + urlencode(query, doseq=True)
    result = call(method, route, payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
