"""Public business context and evidence-backed learning requirements, not user prerequisites."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
import re

VERSION = 'task-quality-v1'

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Evidence(Model):
    table: str
    columns: list[str] = Field(min_length=1, max_length=8)

class JudgmentContract(Model):
    method: Literal['conditional_comparison', 'non_identifiable']
    control_columns: list[str] = Field(min_length=1, max_length=3)
    decision_rule: str = Field(min_length=20, max_length=400)
    accepted_limit: str = Field(min_length=20, max_length=400)

class Requirement(Model):
    competency: Literal['measurement', 'comparison', 'alternatives', 'confounding', 'uncertainty', 'decision']
    question: str = Field(min_length=8, max_length=400)
    evidence: list[Evidence] = Field(min_length=1, max_length=4)
    metric_names: list[str] = Field(default_factory=list,max_length=6)
    completion: str = Field(min_length=8, max_length=400)
    judgment: JudgmentContract | None = None

class BusinessCase(Model):
    provenance: Literal['synthetic'] = 'synthetic'
    background: str = Field(min_length=20, max_length=600)
    observed_problem: str = Field(min_length=20, max_length=600)
    observation_period: str = Field(min_length=8, max_length=200)
    decision: str = Field(min_length=15, max_length=400)
    agent_assumptions: list[str] = Field(min_length=1, max_length=6)
    requirements: list[Requirement] = Field(min_length=2, max_length=6)

def check_quality(recipe, recent=(), intentional_repeat=False):
    """Checks structure and observable support; semantic review remains necessary."""
    case = recipe.business_case
    if case is None:
        raise ValueError('task quality: agent must expand the short request into business_case; do not ask the learner to author it')
    tables = {t.name: {c.name for c in t.columns} for t in recipe.tables}
    for requirement in case.requirements:
        for evidence in requirement.evidence:
            if evidence.table not in tables or not set(evidence.columns).issubset(tables[evidence.table]):
                raise ValueError('task quality: question references missing public evidence')
        if recipe.task_kind != 'design' and requirement.competency in ('measurement','comparison'):
            metrics={m.name:m for m in recipe.metrics}
            if not requirement.metric_names or any(name not in metrics for name in requirement.metric_names) or not any(metrics[name].purpose=='analysis' for name in requirement.metric_names):
                raise ValueError('task quality: quantitative '+requirement.competency+' metric_names='+str(requirement.metric_names)+' must reference existing metrics and at least one analysis metric; available='+str([m.name for m in recipe.metrics]))
    competencies = {r.competency for r in case.requirements}
    if len(competencies)!=len(case.requirements): raise ValueError('task quality: each competency must occur once')
    required = {'measurement', 'decision'} if recipe.difficulty == 'beginner' else {'comparison', 'uncertainty', 'decision'}
    if recipe.difficulty == 'advanced':
        required |= {'alternatives', 'confounding'}
    if not required.issubset(competencies):
        raise ValueError('task quality: difficulty lacks required judgments: '+','.join(sorted(required-competencies)))
    if recipe.task_kind != 'design':
        analytical = [m for m in recipe.metrics if m.purpose == 'analysis']
        if not analytical:
            raise ValueError('task quality: separate analysis metrics from dataset sanity counts')
        if recipe.difficulty != 'beginner' and not any(m.group_by for m in analytical):
            raise ValueError('task quality: intermediate/advanced needs an executable group or period comparison')
        if recipe.difficulty != 'beginner' and not any(m.joins for m in analytical):
            raise ValueError('task quality: intermediate/advanced needs a meaningful FK-linked analysis, not unused extra tables')
        claims=' '.join([recipe.title,recipe.description,recipe.goal,case.observed_problem,case.decision,*[r.question for r in case.requirements]])
        explicit_retention = re.search(r'잔류|리텐션|재방문|재접속|\bD\d+\b|churn|retention',claims,re.I)
        tutorial_funnel = ('튜토리얼' in claims and '완료' in claims and any(
            m.operation=='ratio' and m.conditions and re.search(r'completion|completed|tutorial|완료',m.name,re.I)
            for m in analytical))
        if explicit_retention or (re.search(r'이탈',claims) and not tutorial_funnel):
            retention_metrics=[m for m in analytical if m.operation=='ratio' and any(re.search(r'churn|retention|returned|inactive',c.column,re.I) for c in m.conditions)]
            if not retention_metrics:
                raise ValueError('task quality: retention/churn claims require a defined observable outcome and executable ratio metric; choose a supported current-content performance question instead')
        if recipe.difficulty == 'advanced' and not any(m.operation != 'count' for m in analytical):
            raise ValueError('task quality: advanced cannot be verified by counts alone')
        if recipe.difficulty == 'advanced':
            confounding = next(r for r in case.requirements if r.competency=='confounding')
            judgment = confounding.judgment
            if judgment is None:
                raise ValueError('task quality: advanced confounding needs an explicit judgment contract (method, control_columns, decision_rule, accepted_limit); competency names alone are insufficient')
            evidence_columns = {e.table+'.'+c for e in confounding.evidence for c in e.columns}
            if not set(judgment.control_columns).issubset(evidence_columns):
                raise ValueError('task quality: confounding controls must reference its public evidence columns')
            metrics = {m.name:m for m in analytical}
            if not confounding.metric_names or any(n not in metrics for n in confounding.metric_names):
                raise ValueError('task quality: advanced confounding must connect to executable analysis metrics')
            linked = [metrics[n] for n in confounding.metric_names]
            if judgment.method == 'conditional_comparison':
                def grouped(m):
                    return {g if '.' in g else m.table+'.'+g for g in m.group_by}
                if not any(len(grouped(m)) >= 2 and set(judgment.control_columns).issubset(grouped(m)) for m in linked):
                    raise ValueError('task quality: conditional confounding comparison needs an executable comparison within control strata (at least two group dimensions)')
            elif len(evidence_columns) < 2:
                raise ValueError('task quality: non-identifiable judgment must explain limits using at least two observed public columns')
    for previous in recent:
        if not intentional_repeat and previous.get('quality_version') == VERSION and previous.get('difficulty') == recipe.difficulty and previous.get('goal') == recipe.goal and previous.get('structure') == structure(recipe):
            raise ValueError('task quality: repeated analysis structure and goal; choose a different business decision or comparison')
    return {'version': VERSION, 'structural_checks': 'pass', 'human_quality': 'pending'}

def structure(recipe):
    return '|'.join(sorted(f'{m.operation}:{bool(m.joins)}:{len(m.group_by)}' for m in recipe.metrics if m.purpose == 'analysis'))

def public_description(recipe):
    case = recipe.business_case
    if not case:
        return recipe.description
    return '\n\n'.join([recipe.description, '업무 배경: '+case.background,
        '관측된 문제: '+case.observed_problem, '관측 기간: '+case.observation_period,
        '판단할 업무 결정: '+case.decision,
        '분석 과제:\n'+'\n'.join(f'- {r.question}'+(
            '\n  판단 방법: '+r.judgment.method+'; 관측/통제 열: '+', '.join(r.judgment.control_columns)+
            '\n  완료 판단: '+r.judgment.decision_rule+'\n  인정할 한계: '+r.judgment.accepted_limit
            if r.judgment else '') for r in case.requirements),
        '에이전트가 설정한 연습 조건:\n'+'\n'.join('- '+a for a in case.agent_assumptions),
        '실제 회사 사례를 인용한 것이 아닌 가상 업무 상황이며, 데이터는 연습용 합성 데이터입니다.'])
