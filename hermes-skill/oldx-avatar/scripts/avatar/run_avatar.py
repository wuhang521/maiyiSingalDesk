"""把各段提交到 RunningHub 的数字人应用，按会员并发数并行；有完整的重试与断点续跑。

  python run_avatar.py --workdir <项目目录>                  # 只做检查和预览，不联网、不花钱
  python run_avatar.py --workdir <项目目录> --voice-approved "用户试听后回复的原话" --yes   # 上传文件并提交（会产生费用）
  python run_avatar.py --workdir <项目目录> --yes --only 2   # 只重跑第 2 段，其余沿用已有结果
  python run_avatar.py --workdir <项目目录> --yes --only 2 --seed random   # 重跑第 2 段并换一个随机种子（提示词不用改）
  python run_avatar.py --workdir <项目目录> --yes --all      # 全部重跑（旧片段都会收进历史，不会丢）

配音试听确认（写在代码里）：第一次提交前，--yes 必须带 --voice-approved，内容是用户试听配音后在对话里回复的原话
（例如“配音可以”；用户一开始就说过“配音不用给我听，直接做”也算，填那句话）。用户还没回复就停下等，不能自己编。
这句话和当时 out/merged.wav 的指纹记进 <项目目录>/voice_review.json；配音没变时之后重跑某几段不用再给，
配音重新合成过（换了语气、改了文案……），旧的确认作废，要请用户再听一次。

历史版本：重跑某段之前，它现在的片段会自动收进 avatar/takes/seg_NN/（连同当时的种子、工作流版本、提示词），
不会被覆盖。想换回以前的版本用 takes.py。
工作流版本：每段都记下提交时的工作流版本标签（check_setup.py --set-workflow 设置）。用户改过云端工作流就先记一个，
同一条片子里混用不同工作流版本生成的片段时，预览会提示。

随机种子：每段用的种子记在 result.json 的 seeds 里。不带 --seed 时，跑过的段沿用它上次的种子，没跑过的用默认值 42，
所以同样的输入重跑会得到同样的结果；想让某段出一个不同的结果，用 --only <段号> --seed random（或 --seed <整数>）。

并发数 = min(段数, 会员档位对应并发)，档位用 check_setup.py --set-plan 记录；没记录按 1 路。
超出并发的段在本地排队。实例默认 plus（48G，用户已授权）；ultra 需要显式 --instance ultra，本技能不会自行升级。

重试与续跑（每一层各管各的，避免重复计费）：
  * 上传、下载：各自重试 3 次（2/4/8 秒退避）。云端已成功时，下载失败只重下，**绝不重新提交**。
  * 提交：遇到并发/排队/限流类拒绝（含 HTTP 429、连接错误）就等 20 秒再提交，最多 12 次。
  * 轮询：连接错误容忍约 3 分钟；超时（--timeout-min）后**不重新提交**，任务号保留在 result.json 的 pending 里，
    再次运行本脚本会继续等同一个任务。
  * 任务失败：先判断类型。余额不足、参数/权限错误这类重试也没用，直接停下并报告；其余（含显存不足的 805）
    退避 15 秒后重提交，最多 --retries 次（默认 1，失败任务不计费）。
  * 任务号一提交就写入 result.json 的 pending；脚本被中断后重跑，会先续上这些任务，不会全部重新提交。
"""
import argparse
import json
import os
import random
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402
import _review
import _util  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import takes as TK

lock = threading.Lock()
NON_RETRYABLE = re.compile(r"余额不足|余额|欠费|RH币不足|insufficient|balance|arrears|参数错误|invalid ?param|未授权|无权限|unauthorized|forbidden", re.I)
RATE_LIMIT_WORDS = ("并发", "排队", "队列", "限流", "concurr", "queue", "limit", "频繁")


class NeedsReview(RuntimeError):
    """A resumed task ended; submitting a replacement requires a new batch approval."""


