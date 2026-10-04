"""Staging validation and learner grants are separate from ready publication."""
import os
from pathlib import Path
import psycopg
from psycopg import sql
from .data import generate_package, validate_rows, validate_package
from .packages import PackageCatalog, read_json, write_json
from .errors import DomainError

def generator_dsn():
    configured=os.getenv('GENERATOR_DSN')
    if configured:
        return configured
    key=Path(os.getenv('GENERATOR_PASSWORD_FILE','/run/secrets/generator_password'))
    if not key.is_file():
        raise DomainError('generation_unavailable','전용 데이터 생성 계정 설정이 필요합니다.',503)
    from psycopg.conninfo import make_conninfo
    return make_conninfo(host=os.getenv('GENERATOR_DB_HOST','db'),dbname='training',user='generator',password=key.read_text().strip())

def stage(root, package_id, seed, user_count, scenario):
    path=Path(root)/package_id/'v1'
    if path.exists():
        package=PackageCatalog(root).load(package_id,'v1',allow_unvalidated=True)
        if package.private.get('seed')!=seed or package.private.get('user_count')!=user_count or package.private.get('scenario','baseline')!=scenario:
            raise DomainError('generation_conflict','고정 생성 계획과 기존 자료가 다릅니다.',409)
    else:
        package=generate_package(root,package_id,'v1',seed,user_count,scenario)
    users,sessions=validate_rows(package)
    if len(users)>1000 or len(sessions)>10000:
        raise DomainError('generation_limit','데이터 생성 상한을 넘었습니다.',422)
    categories=package.public['data_dictionary']['categories']
    if any(row[k] not in categories[k] for row in users for k in ('platform','country','acquisition_channel')):
        raise DomainError('validation_failed','범주 검증에 실패했습니다.',422)
    with psycopg.connect(generator_dsn(),connect_timeout=5,options='-c statement_timeout=60000') as conn:
        package=validate_package(conn,root,package_id,'v1',grant_learner=False,publish=False)
    return package

def grant(package):
    with psycopg.connect(generator_dsn(),connect_timeout=5) as conn:
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO learner').format(sql.Identifier(package.schema_name)))
        conn.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO learner').format(sql.Identifier(package.schema_name)))

def validate_plan(package, public, private):
    """Validate operator approval material before granting any learner access."""
    from .evaluation import verify_evidence, verify_comparison
    with psycopg.connect(generator_dsn(),connect_timeout=5,options='-c statement_timeout=60000') as conn:
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(package.schema_name)))
        def proof(query):
            cursor=conn.execute(query)
            rows=cursor.fetchmany(1001)
            return {'saved_execution_id':'operator-validation','result':{'status':'success','result_complete':len(rows)<=1000,'columns':[{'name':c.name} for c in cursor.description],'rows':[list(r) for r in rows]}}
        if verify_evidence(proof(private['sql']),private['expected'])['status']!='verified':
            raise DomainError('validation_failed','표본의 과제 기준과 독립 집계가 다릅니다.',422)
        if private.get('comparison_sql') and verify_comparison(proof(private['comparison_sql']),private['comparison_expected'])['status']!='verified':
            raise DomainError('validation_failed','표본의 비교 과제를 실제 SQL로 확인할 수 없습니다.',422)
        if public.get('analysis_draft') and not proof(public['analysis_draft'])['result']['result_complete']:
            raise DomainError('validation_failed','표본 검토 SQL의 완전한 결과가 필요합니다.',422)
    return 'validated'

def revoke(package):
    with psycopg.connect(generator_dsn(),connect_timeout=5) as conn:
        conn.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM learner').format(sql.Identifier(package.schema_name)))
        conn.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM learner').format(sql.Identifier(package.schema_name)))
    receipt=read_json(package.path/'private/validation.json')
    receipt['status']='validated'
    write_json(package.path/'private/validation.json',receipt)

def publish(package):
    receipt=read_json(package.path/'private/validation.json')
    if receipt['status'] not in ('validated','publishable'):
        raise DomainError('validation_failed','검증 표식이 없습니다.',409)
    receipt['status']='publishable'
    write_json(package.path/'private/validation.json',receipt)
