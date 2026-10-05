"""Read-only package access diagnostics, omitting credentials and query text."""
import json
import psycopg
from psycopg import sql
from da_agent.config import Settings
from da_agent.packages import PackageCatalog
from da_agent.sql_runner import SqlRunner

s=Settings()
catalog=PackageCatalog(s.packages_root)
records=[]
for p in catalog.list_public():
    if p['package_id']!='training-001': continue
    package=catalog.load(p['package_id'],p['release_version'])
    value={'package_id':p['package_id'],'version':p['release_version'],'schema':package.schema_name}
    try:
        with psycopg.connect(s.learner_dsn) as c:
            value['schema_exists']=c.execute('SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)',(package.schema_name,)).fetchone()[0]
            value['usage']=c.execute('SELECT has_schema_privilege(current_user,%s,\'USAGE\')',(package.schema_name,)).fetchone()[0]
            c.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(package.schema_name)))
            value['users']=c.execute('SELECT count(*) FROM users').fetchone()[0]
    except psycopg.Error as e:
        value['exception']=type(e).__name__;value['sqlstate']=e.sqlstate
    r=SqlRunner(s).execute('verification-diagnostic',package.schema_name,package.reference('problem-001')['sql'])
    value['reference_status']=r['status'];value['reference_error']=r['error']
    records.append(value)
print(json.dumps(records,ensure_ascii=False))