def money_value(value):
    """Parse provider money without treating missing or malformed usage as zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip())
        return amount if amount.is_finite() and amount >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def safe_usage(value):
    """Persist only public billing fields, never arbitrary provider response data."""
    if not isinstance(value, dict):
        return {}
    return {field: str(amount) if (amount := money_value(value.get(field))) is not None else None
            for field in ("consumeMoney", "thirdPartyConsumeMoney", "consumeCoins", "taskCostTime")
            if field in value}


def execution_concurrency(requested, segment_count):
    return 1 if os.environ.get("OLDX_HERMES") == "1" else min(requested, segment_count)


def cost_review_required():
    return not (os.environ.get("OLDX_HERMES") == "1" and os.environ.get("OLDX_COST_REVIEW_REQUIRED") == "0")


class AvatarBudget:
    """Hermes submission guard. Its reserve is a software estimate, not a cloud cap."""

    def __init__(self, state, maximum, reserve, job_id, generation_consent=False):
        self.state = state
        self.maximum, self.reserve = money_value(maximum), money_value(reserve)
        self.job_id = str(job_id or "legacy").strip() or "legacy"
        self.generation_consent = generation_consent is True

    @classmethod
    def from_environment(cls, state):
        if os.environ.get("OLDX_HERMES") != "1":
            return None
        return cls(state, os.environ.get("OLDX_AVATAR_BUDGET_CNY"),
                   os.environ.get("OLDX_AVATAR_SEGMENT_RESERVE_CNY"),
                   os.environ.get("OLDX_AVATAR_BUDGET_JOB_ID"),
                   os.environ.get("OLDX_AVATAR_GENERATION_CONSENT") == "1")

    def committed(self):
        total = Decimal("0")
        for record in self.state.d.get("billing", {}).values():
            if record.get("budget_job_id") != self.job_id:
                continue
            # thirdPartyConsumeMoney is retained separately. Its relationship to
            # consumeMoney is undocumented, so never silently add or double it.
            usage = record.get("usage") or {}
            amount = money_value(usage.get("consumeMoney"))
            reserve = money_value(record.get("reserve_cny"))
            if reserve is None:
                reserve = self.reserve or Decimal("0")
            third_party = money_value(usage.get("thirdPartyConsumeMoney"))
            if amount is None:
                amount = reserve
            elif third_party is not None and third_party > 0:
                # Additional-node billing is reported but its inclusion in the
                # amount is unclear. Keep the reserve instead of releasing it.
                amount = max(amount, reserve)
            total += amount
        return total

    def before_submit(self, segment):
        if any(str(index) != str(segment) and record.get("task")
               for index, record in self.state.d.get("pending", {}).items()):
            raise NeedsReview("已有数字人任务仍在云端运行，先续查原任务，暂不提交后续片段")
        if self.generation_consent:
            if self.job_id == "legacy" or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.job_id):
                raise NeedsReview("本次数字人生成尚未取得有效的任务确认，请重新点击生成")
            if any(row.get("budget_job_id") == self.job_id and str(row.get("segment")) == str(segment)
                   for row in self.state.d.get("billing", {}).values()):
                raise NeedsReview("原片段任务已结束，重新生成前请重新确认并创建新任务")
            return
        if self.maximum is None or self.maximum <= 0 or self.reserve is None or self.reserve <= 0:
            raise NeedsReview("数字人费用尚未确认，不能提交新任务；已提交任务可继续查询")
        if self.committed() + self.reserve > self.maximum:
            raise NeedsReview("本批数字人已达到确认预算，停止提交第 %s 段；已有任务仍可续查，请核对账单后确认新预算" % segment)

    def record(self, task, segment, status, response, pending=None):
        previous = self.state.get("billing", task) or {}
        job_id = previous.get("budget_job_id") or (pending or {}).get("budget_job_id") or self.job_id
        reserve = previous.get("reserve_cny") or (pending or {}).get("reserve_cny")
        if reserve is None and self.reserve is not None:
            reserve = str(self.reserve)
        usage = dict(previous.get("usage") or {})
        for field, value in safe_usage((response or {}).get("usage")).items():
            if value is not None or field not in usage:
                usage[field] = value
        self.state.set("billing", task, {"task": task, "segment": segment, "status": status,
                       "budget_job_id": job_id, "reserve_cny": reserve,
                       "usage": usage})


def generation_resume_authorized(args, state, context, budget):
    """Continue only this approved Hermes batch with exactly the original inputs."""
    if budget is None or budget.job_id == "legacy" or args.all or args.only is not None:
        return False
    record = state.get("generation_authorizations", budget.job_id)
    if not record:
        return False
    original = record.get("context") or {}
    stable = ("instance", "concurrency", "only", "all", "seed", "app_id", "workflow")
    if any(context.get(key) != original.get(key) for key in stable):
        return False
    if record.get("maximum_cny") != str(budget.maximum) or record.get("reserve_cny") != str(budget.reserve):
        return False
    if bool(record.get("generation_consent")) != budget.generation_consent:
        return False
    if not set(context.get("todo", [])).issubset(set(original.get("todo", []))):
        return False
    # A terminal task cannot become a free replacement just because its output
    # disappeared or it failed. A pending successful download can still resume.
    pending = state.d.get("pending", {})
    for index in context.get("todo", []):
        if (pending.get(str(index)) or {}).get("task"):
            continue
        if any(row.get("budget_job_id") == budget.job_id and row.get("segment") == index
               for row in state.d.get("billing", {}).values()):
            return False
    snap = _review.snapshot(args.workdir, "generation", original)
    return not snap["missing"] and snap["fingerprint"] == record.get("fingerprint")


def remember_generation_authorization(args, state, context, budget):
    if budget is None or budget.job_id == "legacy":
        return
    snap = _review.snapshot(args.workdir, "generation", context)
    if snap["missing"]:
        raise NeedsReview("生成素材不完整，不能记录本批生成确认")
    state.set("generation_authorizations", budget.job_id,
              {"context": context, "fingerprint": snap["fingerprint"],
               "maximum_cny": str(budget.maximum), "reserve_cny": str(budget.reserve),
               "generation_consent": budget.generation_consent})


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


class State:
    """result.json：results（已完成）、errors、pending（已提交未完成的任务号）、seeds（每段用的随机种子）。每次变化立即落盘。"""

    def __init__(self, path):
        self.path, self.lk = path, threading.Lock()
        self.d = {"results": {}, "errors": {}, "pending": {}, "seeds": {}, "billing": {}, "generation_authorizations": {}}
        if os.path.isfile(path):
            self.d.update(json.load(open(path, encoding="utf-8")))
            for k in ("results", "errors", "pending", "seeds", "billing", "generation_authorizations"):
                self.d.setdefault(k, {})

    def _save(self):
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(self.d, stream, ensure_ascii=False, indent=1)
        os.replace(temporary, self.path)

    def set(self, section, i, value):
        with self.lk:
            self.d[section][str(i)] = value
            self._save()

    def drop(self, section, i):
        with self.lk:
            self.d[section].pop(str(i), None)
            self._save()

    def get(self, section, i):
        return self.d[section].get(str(i))


def with_retry(fn, what, tries=3, base=2.0):
    last = None
    for k in range(tries):
        try:
            return fn()
        except PermissionError:
            raise
        except Exception as e:
            last = e
            if k < tries - 1:
                wait = base * (2 ** k)
                log("%s失败（%s），%.0f 秒后重试（%d/%d）" % (what, str(e)[:80], wait, k + 1, tries - 1))
                time.sleep(wait)
    raise RuntimeError("%s重试 %d 次仍失败：%s" % (what, tries, last))


def submit(i, body, key, wait_min=30, budget=None):
    """提交；并发名额被占满、排队中被拒时耐心重试（20 秒起，逐步拉长到 60 秒），最多等 wait_min 分钟。"""
    t0, wait = time.time(), 20
    while True:
        # Hermes persists this marker before sending. A lost response is ambiguous;
        # never retry a billable POST without a provider task ID.
        marker = os.path.join(os.environ.get("DH_WORKSPACE", "."), "avatar", "submission_%d.json" % i)
        if os.environ.get("OLDX_HERMES"):
            if os.path.isfile(marker):
                try:
                    previous = json.load(open(marker, encoding="utf-8"))
                    bill = (budget.state.get("billing", previous.get("task")) or {}) if budget is not None else {}
                except (OSError, ValueError, AttributeError):
                    bill = {}
                # A new explicitly confirmed job may replace a definitely failed
                # task. Ambiguous or still running submissions remain blocked.
                if (budget is not None and budget.generation_consent and bill.get("status") == "FAILED"
                        and bill.get("budget_job_id") != budget.job_id):
                    os.unlink(marker)
                else:
                    raise NeedsReview("上次提交结果不明确，请先到 RunningHub 核对任务，不能自动重发")
            json.dump({"segment": i, "status": "submitting", "at": time.time()}, open(marker, "w", encoding="utf-8"))
        st, r = C.http_json("%s/run/ai-app/%s" % (C.BASE, C.app_id()), body, key)
        task = r.get("taskId")
        if task and r.get("status") != "FAILED":
            if os.environ.get("OLDX_HERMES"):
                json.dump({"segment": i, "status": "submitted", "task": task}, open(marker, "w", encoding="utf-8"))
            return task
        if budget is not None and task and r.get("status") == "FAILED":
            budget.record(task, i, "FAILED", r)
        if os.environ.get("OLDX_HERMES"):
            if st:
                os.unlink(marker)  # Definitive refusal; caller still stops this job.
            raise NeedsReview("提交未返回可续查任务号，请核对供应商结果后再决定")
        msg = json.dumps({k: r.get(k) for k in ("status", "errorCode", "errorMessage", "message")}, ensure_ascii=False)
        if st in (401, 403):
            raise PermissionError("密钥被拒绝（HTTP %s）" % st)
        if NON_RETRYABLE.search(msg):
            raise RuntimeError("[不可重试] 提交被拒：" + msg[:300])
        if st in (0, 429) or any(k in msg for k in RATE_LIMIT_WORDS):
            if time.time() - t0 + wait > wait_min * 60:
                raise RuntimeError("排队等了 %d 分钟还是提交不上（并发名额一直被占着）：%s。稍后重跑同一条命令即可，已成功的段不会重复提交"
                                   % (wait_min, msg[:120]))
            log("[段%d] 名额被占满，排队中（%s），%d 秒后再试；已等 %.0f 分钟，最多等 %d 分钟" % (i, msg[:60], wait, (time.time() - t0) / 60, wait_min))
            time.sleep(wait)
            wait = min(60, wait + 10)
            continue
        raise RuntimeError("提交失败：" + msg[:300])


def poll(i, task, key, timeout_s, interval=10, max_conn_errors=18):
    """返回 ("SUCCESS"|"FAILED"|"TIMEOUT"|"STALE", query_json)。连接错误累计超限抛 RuntimeError（任务保留）。"""
    t0, last, conn = time.time(), None, 0
    while time.time() - t0 < timeout_s:
        st, q = C.http_json(C.BASE + "/query", {"taskId": task}, key)
        if st in (401, 403):
            raise PermissionError("密钥被拒绝（HTTP %s）" % st)
        if st == 0 or st >= 500 or st == 429:
            conn += 1
            if conn > max_conn_errors:
                raise RuntimeError("连续 %d 次查询失败，任务 %s 可能仍在云端，重跑本脚本会继续查它" % (conn, task))
            time.sleep(interval)
            continue
        if st != 200 and 400 <= st < 500:
            return "STALE", q                      # 任务号不存在或已过期
        conn = 0
        s = q.get("status")
        if s != last:
            log("[段%d] 状态 %s" % (i, s))
            last = s
        if s in ("SUCCESS", "FAILED"):
            return s, q
        time.sleep(interval)
    return "TIMEOUT", None


def run_one(seg, prompt, img_val, w, h, key, instance, out_dir, timeout_min, tries, state, interval=10, backoff=15.0, seed=None, meta=None,
            queue_min=30, allow_submit=True, budget=None):
    i = seg["i"]
    meta = dict(meta or {})
    for attempt in range(1, tries + 2):
        pend = state.get("pending", i)
        if pend and pend.get("task"):
            task = pend["task"]
            log("[段%d] 续上上次已提交的任务 %s" % (i, task))
        else:
            if not allow_submit:
                raise NeedsReview("原任务已结束；重新提交前需确认新的生成方案和费用")
            if budget is not None:
                budget.before_submit(i)
            aud = with_retry(lambda: C.upload(seg["audio"], key), "[段%d] 上传音频" % i)
            body = {"nodeInfoList": [
                {"nodeId": C.NODE_AUDIO, "fieldName": "audio", "fieldValue": aud, "description": "音频"},
                {"nodeId": C.NODE_IMG, "fieldName": "image", "fieldValue": img_val, "description": "口播图"},
                {"nodeId": C.NODE_PROMPT, "fieldName": "string", "fieldValue": prompt, "description": "提示词"},
                {"nodeId": C.NODE_WIDTH, "fieldName": "value", "fieldValue": str(w), "description": "宽"},
                {"nodeId": C.NODE_HEIGHT, "fieldName": "value", "fieldValue": str(h), "description": "高"}],
                "instanceType": instance, "usePersonalQueue": "false"}
            if seed is not None:
                body["nodeInfoList"].append({"nodeId": C.NODE_SEED, "fieldName": "noise_seed", "fieldValue": str(seed), "description": "随机种"})
            task = submit(i, body, key, wait_min=queue_min, budget=budget)
            pending = {"task": task, "instance": instance, "seed": seed, "workflow": meta.get("workflow"),
                       "prompt_sha": meta.get("prompt_sha"), "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            if budget is not None:
                pending.update(budget_job_id=budget.job_id, reserve_cny=str(budget.reserve))
            state.set("pending", i, pending)
            log("[段%d] 已提交 task %s（第 %d 次，种子 %s）" % (i, task, attempt, seed))
        outcome, q = poll(i, task, key, timeout_min * 60, interval)
        if budget is not None and outcome in ("SUCCESS", "FAILED"):
            # Keep the charge before downloads or error handling can fail.
            budget.record(task, i, outcome, q, pend)
        if outcome == "SUCCESS":
            mp4 = [x for x in (q.get("results") or []) if x.get("outputType") == "mp4" and x.get("url")]
            if not mp4:
                state.drop("pending", i)
                raise RuntimeError("[不可重试] 任务成功但没有 mp4 结果，task %s" % task)
            dst = os.path.join(out_dir, "seg_%02d.mp4" % i)
            # 云端已成功（已计费）：下载失败只重下，绝不重新提交
            with_retry(lambda: urllib.request.urlretrieve(mp4[0]["url"], dst), "[段%d] 下载视频" % i, tries=4)
            log("[段%d] 已保存 %s" % (i, dst))
            resumed = bool(pend and pend.get("task"))
            res = {"i": i, "task": task, "file": dst, "seconds": seg["duration"], "seed": pend.get("seed", seed) if resumed else seed,
                   "usage": safe_usage(q.get("usage")) if budget is not None else q.get("usage"),
                   "workflow": pend.get("workflow", meta.get("workflow")) if resumed else meta.get("workflow"),
                   "app_id": meta.get("app_id"), "prompt_sha": pend.get("prompt_sha", meta.get("prompt_sha")) if resumed else meta.get("prompt_sha"),
                   "prompt": meta.get("prompt"), "note": meta.get("note", ""), "created": time.strftime("%Y-%m-%d %H:%M:%S")}
            state.set("results", i, res)
            state.drop("pending", i)
            state.drop("errors", i)
            return res
        if outcome == "TIMEOUT":
            raise RuntimeError("[已保留] 超时（%d 分钟），task %s 可能还在云端，重跑本脚本会继续等它，不会重复提交" % (timeout_min, task))
        if outcome == "STALE":
            log("[段%d] 旧任务 %s 查询不到，丢弃后重新提交" % (i, task))
            state.drop("pending", i)
            if not allow_submit:
                raise NeedsReview("原任务 %s 已过期；重新提交前需确认生成方案和费用" % task)
            continue
        # FAILED
        state.drop("pending", i)
        reason = "errorCode=%s %s | %s" % (q.get("errorCode"), q.get("errorMessage"), json.dumps(q.get("failedReason"), ensure_ascii=False)[:300])
        log("[段%d] 任务失败：%s" % (i, reason[:200]))
        if budget is not None and budget.generation_consent:
            raise NeedsReview("原片段任务已失败，重新生成前请重新确认并创建新任务")
        if NON_RETRYABLE.search(reason):
            raise RuntimeError("[不可重试] " + reason)
        if not allow_submit:
            raise NeedsReview("原任务 %s 已失败；重新提交前需确认生成方案和费用：%s" % (task, reason))
        if attempt <= tries:
            log("[段%d] %.0f 秒后重新提交（失败任务不计费）" % (i, backoff))
            time.sleep(backoff)
            continue
        raise RuntimeError("任务失败，已重试 %d 次：%s" % (tries, reason))
    raise RuntimeError("段%d 没有得到结果" % i)


CANCEL_URL = "https://www.runninghub.cn/task/openapi/cancel"


def cancel_tasks(od, state, only):
    """取消已提交、还没完成的云端任务（result.json 的 pending）。接口：POST /task/openapi/cancel {apiKey, taskId}。"""
    pend = {int(k): v for k, v in state.d.get("pending", {}).items() if v.get("task") and (not only or int(k) in only)}
    if not pend:
        print("没有正在云端跑的任务。")
        _cli.report(cancelled=[], failed_cancel={}, next=None)
        return
    key = C.require_key()
    ok, bad = [], {}
    for i, p in sorted(pend.items()):
        st, r = C.http_json(CANCEL_URL, {"apiKey": key, "taskId": p["task"]}, key)
        if st == 200 and str(r.get("code")) in ("0", "200"):
            ok.append(i)
            state.drop("pending", i)
            print("段%d：已取消任务 %s" % (i, p["task"]))
        else:
            bad[i] = "HTTP %s %s" % (st, json.dumps({k: r.get(k) for k in ("code", "msg", "message")}, ensure_ascii=False)[:200])
            print("段%d：取消失败（%s）。任务号 %s，可以到 RunningHub 后台手动取消" % (i, bad[i], p["task"]))
    _cli.report(cancelled=ok, failed_cancel=bad, next=None if not bad else "到 RunningHub 后台手动取消失败的任务")
    if bad:
        sys.exit(_cli.REMOTE_FAILED)


VOICE_REVIEW = "voice_review.json"


def voice_approval(wd):
    """voice_review.json 里对这一版 out/merged.wav 的确认（用户的原话）；没有或配音变了返回 None"""
    merged = os.path.join(wd, "out", "merged.wav")
    try:
        rec = json.load(open(os.path.join(wd, VOICE_REVIEW), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if rec.get("approved") and os.path.isfile(merged) and rec.get("audio_sha") == _util.file_sha(merged):
        return rec["approved"]
    return None


def check_voice_approval(wd, text):
    """提交之前核对用户试听过这一版配音：本次给了 --voice-approved（记进 voice_review.json），或者记录里的指纹和现在的
    out/merged.wav 一致。都没有就以 need_user 退出，不提交、不花钱。返回用户的原话。"""
    merged = os.path.join(wd, "out", "merged.wav")
    sha = _util.file_sha(merged) if os.path.isfile(merged) else None
    rp = os.path.join(wd, VOICE_REVIEW)
    text = (text or "").strip()
    if text:
        json.dump({"approved": text, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "audio": os.path.abspath(merged), "audio_sha": sha},
                  open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        _cli.report(voice_approved=text)
        return text
    ok = voice_approval(wd)
    if ok:
        _cli.report(voice_approved=ok)
        return ok
    rec = os.path.isfile(rp)
    why = "配音在用户确认之后重新合成过，旧的确认作废，要请用户再听一次" if rec else "还没有用户试听配音的确认"
    _cli.report(wait_for_user=True, next="停下，不要提交：把 %s 的路径和各段时长、语速发给用户试听，等用户回复。用户确认后再用 "
                "--voice-approved \"用户回复的原话\" --yes 提交（预览里的段数和预计费用一起告诉用户）" % merged)
    _cli.fail(_cli.NEED_USER, "提交生成人物视频之前，要用户先试听配音并确认（%s）。--yes 必须带 --voice-approved \"用户回复的原话\"；"
                              "用户还没回复就停下等，不要自己填。" % why)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--instance", default=C.DEFAULT_INSTANCE, choices=["default", "plus", "ultra"])
    ap.add_argument("--concurrency", type=int)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--all", action="store_true", help="全部段重跑（旧片段收进历史）")
    ap.add_argument("--note", default="", help="给这次生成的版本记一句备注，例如“换成中文叙述”")
    ap.add_argument("--seed", help="随机种子：整数，或 random（每段各取一个随机数）。不填：没跑过的段用 42；跑过的段沿用上次的种子，"
                                   "但提示词和工作流都没改时自动换随机种子（不然结果一样）")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--timeout-min", type=int, default=45)
    ap.add_argument("--queue-min", type=int, default=30, help="并发名额被占满时最多排队等几分钟（默认 30）")
    ap.add_argument("--cancel", action="store_true", help="取消已提交、还没完成的云端任务（配合 --only 只取消某几段）")
    ap.add_argument("--voice-approved", default="", help="用户试听配音后回复的原话（第一次提交时 --yes 必须带上）")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--legacy", action="store_true", help="旧项目：plan.json 里没有音频指纹也继续")
    a = ap.parse_args()
    od = os.path.join(a.workdir, "avatar")
    if a.cancel:
        return cancel_tasks(od, State(os.path.join(od, "result.json")), a.only)
    plan = json.load(open(os.path.join(od, "plan.json"), encoding="utf-8"))
    info = json.load(open(os.path.join(od, "image_info.json"), encoding="utf-8"))
    C.verify_plan_audio(a.workdir, plan, a.legacy)         # 分段之后音频被改过就停下，不提交
    segs = plan["segments"]
    prompts = {}
    for s in segs:
        p = os.path.join(od, "prompts", "segment_%02d.txt" % s["i"])
        if not os.path.isfile(p):
            _cli.fail(_cli.ERROR, "缺少提示词：%s（先运行 build_prompts.py）" % p)
        prompts[s["i"]] = open(p, encoding="utf-8").read()
    w, h = info["width"], info["height"]
    conc = execution_concurrency(C.concurrency(a.concurrency), len(segs))
    state = State(os.path.join(od, "result.json"))
    budget = AvatarBudget.from_environment(state)
    # Migrated Hermes jobs may only recover their existing cloud receipts. Their
    # old cost confirmation must never become authorization for a new segment.
    resume_only = os.environ.get("OLDX_HERMES") == "1" and os.environ.get("OLDX_AVATAR_RESUME_ONLY") == "1"
    if resume_only and (a.all or a.only is not None):
        raise NeedsReview("恢复模式只能查询已提交的原任务，不能指定重做片段")
    review_context = _review.generation_context(a, C)
    resume_authorized = generation_resume_authorized(a, state, review_context, budget)
    # A pure pending-task resume must keep polling the same task, without another paid submission.
    selected = [s for s in segs if a.all or a.only is None or s["i"] in a.only]
    if resume_only:
        selected = [s for s in selected if (state.get("pending", s["i"]) or {}).get("task")]
    new_submission = any(
        a.all or a.only is not None or
        (not os.path.isfile((state.get("results", s["i"]) or {}).get("file", ""))
         and not (state.get("pending", s["i"]) or {}).get("task"))
        for s in selected)
    if a.yes:
        quote = None
        if new_submission:
            _review.require(a.workdir, "inputs")
            quote = _review.require(a.workdir, "voice")
            if not resume_authorized:
                _review.require(a.workdir, "generation", review_context)
        if new_submission or _review.read(a.workdir) is None:
            check_voice_approval(a.workdir, quote or a.voice_approved)
        if new_submission and not resume_authorized:
            remember_generation_authorization(a, state, review_context, budget)
    if a.all:
        a.only = [s["i"] for s in segs]
    old_results = {k: dict(v) for k, v in state.d["results"].items()}        # 丢掉之前留一份，收进历史时用
    if a.only is not None and a.yes:                       # 指定重跑：丢掉这些段的旧结果和旧任务（预览时不动 result.json）
        for i in a.only:
            state.drop("results", i)
            state.drop("pending", i)
            state.drop("errors", i)
    if a.seed not in (None, "random") and not re.fullmatch(r"\d{1,15}", a.seed):
        _cli.fail(_cli.USAGE, "--seed 只能是非负整数或 random")
    done = {int(k): v for k, v in state.d["results"].items() if os.path.isfile(v["file"]) and not (a.only is not None and int(k) in a.only)}
    todo = [s for s in segs if s["i"] not in done and (a.only is None or s["i"] in a.only)
            and (not resume_only or (state.get("pending", s["i"]) or {}).get("task"))]
    resumable = [s["i"] for s in todo if state.get("pending", s["i"])]
    seeds = {}
    for s in todo:                                         # 已提交未完成的任务沿用它提交时的种子，不受 --seed 影响
        pend = state.get("pending", s["i"]) or {}
        if pend.get("task"):
            seeds[s["i"]] = pend.get("seed", state.get("seeds", s["i"]))
        elif a.seed == "random":
            seeds[s["i"]] = random.randint(1, 2 ** 31 - 1)
        elif a.seed is not None:
            seeds[s["i"]] = int(a.seed)
        else:
            old = state.get("seeds", s["i"])
            seeds[s["i"]] = C.DEFAULT_SEED if old is None else old
    cfg = C.load_config()
    wf_label, wf_at = C.workflow()
    auto_seed = []                                         # 重跑时提示词、工作流、种子都没变：结果会一模一样，自动换随机种子
    for s in todo:
        old = old_results.get(str(s["i"])) or {}
        same = (old.get("prompt_sha") == TK.prompt_sha(prompts[s["i"]]) and (old.get("workflow") or "未记录") == wf_label
                and old.get("seed") == seeds[s["i"]])
        if a.seed is None and old and same and not (state.get("pending", s["i"]) or {}).get("task"):
            seeds[s["i"]] = random.randint(1, 2 ** 31 - 1)
            auto_seed.append(s["i"])
    if auto_seed:
        print("段%s 的提示词和工作流都没改、种子也一样，重跑会得到一模一样的结果：已自动换成随机种子。想固定种子就用 --seed <整数>。"
              % "、".join(map(str, auto_seed)))
    to_archive = [s["i"] for s in todo if os.path.isfile(os.path.join(od, "clips", "seg_%02d.mp4" % s["i"]))
                  and not (state.get("pending", s["i"]) or {}).get("task")]
    print("尺寸 %d×%d，实例 %s，应用 %s" % (w, h, a.instance, C.app_id()))
    print("工作流版本：%s%s" % (wf_label, "（记录于 %s）" % wf_at if wf_at else "（用户改过工作流时用 check_setup.py --set-workflow 记一个）"))
    kept_wf = {k: (v.get("workflow") or "未记录") for k, v in state.d["results"].items()
               if int(k) in done and (v.get("workflow") or "未记录") != wf_label}
    if kept_wf:
        print("注意：沿用的段%s 是在别的工作流版本下生成的（%s），和这次新生成的放在一起，前后画面可能不一致。"
              % ("、".join(sorted(kept_wf, key=int)), "、".join(sorted(set(kept_wf.values())))))
    if to_archive:
        print("段%s 现有的片段会先收进历史版本（takes.py 可以换回来），不会被覆盖。" % "、".join(map(str, to_archive)))
    print("会员档位：%s，并发 %d（共 %d 段，本次处理 %d 段，沿用已有 %d 段）" % (cfg.get("plan", "未记录"), conc, len(segs), len(todo), len(done)))
    if resumable:
        print("其中段%s 有上次已提交、未完成的云端任务，会先续上它们，不会重复提交。" % "、".join(map(str, resumable)))
    if "concurrency" not in cfg and a.concurrency is None:
        print("提示：还没记录会员档位，只按 1 路并发；可用 check_setup.py --set-plan 记录。")
    for s in todo:
        old = state.get("seeds", s["i"])
        note = "" if old is None else ("（和上次相同：输入没改的话结果也会相同）" if old == seeds[s["i"]] else "（上次是 %s）" % old)
        print("  段%d：%.1f 秒  提示词 %d 字  %s  种子 %s%s" % (s["i"], s["duration"], len(prompts[s["i"]]), os.path.basename(s["audio"]), seeds[s["i"]], note))
    if a.instance == "ultra":
        print("注意：ultra 未经用户授权，请确认用户同意后再用。")
    todo_s = sum(s["duration"] for s in todo)
    if cost_review_required():
        lo, mid, hi = C.cost_estimate(todo_s)[:3]
        basis = C.cost_estimate(todo_s)[3]
        if todo:
            print("预计费用：约 %d RH 币（%d–%d），%s；以 RunningHub 后台为准。" % (mid, lo, hi, basis))
        _cli.report(estimate_coins={"low": lo, "mid": mid, "high": hi, "basis": basis})
    _cli.report(workflow=wf_label, instance=a.instance, concurrency=conc, total_segments=len(segs),
                todo=[{"i": s["i"], "seconds": s["duration"], "seed": seeds[s["i"]]} for s in todo],
                reused=sorted(done), resumable=resumable, will_archive=to_archive, auto_seed=auto_seed,
                todo_seconds=round(sum(s["duration"] for s in todo), 2))
    if not a.yes:
        approved = voice_approval(a.workdir)
        if _review.read(a.workdir) is not None:
            voice_review = _review.stage_status(a.workdir, "voice")
            approved = voice_review.get("quote") if voice_review["state"] in ("approved", "skipped") else None
        if approved:
            print("配音试听：用户已确认（“%s”）。" % approved)
        else:
            print("配音试听：还没有用户确认这一版配音。提交前先停下，把 out/merged.wav 发给用户试听，和段数、%s一起确认；"
                  "用户回复确认后用 --voice-approved \"用户回复的原话\" --yes 提交。" % ("预计费用" if cost_review_required() else "生成方案"))
        print("[预览] 没有联网，也没有产生费用。确认后加 --yes。")
        _cli.report(preview=True, voice_approved=approved, wait_for_user=not approved)
        if _review.read(a.workdir) is not None:
            _cli.report(review_context=review_context, wait_for_user=True,
                        next="展示动作、时长%s；用 review.py present generation 准备本次参数并等待用户确认" % ("与费用" if cost_review_required() else "与生成方案"))
        return
    if not todo:
        print("没有需要提交的段。")
        return
    key = C.require_key()
    if new_submission and not resume_authorized:
        _review.consume_generation(a.workdir)
    os.makedirs(os.path.join(od, "clips"), exist_ok=True)
    img_val = None if resume_only else with_retry(lambda: C.upload(info["uploaded_image"], key), "上传图片")
    if not resume_only:
        log("图片已上传")
    for s in todo:
        state.set("seeds", s["i"], seeds[s["i"]])
    for i in to_archive:
        n = TK.archive(od, i, old_results.get(str(i)))
        if n:
            log("[段%d] 原来的片段已收进历史版本 %d" % (i, n))
    meta = {"workflow": wf_label, "app_id": C.app_id(), "note": a.note}
    errors = {}
    review_required = False
    with ThreadPoolExecutor(max_workers=max(1, conc)) as ex:
        futs = {ex.submit(run_one, s, prompts[s["i"]], img_val, w, h, key, a.instance,
                          os.path.join(od, "clips"), a.timeout_min, a.retries, state, seed=seeds[s["i"]],
                          meta={**meta, "prompt": prompts[s["i"]], "prompt_sha": TK.prompt_sha(prompts[s["i"]])}, queue_min=a.queue_min,
                          allow_submit=not resume_only and (new_submission or _review.read(a.workdir) is None), budget=budget): s["i"] for s in todo}
        for f, i in futs.items():
            try:
                f.result()
            except PermissionError as e:
                print(str(e), "请重新获取 Key。")
                sys.exit(_cli.KEY_REJECTED)
            except Exception as e:
                review_required = review_required or isinstance(e, NeedsReview)
                errors[i] = str(e)
                state.set("errors", i, str(e))
    results = {int(k): v for k, v in state.d["results"].items()}
    total, known, coins = 0.0, 0, 0.0
    print("\n=== 汇总 ===")
    for i in sorted(results):
        if not cost_review_required():
            print("段%d：成功" % i)
            continue
        u = results[i].get("usage") or {}
        cost, cn = u.get("consumeMoney"), u.get("consumeCoins")
        try:
            if cost not in (None, ""):
                total += float(cost)
                known += 1
            elif cn not in (None, ""):
                coins += float(cn)
                known += 1
        except ValueError:
            pass
        shown = ("%s 元" % cost) if cost not in (None, "") else ("%s RH 币" % cn) if cn not in (None, "") else "接口没返回"
        print("段%d：成功 费用=%s 用时=%ss" % (i, shown, u.get("taskCostTime")))
    for i in sorted(errors):
        print("段%d：失败 → %s" % (i, errors[i][:300]))
    if not cost_review_required():
        pass  # Usage remains in the private ledger; direct mode has no cost report.
    elif results and known == 0:      # 有的账号或应用的接口不返回费用：合计不是 0 元，是不知道
        print("本次费用：接口没有返回费用，不能按 0 元算——请在 RunningHub 后台查看。")
    elif known < len(results):
        print("本次已知费用合计 = %s（只有 %d/%d 段返回了费用，实际更多，以后台为准）" % (round(total, 4), known, len(results)))
    else:
        print("本次已知费用合计 = %s%s" % ("%s 元" % round(total, 4) if total else "", ("%s%d RH 币" % ("，" if total else "", coins)) if coins else ""))
    billing_report = ({"cost_known": round(total, 4) if total else None, "coins_known": round(coins) if coins else None,
                       "cost_reported_segments": known} if cost_review_required() else {})
    _cli.report(preview=False, done=sorted(results), failed={str(k): v[:300] for k, v in errors.items()}, **billing_report,
                result_json=os.path.join(od, "result.json"))
    if errors:
        if review_required:
            _cli.report(review_stage="generation", wait_for_user=True,
                        next="原任务已结束；用 review.py present generation 展示新方案%s，等用户回复后再提交" % ("与费用" if cost_review_required() else "并创建新的生成任务"))
            sys.exit(_cli.NEED_USER)
        print("有失败的段，未拼接。排查后重跑本脚本即可：已成功的段不会重复提交，仍在云端的任务会被续上；"
              "想强制重做某段用 --only <段号>。不要自行换更大的实例。")
        sys.exit(_cli.REMOTE_FAILED)


if __name__ == "__main__":
    _cli.run(main)
