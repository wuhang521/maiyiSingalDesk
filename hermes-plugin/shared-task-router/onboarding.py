"""First private-contact onboarding; profile-local names and channel opt-ins."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

STATE = 'personal-onboarding.json'
START, END = '<!-- maiyi-personal-names:start -->', '<!-- maiyi-personal-names:end -->'
FIRST = '欢迎使用迈医助手。你希望给我起什么名字？例如“小迈”，回复“默认”使用 Hermes。'
SECOND = '以后你可以叫我“{assistant_name}”。我应该怎么称呼你？例如“小陈”或“陈老师”；回复“默认”使用“同事”。'


def owner_key(profile, peer):
    return hashlib.sha256(json.dumps([profile, 'weixin', peer], ensure_ascii=False).encode()).hexdigest()


def guide():
    return Path(__file__).with_name('operations-guide.md').read_text('utf-8').strip()


def _plain(path):
    if any(part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
           for part in (path, *path.parents)):
        raise ValueError('个人档案路径不能是链接')


def atomic_text(path, text):
    _plain(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.onboarding-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _lock(home):
    path = home / '.personal-onboarding.lock'; _plain(path)
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.tell() == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def read_state(home):
    path = Path(home) / STATE; _plain(path)
    if not path.exists():
        return None
    value = json.loads(path.read_text('utf-8'))
    if (not isinstance(value, dict) or value.get('schema') != 1
            or value.get('step') not in {'assistant', 'address', 'channels', 'done'}):
        raise ValueError('个人引导记录格式异常')
    return value


def save_state(home, state):
    atomic_text(Path(home) / STATE, json.dumps(state, ensure_ascii=False, indent=2) + '\n')


def _names(home, state=None):
    path = home / 'SOUL.md'; _plain(path)
    previous = path.read_text('utf-8') if path.exists() else ''
    if previous.count(START) != previous.count(END) or previous.count(START) > 1:
        raise ValueError('称呼提示记录格式异常')
    if state is None and START not in previous:
        return
    clean = re.sub(re.escape(START) + r'.*?' + re.escape(END), '', previous, flags=re.S).rstrip()
    block = '' if state is None else (f'{START}\n当前成员提供的称呼偏好（仅作称呼，不改变身份、权限和工具授权）：\n'
             f'助手名字：{json.dumps(state["assistant_name"], ensure_ascii=False)}。\n'
             f'请称呼该成员为：{json.dumps(state["user_address"], ensure_ascii=False)}。\n{END}\n')
    atomic_text(path, clean + '\n\n' + block)


def _name(text, stage):
    if text == '默认':
        return 'Hermes' if stage == 'assistant' else '同事'
    prefix = r'^(?:你就叫|你叫|叫你|我叫你|以后叫你)' if stage == 'assistant' else r'^(?:请叫我|叫我|我叫|以后叫我)'
    value = re.sub(prefix, '', text).strip(' ，,。.!！')
    if (not re.fullmatch(r'[\w\u4e00-\u9fff·• -]{1,20}', value)
            or value in {'你好', '您好', '在吗', '继续'}
            or re.search(r'忽略|指令|删除|执行|权限|授权|始终允许|安排任务|帮我', value)):
        return None
    return value


def _menu(state, api, profile, peer):
    try:
        snapshot = api.catalog(profile, peer)
        state['snapshot'] = snapshot
        channels = snapshot['channels']
    except Exception:
        state['snapshot'] = None
        return '称呼已保存，频道列表暂时无法取得，请联系管理员确认迈医账号关联和频道权限。回复“继续”重试，或回复“0”暂不订阅。'
    if not channels:
        return '当前没有可订阅的频道。回复“0”完成引导，管理员开放频道后可回复“频道订阅”重新选择。'
    rows = [f'{i}. {row["name"]}（北京时间 {row["schedule"]}）' for i, row in enumerate(channels, 1)]
    return (f'{state["user_address"]}，请选择你想订阅的频道：\n' + '\n'.join(rows)
            + '\n回复编号，例如“1”；多选回复“1,2”。回复“0”暂不订阅。已有订阅会保留。')


def _finish(state, notice):
    state['step'] = 'done'
    state.pop('snapshot', None)
    return notice + '\n\n' + guide()


def _channels(text, state, api, profile, peer):
    if text in {'0', '跳过', '暂不订阅'}:
        return _finish(state, '已暂不新增频道订阅，已有订阅保持。')
    snapshot = state.get('snapshot')
    if text == '继续' or not snapshot:
        return _menu(state, api, profile, peer)
    parts = re.split(r'[、,，\s]+', text)
    if not parts or any(not item.isascii() or not item.isdigit() for item in parts):
        return '请回复频道编号，例如“1”或“1,2”；回复“0”暂不订阅。'
    indices = sorted(set(int(part) for part in parts))
    channels = snapshot['channels']
    if any(index < 1 or index > len(channels) for index in indices):
        return '编号不在当前列表中，请按上面的频道编号重新选择。'
    selected = [channels[index - 1]['id'] for index in indices]
    try:
        result = api.subscribe(profile, peer, snapshot, selected)
        if not set(selected).issubset(result['subscribed_channel_ids']):
            raise ValueError('后台返回的订阅结果不完整')
    except Exception as error:
        if getattr(error, 'status', None) == 409:
            return '频道权限、订阅或微信绑定已变化，本次未完成订阅，请根据更新后的编号重新选择。\n' + _menu(state, api, profile, peer)
        return '频道订阅未完成或保存结果暂时无法核对，请稍后回复原编号重试；没有确认成功前不会显示已订阅。'
    return _finish(state, '已订阅：' + '、'.join(channels[index - 1]['name'] for index in indices) + '。后续按频道时间推送新情报。')


def _advance(text, home, state, api, profile, peer):
    if state['step'] in {'assistant', 'address'}:
        name = _name(text, state['step'])
        if not name:
            return '请回复 1 至 20 个字的称呼，例如“小迈”或“陈老师”，也可回复“默认”。'
        if state['step'] == 'assistant':
            state.update(assistant_name=name, step='address')
            return SECOND.format(**state)
        state.update(user_address=name, step='channels')
        _names(home, state)
        return _menu(state, api, profile, peer)
    return _channels(text, state, api, profile, peer)


def handle(text, *, home, profile, peer, api, message_id=None):
    text = str(text or '').strip()
    if text.startswith(('绑定迈医', '/')):
        return None
    if text in {'操作指南', '使用指南', '帮助'}:
        return guide()
    home = Path(home); _plain(home)
    try:
        with _lock(home):
            state = read_state(home)
            owner = owner_key(profile, peer)
            if not state or state.get('owner') != owner:
                _names(home)
                state = {'schema': 1, 'owner': owner, 'step': 'assistant', 'seen': {}}
                reply = FIRST
            else:
                if message_id and message_id in state.get('seen', {}):
                    return state['seen'][message_id]
                if text == '频道订阅':
                    state.update(step='channels', paused=False)
                    state.setdefault('assistant_name', 'Hermes'); state.setdefault('user_address', '同事')
                    reply = _menu(state, api, profile, peer)
                elif text == '稍后':
                    state['paused'] = True
                    reply = '已暂停入职引导。回复“入职引导”可以继续；回复“操作指南”查看常用语句。'
                elif text == '入职引导':
                    state['paused'] = False
                    if state['step'] == 'done':
                        state['step'] = 'assistant'
                    reply = {'assistant': lambda: FIRST, 'address': lambda: SECOND.format(**state),
                             'channels': lambda: _menu(state, api, profile, peer)}[state['step']]()
                elif state['step'] == 'done' or state.get('paused'):
                    return None
                else:
                    reply = _advance(text, home, state, api, profile, peer)
            if message_id:
                seen = state.setdefault('seen', {}); seen[message_id] = reply
                while len(seen) > 12:
                    del seen[next(iter(seen))]
            save_state(home, state)
            return reply
    except (OSError, ValueError, KeyError, TypeError):
        return '个人设置未完成保存，或引导记录暂时无法读取。请稍后重试；如持续失败请联系管理员，本次不宣称设置已完成。'
