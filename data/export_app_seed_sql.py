"""KPI 사전(내장 지표 5개)과 합성 데이터의 월별 원장을 Supabase `app` 스키마용 SQL 파일로 내보낸다.

- 지표 정의는 experiment/kpi_dictionary_A_v2.json 에서, 원장 값은 data/poc_desk_sample.db 의 ledger 에서 그대로 가져온다.
- 몇 번 실행해도 같은 결과가 나오게 upsert(있으면 갱신)로 쓴다. 이미 쌓인 실행 기록(정의 번호를 가리킴)은 건드리지 않는다.
- 원장 지표는 net_revenue 하나뿐이다(다른 지표는 별도 정답 출처가 없다. docs/09).

실행: python data/export_app_seed_sql.py <출력 .sql 경로>
적용: npx supabase db query --linked -f <출력 .sql 경로>
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICT = ROOT / "experiment" / "kpi_dictionary_A_v2.json"
DB = ROOT / "data" / "poc_desk_sample.db"


def lit(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def main(out_path):
    d = json.loads(DICT.read_text(encoding="utf-8"))
    version = int(str(d["version"]).lower().lstrip("v"))
    lines = ["begin;"]
    for key, m in d["metrics"].items():
        aliases = "ARRAY[" + ", ".join(lit(a) for a in m["aliases"]) + "]::text[]" if m["aliases"] else "'{}'::text[]"
        lines.append(
            "insert into app.kpi_definitions (dataset, metric_key, version, name, aliases, formula, include_exclude, "
            "period_basis, unit, source, has_reference) values "
            f"('demo', {lit(key)}, {version}, {lit(m['name'])}, {aliases}, {lit(m['formula'])}, "
            f"{lit(m['include_exclude'])}, {lit(m['period_basis'])}, {lit(m['unit'])}, 'builtin', true) "
            "on conflict (dataset, metric_key, version) do update set name = excluded.name, aliases = excluded.aliases, "
            "formula = excluded.formula, include_exclude = excluded.include_exclude, "
            "period_basis = excluded.period_basis, unit = excluded.unit;")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ledger = con.execute("select period_month, net_revenue_ex_vat from ledger order by 1").fetchall()
    for month, value in ledger:
        lines.append(
            "insert into app.ledger (definition_id, period_month, value) "
            f"select definition_id, {lit(month)}, {int(value)} from app.kpi_definitions "
            f"where dataset = 'demo' and metric_key = 'net_revenue' and version = {version} "
            "on conflict (definition_id, period_month) do update set value = excluded.value;")
    lines.append("commit;")
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"지표 정의 {len(d['metrics'])}개(버전 {version}), 원장 {len(ledger)}개월 -> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
