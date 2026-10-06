"""Safe many-to-one joins, grouped aggregates and rates; independent Python and SQL."""
import math
import datetime as dt
from psycopg import sql

OPS = {'eq':'=', 'gt':'>', 'gte':'>=', 'lt':'<', 'lte':'<='}

def resolve(reference, base):
    parts = reference.split('.')
    return (base, parts[0]) if len(parts) == 1 else tuple(parts)


def foreign_key_target(table_name, column_name, tables, seen=None):
    """Preserve a declared FK only through exact derived grouping keys."""
    seen = set() if seen is None else seen
    identity = (table_name, column_name)
    if identity in seen or table_name not in tables:
        return None
    seen.add(identity)
    table = tables[table_name]
    column = next((c for c in table.columns if c.name == column_name), None)
    if column is None:
        return None
    gen = column.generator
    if gen.kind == 'foreign_key':
        return gen.table
    source = getattr(table, 'derived_from', None)
    if gen.kind == 'group_key' and source:
        return foreign_key_target(source, gen.source_column, tables, seen)
    return None

def validate_metric(metric, tables):
    if metric.table not in tables:
        raise ValueError('unknown metric table')
    accessible = {metric.table}
    for join in metric.joins:
        source, name = resolve(join.source_column, metric.table)
        if source not in accessible or join.table not in tables or join.table in accessible:
            raise ValueError('metric join must reference a new table from an accessible source')
        column = next((c for c in tables[source].columns if c.name == name), None)
        if foreign_key_target(source, name, tables) != join.table:
            kind = column.generator.kind if column else 'missing'
            raise ValueError(f'{metric.name}: metric joins must follow declared foreign keys to primary ids (no fanout); '
                f'{source}.{name} uses {kind}, so it cannot join {join.table}. '
                'A derived group_key must inherit an actual source foreign_key; ordinary integers and aggregates cannot join.')
        accessible.add(join.table)
    def column(ref):
        table, name = resolve(ref, metric.table)
        if table not in accessible or not any(c.name == name for c in tables[table].columns):
            raise ValueError('unknown or unjoined analytical metric column')
        return next(c.generator for c in tables[table].columns if c.name == name)
    if metric.operation not in ('count', 'ratio'):
        gen = column(metric.column or '')
        if metric.operation in ('sum','avg','min','max') and (gen.value_type if gen.kind in ('aggregate','group_key') else gen.kind) not in ('integer','number','id','foreign_key'):
            raise ValueError('numeric metric column required')
    for ref in metric.group_by:
        column(ref)
    if len(set(metric.group_by)) != len(metric.group_by):
        raise ValueError('duplicate comparison group key')
    for condition in metric.conditions + metric.denominator_conditions:
        if isinstance(condition.value,float) and not math.isfinite(condition.value): raise ValueError('finite metric condition required')
        gen = column(condition.column)
        kind = gen.value_type if gen.kind in ('aggregate','group_key') else gen.kind
        if (kind in ('integer','number','id','foreign_key')) != (type(condition.value) in (int,float)):
            raise ValueError('analytical metric condition type differs')
    if metric.operation == 'ratio' and not metric.conditions:
        raise ValueError('ratio requires explicit numerator conditions')
    if metric.operation != 'ratio' and metric.denominator_conditions:
        raise ValueError('denominator conditions only supported for ratios')

