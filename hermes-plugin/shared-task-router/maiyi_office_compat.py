"""Classification only: leave explicit office documents to Hermes office tools."""
import re

_task_command=re.compile(r"(?:安排|创建|新建|新增|分配|发布|修改|完成|取消|删除|关闭|重开|查询|查看)(?:一条|一个|一下|我的|这些|这项|现有|共享)?(?:共享)?任务")
_office_prefix=re.compile(r"^(?:【[^】]{1,30}】\s*)?办公\s*[:：]")
_number=re.compile(r"(?<![A-Z0-9])(?:MY|YZ)-(?:CC|BX|ZF)-\d{4}-\d{4,}(?!\d)",re.I)
_business=re.compile(r"报销|出差申请|出差单|结束报告|出差台账|费用支付|付款结果|审批|票据")

def is_office_request(text):
    if not isinstance(text,str):return False
    text=text.strip()
    if text.split(maxsplit=1)[0:1]==['/office']:return True
    if _task_command.search(text):return False
    return bool(_office_prefix.search(text) or (_number.search(text) and _business.search(text)))
