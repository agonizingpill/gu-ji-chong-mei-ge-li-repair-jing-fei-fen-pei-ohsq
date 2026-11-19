import os
import redis
from sqlalchemy import select
from .models import Accession,Isolation,RiskAssessment
from .common import get,reject,finish
from .catalog_service import latest_observation
queue=redis.Redis.from_url(os.getenv('REDIS_URL','redis://redis:6379/0'),decode_responses=True)
def request_risk(db,data,user):
    folio=get(db,Accession,data.accession_id,True);obs=latest_observation(db,folio.id)
    reject(not obs,'先录入病害测量')
    reject(not db.scalar(select(Isolation).where(Isolation.accession_id==folio.id,Isolation.active==True)),'先隔离入柜再评估')
    queued=db.scalar(select(RiskAssessment).where(RiskAssessment.observation_id==obs.id,RiskAssessment.status.in_(['queued','running'])))
    reject(queued is not None,'该次观测仍在排队')
    # 重试分级不重复生成同次测量的结论：成功、待复核或跨档待补测的记录直接复用，仅失败记录重新入队。
    existing=db.scalar(select(RiskAssessment).where(RiskAssessment.observation_id==obs.id).order_by(RiskAssessment.id.desc()))
    if existing and existing.status in ('done','reviewed','pending'):return finish(db,user,'复用既有分级结论',existing)
    if existing and existing.status=='failed':
        existing.status='queued';existing.error='';row=existing
    else:
        row=RiskAssessment(accession_id=folio.id,observation_id=obs.id);db.add(row);db.flush()
    queue.rpush('folio:risk',row.id);return finish(db,user,'提交风险分级',row)
def review(db,id,data,user):
    row=get(db,RiskAssessment,id,True)
    reject(row.status!='done','仅已完成分级可复核')
    reject(latest_observation(db,row.accession_id).id!=row.observation_id,'病害已复测，请重新运行最新分级')
    reject(row.provisional,'区间测量的暂定等级不能直接复核，请补测为实测值后重新分级')
    reject(len(row.candidate_levels)>1,'候选等级跨档，等待补测收窄为实测值后再复核')
    row.status='reviewed';row.approved=data.level;row.review_note=data.note
    return finish(db,user,'风险主管复核',row)
