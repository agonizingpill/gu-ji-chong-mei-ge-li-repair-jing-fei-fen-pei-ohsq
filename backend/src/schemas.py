from pydantic import BaseModel,Field,model_validator
class Login(BaseModel):
    username:str
    password:str
class FolioInput(BaseModel):
    code:str=Field(min_length=3,max_length=40)
    title:str=Field(min_length=2,max_length=100)
    era:str=Field(min_length=1,max_length=40)
    paper:str=Field(min_length=1,max_length=40)
class ObservationInput(BaseModel):
    accession_id:int
    humidity:float=Field(ge=0,le=100)
    holes:int=Field(ge=0,le=10000)
    mold:float=Field(ge=0,le=100)
    humidity_max:float|None=Field(default=None,ge=0,le=100)
    mold_max:float|None=Field(default=None,ge=0,le=100)
    note:str=Field(default='',max_length=300)
    @model_validator(mode='after')
    def check_ranges(self):
        if self.humidity_max is not None and self.humidity_max<self.humidity:
            raise ValueError('含水范围上界不能小于实测下界')
        if self.mold_max is not None and self.mold_max<self.mold:
            raise ValueError('霉面积范围上界不能小于实测下界')
        return self
class CabinetInput(BaseModel):
    code:str=Field(min_length=1,max_length=30)
    capacity:int=Field(ge=1,le=100)
class AdmitInput(BaseModel):
    accession_id:int
    cabinet_id:int
class RiskInput(BaseModel):
    accession_id:int
class ReviewInput(BaseModel):
    level:str=Field(pattern='^(low|medium|high)$')
    note:str=Field(min_length=2,max_length=200)
class TreatmentInput(BaseModel):
    accession_id:int
    name:str=Field(min_length=2,max_length=100)
    estimated_cents:int=Field(gt=0,le=100000000)
    steps:list[str]=Field(min_length=2,max_length=8)
class RevisionInput(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    estimated_cents:int=Field(gt=0,le=100000000)
class EvidenceInput(BaseModel):
    evidence:str=Field(min_length=3,max_length=200)
class GrantInput(BaseModel):
    code:str=Field(min_length=2,max_length=40)
    name:str=Field(min_length=2,max_length=100)
    budget_cents:int=Field(gt=0,le=1000000000)
class FundInput(BaseModel):
    grant_id:int
class SettleInput(BaseModel):
    actual_cents:int=Field(gt=0)
class HandoverInput(BaseModel):
    accession_id:int
    kind:str=Field(pattern='^(storage|exhibition)$')
    recipient:str=Field(min_length=2,max_length=100)
    note:str=Field(min_length=2,max_length=200)
