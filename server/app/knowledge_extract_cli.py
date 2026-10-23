"""Process-isolated parser; input via stdin, bounded JSON result via stdout."""
import json
import sys
from pathlib import Path

from .knowledge_extract import NeedsInput, extract_file, extract_html, fetch_article


def main():
    request = json.loads(sys.stdin.read(10000))
    try:
        if request.get("path"):
            result = extract_file(Path(request["path"]), request["filename"])
        else:
            result = extract_html(fetch_article(request["url"]), wechat=True)
        response = dict(state="ready", text=result.text, method=result.method, chunks=result.chunks,
                        title=result.title, publisher=result.publisher)
    except NeedsInput as error:
        response = dict(state="needs_input", note=str(error)[:500])
    except (TimeoutError, ConnectionError, OSError):
        response = dict(state="retry_wait", note="内容读取暂时失败，系统将有限重试；也可直接补充正文 / 字幕。")
    except Exception:
        response = dict(state="failed", note="文件解析失败或识别结果格式不正确。原始素材已保留，请补充正文 / 字幕。")
    sys.stdout.write(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
