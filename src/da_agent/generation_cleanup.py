"""Remove only aged, unreferenced failed generated artifacts with checked identity."""
import datetime as dt
import re
import shutil
from pathlib import Path
import psycopg
from psycopg import sql
from .package_validation import generator_dsn

def prune(store,catalog,at=None):
    at=at or dt.datetime.now(dt.timezone.utc)
    removed=[]
    with store.connect() as conn:
        jobs=conn.execute("SELECT j.private,r.payload FROM generation_jobs j JOIN training_requests r USING(request_id) WHERE r.payload->>'status' IN ('failed','cancelled','interrupted')").fetchall()
        references={r['payload']['package_id'] for r in conn.execute('SELECT payload FROM attempts')}
        references.update(r['payload']['package_id'] for r in conn.execute('SELECT payload FROM generation_samples'))
    for row in jobs:
        identity=row['private'].get('package_id')
        if not identity or identity in references or not re.fullmatch(r'generated-[0-9a-f]{24}',identity): continue
        stamp=dt.datetime.fromisoformat(row['payload']['created_at'].replace('Z','+00:00'))
        if at-stamp<dt.timedelta(hours=24): continue
        root=Path(catalog.root).resolve()
        target=(root/identity/'v1').resolve()
        if not target.is_relative_to(root) or target!=root/identity/'v1' or not target.is_dir(): continue
        package=catalog.load(identity,'v1',allow_unvalidated=True)
        if package.path.resolve()!=target or not re.fullmatch(r'pkg_[0-9a-f]{20}',package.schema_name): continue
        # Recheck durable references immediately before removing this exact artifact.
        with store.connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(identity,))
            referenced=conn.execute("SELECT 1 FROM attempts WHERE payload->>'package_id'=%s",(identity,)).fetchone()
            sampled=conn.execute("SELECT 1 FROM generation_samples WHERE payload->>'package_id'=%s",(identity,)).fetchone()
            active=conn.execute("SELECT 1 FROM training_requests r JOIN generation_jobs j USING(request_id) WHERE j.private->>'package_id'=%s AND r.payload->>'status' NOT IN ('failed','cancelled','interrupted')",(identity,)).fetchone()
            if referenced or sampled or active: continue
            with psycopg.connect(generator_dsn()) as data_conn:
                data_conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(package.schema_name)))
            shutil.rmtree(target)
            removed.append(identity)
    return {'removed_package_ids':removed,'minimum_age_hours':24}
