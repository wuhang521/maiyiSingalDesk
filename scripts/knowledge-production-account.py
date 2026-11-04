"""Create/clean one temporary release-check identity, never an existing user."""
import argparse,json,os,secrets,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('mode',choices=['create','cleanup']);p.add_argument('--marker',type=Path);p.add_argument('--no-channel',action='store_true');p.add_argument('--synthetic-weixin',action='store_true');a=p.parse_args()
os.chdir('/opt/signaldesk-task');sys.path.insert(0,os.getcwd());os.umask(0o077)
from app.database import Base,SessionLocal
from app import knowledge_models
from app.models import Staff,StaffChannel,UserAccount,UserSession
from app.auth import hash_password
from app.knowledge_service import purge_item
from app.models import utcnow
from sqlalchemy import select,delete,or_
if a.mode=='create':
    name='kb-release-check-'+secrets.token_hex(6);password=secrets.token_urlsafe(32)
    with SessionLocal() as db:
        staff=Staff(name='知识库发布合成验收 '+name[-6:]);db.add(staff);db.flush()
        user=UserAccount(username=name,staff_id=staff.id,role='member',password_hash=hash_password(password));db.add(user);db.flush()
        recipient=''
        if a.synthetic_weixin:
            assert not a.no_channel
            recipient='synthetic-kb-'+secrets.token_hex(12);db.add(StaffChannel(staff_id=staff.id,platform='weixin',recipient_id=recipient,enabled=True))
        db.commit()
        marker=Path('/var/tmp')/('signaldesk-knowledge-smoke-'+secrets.token_hex(6)+'.json')
        marker.write_text(json.dumps({'user_id':user.id,'staff_id':staff.id,'username':name}));marker.chmod(0o600)
        print(json.dumps({'username':name,'password':password,'marker':str(marker),'synthetic_recipient':recipient}))
else:
    assert a.marker and a.marker.parent==Path('/var/tmp') and a.marker.name.startswith('signaldesk-knowledge-smoke-') and not a.marker.is_symlink()
    value=json.loads(a.marker.read_text());uid=value['user_id'];sid=value['staff_id']
    with SessionLocal() as db:
        user=db.get(UserAccount,uid);assert user and user.username==value['username'] and user.username.startswith('kb-release-check-') and user.staff_id==sid
        itemids=list(db.scalars(select(knowledge_models.KnowledgeItem.id).where(knowledge_models.KnowledgeItem.owner_id==uid)))
        for item_id in itemids:
            item=db.get(knowledge_models.KnowledgeItem,item_id);item.deleted_at=utcnow();db.commit()
            purge_item(db,uid,item_id,item.revision)
        usageids=select(knowledge_models.KnowledgeCloudUsage.id).where(knowledge_models.KnowledgeCloudUsage.owner_id==uid)
        db.execute(delete(knowledge_models.KnowledgeProviderTrace).where(knowledge_models.KnowledgeProviderTrace.usage_id.in_(usageids)))
        db.execute(delete(knowledge_models.KnowledgeCostReservation).where(knowledge_models.KnowledgeCostReservation.usage_id.in_(usageids)))
        for table in reversed(Base.metadata.sorted_tables):
            if not table.name.startswith('knowledge_'):continue
            conditions=[]
            if 'owner_id' in table.c:conditions.append(table.c.owner_id==uid)
            if itemids and 'item_id' in table.c:conditions.append(table.c.item_id.in_(itemids))
            if table.name=='knowledge_team_member':conditions.append(table.c.user_id==uid)
            if conditions:db.execute(delete(table).where(or_(*conditions)))
        db.execute(delete(UserSession).where(UserSession.user_id==uid));db.execute(delete(StaffChannel).where(StaffChannel.staff_id==sid))
        db.execute(delete(UserAccount).where(UserAccount.id==uid));db.execute(delete(Staff).where(Staff.id==sid));db.commit()
        assert db.get(UserAccount,uid) is None and db.get(Staff,sid) is None
    a.marker.unlink();print(json.dumps({'cleaned':True,'existing_users_changed':False,'synthetic_only':True}))
