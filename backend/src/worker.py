import os,time,logging
from pathlib import Path
from sqlalchemy import select
from .database import Session
from .models import RiskAssessment,Observation
from .risk_service import queue
from .algorithm import build_model,candidate_levels
ARTIFACTS_DIR=os.getenv('ARTIFACTS_DIR','/artifacts')
logging.basicConfig(level=logging.INFO)
def run_assessment(db,row):
    obs=db.get(Observation,row.observation_id)
    model,metrics=build_model()
    h_hi=obs.humidity if obs.humidity_max is None else obs.humidity_max
    m_hi=obs.mold if obs.mold_max is None else obs.mold_max
    candidates=candidate_levels(model,obs.humidity,h_hi,obs.holes,obs.mold,m_hi)
    # 实测值（无区间）时候选退化为单点预测；区间且唯一档给暂定等级，跨档则等补测。
    is_range=obs.humidity_max is not None or obs.mold_max is not None
    row.candidate_levels=candidates
    row.provisional=is_range and len(candidates)==1
    row.predicted=candidates[0] if len(candidates)==1 else None
    row.metrics=metrics
    row.status='done' if len(candidates)==1 else 'pending'
    row.error='' if len(candidates)==1 else '候选等级跨 %s 档，等待补测实测值' % '、'.join(candidates)
    Path(ARTIFACTS_DIR).mkdir(exist_ok=True)
    Path(f'{ARTIFACTS_DIR}/risk-{row.id}.json').write_text(__import__('json').dumps({'candidate_levels':candidates,'provisional':row.provisional,**metrics},ensure_ascii=False))
def main():
    while True:
        Path('/tmp/worker-heartbeat').touch()
        try:
            item=queue.blpop('folio:risk',timeout=2)
            if not item:continue
            with Session() as db:
                row=db.get(RiskAssessment,int(item[1]))
                if not row or row.status!='queued':continue
                row.status='running';db.commit()
                try:
                    run_assessment(db,row)
                except Exception as exc:row.status='failed';row.error=str(exc)[:200]
                db.commit()
        except Exception:logging.exception('worker iteration');time.sleep(1)
if __name__=='__main__':main()