def reference(metric, tables, rows):
    indexes = {name:{r[tables[name].columns[0].name]:r for r in data} for name,data in rows.items()}
    joined = [{(metric.table,k):v for k,v in row.items()} for row in rows[metric.table]]
    for join in metric.joins:
        source = resolve(join.source_column, metric.table)
        joined = [{**row, **{(join.table,k):v for k,v in indexes[join.table][row[source]].items()}} for row in joined if row[source] in indexes[join.table]]
    def matches(row, conditions):
        for c in conditions:
            value = row[resolve(c.column, metric.table)]
            if value is None: return False
            if not {'eq':lambda:value==c.value,'gt':lambda:value>c.value,'gte':lambda:value>=c.value,'lt':lambda:value<c.value,'lte':lambda:value<=c.value}[c.operator](): return False
        return True
    # WHERE filters observations, whereas ratio conditions filter the numerator only.
    base_conditions = metric.denominator_conditions if metric.operation == 'ratio' else metric.conditions
    joined = [r for r in joined if matches(r,base_conditions)]
    groups = {}
    for row in joined:
        key = tuple(row[resolve(c,metric.table)] for c in metric.group_by)
        groups.setdefault(key, []).append(row)
    if not metric.group_by: groups.setdefault((), [])
    result = []
    for key, group in groups.items():
        values = [r[resolve(metric.column,metric.table)] for r in group if r[resolve(metric.column,metric.table)] is not None] if metric.column else []
        op = metric.operation
        if op == 'count': value = len(group)
        elif op == 'distinct': value = len(set(values))
        elif op == 'ratio': value = sum(matches(r,metric.conditions) for r in group)/len(group) if group else None
        elif not values: value = None
        elif op == 'avg': value = sum(values)/len(values)
        elif op == 'sum': value = sum(values)
        elif op == 'min': value = min(values)
        else: value = max(values)
        if metric.minimum is not None and (value is None or value < metric.minimum) or metric.maximum is not None and (value is None or value > metric.maximum):
            raise ValueError('analytical metric lacks required group sample or value range')
        normalized_key=[]
        for ref,item in zip(metric.group_by,key):
            table,name=resolve(ref,metric.table)
            gen=next(c.generator for c in tables[table].columns if c.name==name)
            kind=gen.value_type if gen.kind in ('group_key','aggregate') else gen.kind
            if item is not None and kind in ('timestamp','timestamp_bucket','timestamp_sequence','timestamp_offset'):
                item=dt.datetime.fromisoformat(item.replace('Z','+00:00')).astimezone(dt.timezone.utc).isoformat().replace('+00:00','Z')
            normalized_key.append(item)
        result.append([*normalized_key,value])
    if metric.group_by and len(result) < 2:
        raise ValueError('comparison needs at least two observable groups/periods')
    columns = [ref.replace('.','__') for ref in metric.group_by] + [metric.name]
    return {'columns':columns,'rows':result}, compile_metric(metric,tables)


def compile_metric(metric, tables):
    """Compile the same validated metric without requiring private fixture rows."""
    validate_metric(metric,tables)
    base_conditions = metric.denominator_conditions if metric.operation == 'ratio' else metric.conditions
    def identifier(ref): return sql.Identifier(*resolve(ref,metric.table))
    def where(conditions):
        return sql.SQL(' AND ').join(sql.SQL('{} {} {}').format(identifier(c.column),sql.SQL(OPS[c.operator]),sql.Literal(c.value)) for c in conditions)
    expression = sql.SQL('COUNT(*)') if metric.operation=='count' else sql.SQL('COUNT(DISTINCT {})').format(identifier(metric.column)) if metric.operation=='distinct' else sql.SQL('COUNT(*) FILTER (WHERE {})::double precision / NULLIF(COUNT(*),0)').format(where(metric.conditions)) if metric.operation=='ratio' else sql.SQL('{}({})').format(sql.SQL(metric.operation.upper()),identifier(metric.column))
    from_clause = sql.Identifier(metric.table)
    for join in metric.joins:
        from_clause += sql.SQL(' JOIN {} ON {} = {}').format(sql.Identifier(join.table),identifier(join.source_column),sql.Identifier(join.table,tables[join.table].columns[0].name))
    columns = [ref.replace('.','__') for ref in metric.group_by] + [metric.name]
    selections = [sql.SQL('{} AS {}').format(identifier(ref),sql.Identifier(alias)) for ref,alias in zip(metric.group_by,columns)]
    selections.append(sql.SQL('{} AS {}').format(expression,sql.Identifier(metric.name)))
    query = sql.SQL('SELECT {} FROM {}').format(sql.SQL(', ').join(selections),from_clause)
    if base_conditions: query += sql.SQL(' WHERE {}').format(where(base_conditions))
    if metric.group_by: query += sql.SQL(' GROUP BY {}').format(sql.SQL(', ').join(identifier(ref) for ref in metric.group_by))
    return query.as_string()
