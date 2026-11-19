"""潮湿卷册分级新规则集成测试（SQLite + fakeredis，不依赖 docker）。

运行：/workspace/.venv/bin/python -I -u tests/test_range_risk.py

并发说明：SQLite 不支持 SELECT ... FOR UPDATE（SQLAlchemy 渲染为空操作），
锁内重读与串行化逻辑仍在服务层真实执行；PostgreSQL 上同样代码即行锁。
"""
import os,sys,tempfile
_TMP=tempfile.mkdtemp(prefix='folio-test-')
os.environ['DATABASE_URL']='sqlite:///'+_TMP+'/test.db?timeout=30'
os.environ['JWT_SECRET']='test-secret'
os.environ['ARTIFACTS_DIR']=_TMP+'/artifacts'
sys.path.insert(0,'/workspace/backend')
import fakeredis
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import select
from src.database import Base,engine,Session
from src import risk_service
from src.worker import run_assessment
from src.models import *
from src.schemas import ObservationInput,HandoverInput
from src.auth import password_hash

risk_service.queue=fakeredis.FakeRedis(decode_responses=True)
from src.app import app
from src import seed
Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
seed.seed()

PASS=[];FAIL=[]
def check(name,cond,detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ok ' if cond else 'FAIL ')+name+((' :: '+detail) if detail and not cond else ''),flush=True)

def login(role):
    r=client.post('/auth/login',json={'username':role,'password':'Folio@2026'})
    return {'Authorization':'Bearer '+r.json()['token']}

def run_job(ra_id):
    with Session() as db:
        row=db.get(RiskAssessment,ra_id)
        row.status='running';db.commit()
        run_assessment(db,row);db.commit()

with TestClient(app) as client:
    op=login('operator');sup=login('supervisor');vw=login('viewer')
    fid=max(a['id'] for a in client.get('/workspace',headers=op).json()['accessions'])+1

    # ---- 1. 范围登记与校验 ----
    r=client.post('/accessions',headers=op,json={'code':'GB-T001','title':'范围测试卷','era':'清代','paper':'竹纸'})
    check('新卷入藏',r.status_code==200,str(r.json()))
    r=client.post('/observations',headers=op,json={'accession_id':fid,'humidity':62,'holes':0,'mold':0.5,'humidity_max':58,'note':'上界小于下界'})
    check('范围上界小于下界被拒',r.status_code==422,str(r.json()))
    r=client.post('/observations',headers=op,json={'accession_id':fid,'humidity':58,'holes':0,'mold':0,'humidity_max':62,'mold_max':1,'note':'跨档范围（霉面积跨树阈值 0.78）'})
    check('范围测量登记成功',r.status_code==200,str(r.json()))
    obs_cross=r.json()
    check('范围字段回传',obs_cross['humidity_max']==62 and obs_cross['mold_max']==1)
    r=client.post('/isolations',headers=op,json={'accession_id':fid,'cabinet_id':1})
    check('入柜',r.status_code==200,str(r.json()))

    # ---- 2. 跨档范围 => pending + 候选等级 ----
    r=client.post('/risks',headers=op,json={'accession_id':fid})
    check('分级提交',r.status_code==200,str(r.json()))
    ra_id=r.json()['id'];run_job(ra_id)
    ra=next(x for x in client.get('/workspace',headers=op).json()['risk_assessments'] if x['id']==ra_id)
    check('跨档 => pending',ra['status']=='pending',ra['status'])
    check('跨档候选含 low/medium',set(ra['candidate_levels'])=={'low','medium'},str(ra['candidate_levels']))
    check('跨档不产出预测值',ra['predicted'] is None)
    check('跨档非暂定',ra['provisional'] is False)

    # ---- 3. 跨档不能复核 ----
    r=client.post(f'/risks/{ra_id}/review',headers=sup,json={'level':'low','note':'尝试直接复核跨档'})
    check('跨档复核被拒',r.status_code==409,str(r.json()))

    # ---- 4. 重试分级不重复生成同次测量结论 ----
    r=client.post('/risks',headers=op,json={'accession_id':fid})
    check('跨档重试复用同一记录',r.status_code==200 and r.json()['id']==ra_id,str(r.json()))
    check('复用不重新排队（仍 pending）',r.json()['status']=='pending')
    with Session() as db:
        row=db.get(RiskAssessment,ra_id);row.status='failed';row.error='模拟失败';db.commit()
    r=client.post('/risks',headers=op,json={'accession_id':fid})
    check('失败记录重试沿用原 id 并重新排队',r.status_code==200 and r.json()['id']==ra_id and r.json()['status']=='queued',str(r.json()))
    run_job(ra_id)
    check('失败重跑后仍为 pending',client.get('/workspace',headers=op).json()['risk_assessments'][-1]['status']=='pending')

    # ---- 5. 单档范围 => 暂定等级，不能复核、不能开工 ----
    fid2=client.post('/accessions',headers=op,json={'code':'GB-T002','title':'暂定等级卷','era':'清代','paper':'竹纸'}).json()['id']
    client.post('/observations',headers=op,json={'accession_id':fid2,'humidity':40,'holes':0,'mold':0,'humidity_max':45,'mold_max':0.4,'note':'低风险区间'})
    client.post('/isolations',headers=op,json={'accession_id':fid2,'cabinet_id':3})
    ra2_id=client.post('/risks',headers=op,json={'accession_id':fid2}).json()['id'];run_job(ra2_id)
    ra2=next(x for x in client.get('/workspace',headers=op).json()['risk_assessments'] if x['id']==ra2_id)
    check('单档范围 => done 且暂定',ra2['status']=='done' and ra2['provisional'] is True,str(ra2))
    check('暂定候选仅 low',ra2['candidate_levels']==['low'] and ra2['predicted']=='low')
    r=client.post(f'/risks/{ra2_id}/review',headers=sup,json={'level':'low','note':'尝试复核暂定'})
    check('暂定等级复核被拒',r.status_code==409,str(r.json()))
    r=client.post('/treatments',headers=sup,json={'accession_id':fid2,'name':'暂定卷方案','estimated_cents':10000,'steps':['干燥除霉','平整验收']})
    check('暂定未复核不能建方案',r.status_code==409,str(r.json()))

    # ---- 6. 补测为实测值后重新分级复核；旧结论归档但不再有效 ----
    r=client.post('/observations',headers=op,json={'accession_id':fid2,'humidity':42,'holes':0,'mold':0,'note':'补测实测值'})
    check('补测登记',r.status_code==200)
    ra3_id=client.post('/risks',headers=op,json={'accession_id':fid2}).json()['id']
    check('新测量生成新分级单',ra3_id!=ra2_id)
    run_job(ra3_id)
    ra3=next(x for x in client.get('/workspace',headers=op).json()['risk_assessments'] if x['id']==ra3_id)
    check('实测值非暂定',ra3['provisional'] is False and ra3['candidate_levels']==['low'])
    r=client.post(f'/risks/{ra3_id}/review',headers=sup,json={'level':'low','note':'补测后复核低风险'})
    check('实测复核通过',r.status_code==200,str(r.json()))
    old=next(x for x in client.get('/workspace',headers=op).json()['risk_assessments'] if x['id']==ra2_id)
    check('旧暂定记录保留归档',old['provisional'] is True)

    # ---- 7. 旧测量预测晚到：照常归档，当前结论跟最新测量 ----
    fid3=client.post('/accessions',headers=op,json={'code':'GB-T003','title':'晚到预测卷','era':'清代','paper':'竹纸'}).json()['id']
    client.post('/observations',headers=op,json={'accession_id':fid3,'humidity':74,'holes':8,'mold':12,'note':'旧测量高风险'})
    client.post('/isolations',headers=op,json={'accession_id':fid3,'cabinet_id':3})
    late_id=client.post('/risks',headers=op,json={'accession_id':fid3}).json()['id']
    client.post('/observations',headers=op,json={'accession_id':fid3,'humidity':52,'holes':0,'mold':0,'note':'复测合格'})
    new_id=client.post('/risks',headers=op,json={'accession_id':fid3}).json()['id']
    run_job(late_id);run_job(new_id)
    ws=client.get('/workspace',headers=op).json()
    late=next(x for x in ws['risk_assessments'] if x['id']==late_id)
    new=next(x for x in ws['risk_assessments'] if x['id']==new_id)
    check('旧单晚到照常归档为 done/high',late['status']=='done' and late['predicted']=='high',str(late))
    check('新单对应最新观测 low',new['predicted']=='low')
    r=client.post(f'/risks/{late_id}/review',headers=sup,json={'level':'high','note':'旧测量复核'})
    check('旧观测复核被拒（须最新测量）',r.status_code==409,str(r.json()))
    r=client.post(f'/risks/{new_id}/review',headers=sup,json={'level':'low','note':'最新测量复核'})
    check('最新测量复核通过',r.status_code==200,str(r.json()))
    with Session() as db:
        from src.catalog_service import reviewed_risk
        rr=reviewed_risk(db,fid3)
        check('当前有效复核跟随最新测量',rr is not None and rr.id==new_id and rr.approved=='low')

    # ---- 8. 出柜以最新登记测量为准 ----
    client.post('/observations',headers=op,json={'accession_id':fid3,'humidity':65,'holes':0,'mold':0,'note':'返潮复测'})
    r=client.post('/handovers',headers=sup,json={'accession_id':fid3,'kind':'storage','recipient':'库房陈宁','note':'尝试出柜'})
    check('最新测量不合格拒绝出柜',r.status_code==409,str(r.json()))
    client.post('/observations',headers=op,json={'accession_id':fid3,'humidity':55,'holes':0,'mold':0,'note':'再次干燥合格'})
    re_id=client.post('/risks',headers=op,json={'accession_id':fid3}).json()['id'];run_job(re_id)
    client.post(f'/risks/{re_id}/review',headers=sup,json={'level':'low','note':'再次复核'})
    r=client.post('/handovers',headers=sup,json={'accession_id':fid3,'kind':'storage','recipient':'库房陈宁','note':'尝试出柜'})
    check('风险通过后仍被工序/方案条件拦下',r.status_code==409,str(r.json()))

    # ---- 9. 满柜拒绝（顺序确定性）与同册重复入柜 ----
    c=client.post('/cabinets',headers=sup,json={'code':'QX','capacity':1}).json()
    fA=client.post('/accessions',headers=op,json={'code':'GB-T005','title':'抢位甲','era':'清代','paper':'竹纸'}).json()['id']
    fB=client.post('/accessions',headers=op,json={'code':'GB-T006','title':'抢位乙','era':'清代','paper':'竹纸'}).json()['id']
    client.post('/observations',headers=op,json={'accession_id':fA,'humidity':50,'holes':0,'mold':0,'note':'x'})
    client.post('/observations',headers=op,json={'accession_id':fB,'humidity':50,'holes':0,'mold':0,'note':'x'})
    check('容量1首次入柜成功',client.post('/isolations',headers=op,json={'accession_id':fA,'cabinet_id':c['id']}).status_code==200)
    r=client.post('/isolations',headers=op,json={'accession_id':fB,'cabinet_id':c['id']})
    check('满柜第二册被拒',r.status_code==409 and '满' in r.json()['detail'],str(r.json()))
    r=client.post('/isolations',headers=op,json={'accession_id':fA,'cabinet_id':c['id']})
    check('同册重复入柜被拒',r.status_code==409,str(r.json()))

# ---- 10. 服务层并发：补测与出柜同时进行，锁内重读最新测量 ----
from src import catalog_service as catalog,handover_service as handovers
with Session() as db:
    operator=db.scalar(select(User).where(User.username=='operator'))
    supervisor=db.scalar(select(User).where(User.username=='supervisor'))

with Session() as db:
    folio=Accession(code='GB-T007',title='并发卷',era='清代',paper='竹纸',status='isolated');db.add(folio);db.flush()
    fid4=folio.id
    db.add(Observation(accession_id=folio.id,humidity=50,holes=0,mold=0,note='合格基线'));db.flush()
    db.add(Isolation(accession_id=folio.id,cabinet_id=1))
    ra=RiskAssessment(accession_id=folio.id,observation_id=db.scalar(select(Observation).where(Observation.accession_id==folio.id)).id,status='reviewed',predicted='low',approved='low',provisional=False,candidate_levels=['low']);db.add(ra);db.flush()
    grant=Grant(code='GF-C',name='并发专项',budget_cents=500000);db.add(grant);db.flush()
    plan=Treatment(accession_id=folio.id,name='并发卷方案',estimated_cents=10000,status='completed');db.add(plan);db.flush()
    for i,n in enumerate(['干燥除霉','平整验收'],1):
        db.add(Step(treatment_id=plan.id,name=n,position=i,done=True,evidence='签证'))
    grant.spent_cents=9000
    db.add(Encumbrance(treatment_id=plan.id,grant_id=grant.id,amount_cents=10000,actual_cents=9000,status='settled'))
    db.commit()

def service_call(fn,*args):
    try:
        with Session() as db:
            fn(db,*args)
        return ('ok',200,'')
    except HTTPException as e:return ('http',e.status_code,e.detail)
    except Exception as e:return ('err',500,str(e))

# 守卫：两条写路径都必须对卷册加行锁，PG 上后到事务阻塞并在锁内重读最新测量。
import inspect
check('补测路径带卷册行锁','get(db,Accession,data.accession_id,True)' in inspect.getsource(catalog.observe))
check('出柜路径带卷册行锁','get(db,Accession,data.accession_id,True)' in inspect.getsource(handovers.handover))

obs_input=ObservationInput(accession_id=fid4,humidity=70,holes=0,mold=0,note='并发返潮')
hand_input=HandoverInput(accession_id=fid4,kind='storage',recipient='库房陈宁',note='并发出柜')

# 顺序 A：出柜事务先拿锁 → 放行并释放；随后补测被 released 状态拦截。
r_hand=service_call(handovers.handover,hand_input,supervisor)
r_obs=service_call(catalog.observe,obs_input,operator)
check('顺序A 出柜成功',r_hand[1]==200,str(r_hand))
check('顺序A 出柜后补测被拒',r_obs[1]==409,str(r_obs))
with Session() as db:
    check('顺序A 卷册已释放',db.get(Accession,fid4).status=='released')

# 重置回在柜合格状态，再验证顺序 B。
with Session() as db:
    folio=db.get(Accession,fid4);folio.status='isolated'
    iso=db.scalar(select(Isolation).where(Isolation.accession_id==fid4));iso.active=True;iso.exited=None
    db.add(Observation(accession_id=fid4,humidity=50,holes=0,mold=0,note='重新入柜合格基线'));db.commit()

# 顺序 B：补测事务先拿锁 → 返潮测量落账；随后出柜锁内重读最新测量被拦。
r_obs=service_call(catalog.observe,obs_input,operator)
r_hand=service_call(handovers.handover,hand_input,supervisor)
check('顺序B 补测成功落账',r_obs[1]==200,str(r_obs))
check('顺序B 出柜被最新登记测量拦下',r_hand[1]==409,str(r_hand))
with Session() as db:
    check('顺序B 卷册未被错误释放',db.get(Accession,fid4).status!='released')
    check('顺序B 最新登记即返潮值',catalog.latest_observation(db,fid4).humidity==70)

# ---- 11. viewer 写入拒绝（API 层）----
with TestClient(app) as client:
    vw=login('viewer')
    r=client.post('/observations',headers=vw,json={'accession_id':fid,'humidity':50,'holes':0,'mold':0,'note':'只读员尝试'})
    check('只读员写入被拒',r.status_code==403)

print('\n%d passed, %d failed'%(len(PASS),len(FAIL)),flush=True)
if FAIL:sys.exit(1)
