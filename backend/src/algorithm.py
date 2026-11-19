import hashlib
import numpy as np
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score,confusion_matrix
LEVELS=['low','medium','high']
_MODEL_CACHE=None
def build_model():
    # 固定 seed：模型与评估指标在进程内只训练一次，多次分级完全可复现。
    global _MODEL_CACHE
    if _MODEL_CACHE is None:
        rng=np.random.default_rng(31)
        x=np.column_stack([rng.uniform(30,90,600),rng.integers(0,31,600),rng.uniform(0,25,600)])
        # 专家规则构造的合成训练样本，不能替代真实馆藏外部验证。
        y=np.where((x[:,0]>=72)|(x[:,2]>=10),'high',np.where((x[:,0]>=60)|(x[:,1]>=10)|(x[:,2]>=2),'medium','low'))
        train,test=train_test_split(np.arange(len(x)),test_size=.25,random_state=31,stratify=y)
        model=DecisionTreeClassifier(max_depth=5,min_samples_leaf=4,random_state=31).fit(x[train],y[train])
        prediction=model.predict(x[test])
        metrics={'macro_f1':float(f1_score(y[test],prediction,average='macro')),'confusion_matrix':confusion_matrix(y[test],prediction,labels=LEVELS).tolist(),'labels':LEVELS,'train_count':len(train),'test_count':len(test),'seed':31,'dataset_sha256':hashlib.sha256(x.tobytes()+y.tobytes()).hexdigest(),'data_source':'专家规则合成样本；留出指标仅证明内部可复现性'}
        _MODEL_CACHE=(model,metrics)
    return _MODEL_CACHE
def classify(humidity,holes,mold):
    model,metrics=build_model()
    return str(model.predict([[humidity,holes,mold]])[0]),metrics
def candidate_levels(model,humidity_lo,humidity_hi,holes,mold_lo,mold_hi,grid_points=41):
    # 决策树按阈值切分特征轴；在两个区间轴上取稠密网格，求树在矩形内的全部预测。
    # 不只用区间端点：树的内部阈值可能落在区间中部（如霉面积 0.78、1.67）。
    hs=np.linspace(humidity_lo,humidity_hi,grid_points)
    ms=np.linspace(mold_lo,mold_hi,grid_points)
    samples=np.array([[h,holes,m] for h in hs for m in ms])
    found=[level for level in LEVELS if level in set(model.predict(samples))]
    return found
