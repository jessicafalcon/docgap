"""Probe: can descriptions drafted from an evidence packet alone move Haiku 4.5 on the pilot?

Not a pilot pass. Stage 1 drafts every mart column with Opus 5.5 from a stand-in for
Phase 5's evidence packet (no dictionary). Stage 2 runs Haiku 4.5 on the 12 pilot
questions x 3 repetitions with pass 2's no-docs manifest plus those drafts, graded on
the gold and accepted results. Every draft goes in (no gate): an upper bound.
Its outputs are this directory's `packets.json`, `drafts.json` and one folder per run;
the response cache and the event log go to the gitignored `data/probe/` (ADR 0036).

    PYTHONPATH=. uv run --env-file .env python fixtures/probe/probe.py draft
    PYTHONPATH=. uv run --env-file .env python fixtures/probe/probe.py run [--limit N]
"""

# Every identifier in its SQL comes from the committed marts YAML, and K is a
# constant: nothing outside the repo reaches a query string.
# ruff: noqa: S608

from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path

import yaml

from docgap.config import load_config
from docgap.llm import Budget, LlmClient, ResponseCache, anthropic_transport
from docgap.log import EventLog
from docgap.manifest import read_marts
from docgap.models import ModelSettings
from eval.agent.loop import Question, run_agent
from eval.agent.tools import AgentTools
from eval.agent.warehouse import Warehouse, connect_marts
from eval.pilot import grade_run
from eval.questions import PILOT_GOLD, PILOT_QUESTIONS, load_questions, read_accepted, read_gold

ROOT = Path.cwd()
AGENT_ID = "claude-haiku-4-5-20251001"
OUT = Path(__file__).parent
RUNS = OUT / f"{AGENT_ID}.drafts"
WORK = ROOT / "data" / "probe"
DRAFTER = "claude-opus-5-5"
K = 11
BASE_MANIFEST = ROOT / "fixtures/pilot/pass-2/dbt/no_docs.json"
AGENT_DB = ROOT / "data/agent/sample/ANALYTICS.duckdb"
MARTS_YAML = ROOT / "warehouse/dbt/models/marts/_marts__models.yml"
DBT_MANIFEST = ROOT / "warehouse/dbt/target/manifest.json"
REPETITIONS = 3

SYSTEM = """\
You write the description of one column of a data warehouse for the people and AI
agents who query it, from the evidence given and nothing else: the column's name and
type, the SQL of its model and the models upstream, the model's description, and an
aggregate profile of its values. Write in French, at most 200 characters. Make no
claim the evidence doesn't support; list what you couldn't infer as unknowns instead.
Reply with one JSON object only: {"description": "...", "unknowns": ["...", ...]}."""


def columns() -> list[tuple[str, str, str, str]]:
    """(model, column, data type, sensitivity) for every mart column."""
    out = []
    for model in yaml.safe_load(MARTS_YAML.read_text())["models"]:
        for column in model["columns"]:
            out.append(
                (
                    model["name"],
                    column["name"],
                    column["data_type"],
                    column["config"]["meta"]["sensitivity"],
                )
            )
    return out


def lineage(model: str) -> list[dict[str, str]]:
    """The compiled SQL of the model and every model upstream of it."""
    nodes = json.loads(DBT_MANIFEST.read_text())["nodes"]
    out, todo, seen = [], [f"model.docgap.{model}"], set()
    while todo:
        uid = todo.pop()
        if uid in seen or uid not in nodes:
            continue
        seen.add(uid)
        node = nodes[uid]
        if node["resource_type"] == "model":
            out.append({"model": node["name"], "compiled_sql": node["compiled_code"]})
        elif node["resource_type"] == "seed":
            out.append({"seed": node["name"]})
        todo.extend(node.get("depends_on", {}).get("nodes", []))
    return out


def profile(con, model: str, column: str, data_type: str, sensitivity: str) -> dict:
    table, col = model.upper(), f'"{column}"'
    n, nulls, distinct = con.execute(
        f"SELECT COUNT(*), COUNT(*) - COUNT({col}), COUNT(DISTINCT {col}) FROM {table}"
    ).fetchone()
    out = {"rows": n, "null_rate": round(nulls / n, 4) if n else None, "distinct": distinct}
    if sensitivity == "restricted":
        out["values"] = "withheld: restricted column"
        return out
    # A dimension's values are counted by the fact rows that carry them.
    if model.startswith("dim_"):
        keys = {
            "dim_benefit_type": "PRS_NAT",
            "dim_provider_activity": "PSE_ACT_SNDS",
        }
        key = keys[model]
        counts = (
            f"SELECT d.{col} AS v, COUNT(*) AS n FROM {table} d "
            f"JOIN FCT_REIMBURSEMENTS f ON f.{key} = d.{key} GROUP BY 1"
        )
    else:
        counts = f"SELECT {col} AS v, COUNT(*) AS n FROM {table} WHERE {col} IS NOT NULL GROUP BY 1"
    top = con.execute(
        f"SELECT v, n FROM ({counts}) WHERE n >= {K} ORDER BY n DESC, v LIMIT 10"
    ).fetchall()
    out[f"top_values_carried_by_at_least_{K}_rows"] = [[str(v), c] for v, c in top]
    if re.match(r"(integer|decimal|bigint)", data_type):
        ordered = f"SELECT {col} AS v FROM {table} WHERE {col} IS NOT NULL ORDER BY v"
        low = con.execute(f"{ordered} LIMIT 1 OFFSET {K - 1}").fetchone()
        high = con.execute(f"{ordered} DESC LIMIT 1 OFFSET {K - 1}").fetchone()
        out[f"min_max_clipped_to_{K}th"] = [
            str(low[0]) if low else None,
            str(high[0]) if high else None,
        ]
    return out


def client(log: EventLog, spend: float) -> LlmClient:
    config = load_config(ROOT / "docgap.toml")
    llm_config = config.llm.model_copy(update={"max_spend_usd": spend})
    return LlmClient(
        anthropic_transport(config.llm), ResponseCache(WORK / "cache"), Budget(llm_config), log
    )


def draft() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    nodes = json.loads(DBT_MANIFEST.read_text())["nodes"]
    con = connect_marts(AGENT_DB, database="ANALYTICS", schema="MARTS")
    drafts, packets = {}, {}
    with (WORK / "events.jsonl").open("a") as sink:
        llm = client(EventLog(sink, "probe-drafts"), spend=4.0)
        for model, column, data_type, sensitivity in columns():
            fqn = f"ANALYTICS.MARTS.{model.upper()}.{column}"
            packet = {
                "column": fqn,
                "data_type": data_type,
                "model_description": nodes[f"model.docgap.{model}"]["description"],
                "lineage": lineage(model),
                "profile": profile(con, model, column, data_type, sensitivity),
            }
            packets[fqn] = packet
            message = llm.complete(
                ModelSettings(model=DRAFTER, sampling={}),
                {
                    "max_tokens": 16000,
                    "system": SYSTEM,
                    "messages": [
                        {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}
                    ],
                },
                prompt_version="probe-drafter-1",
                draw=None,
                stage="probe_draft",
                item=fqn,
            )
            text = "".join(b.text for b in message.content if b.type == "text")
            parsed = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            drafts[fqn] = parsed
            print(fqn, "|", parsed["description"])
        print(f"drafter: {llm.budget.calls} calls, ${llm.budget.spend_usd:.2f}")
    (OUT / "packets.json").write_text(json.dumps(packets, ensure_ascii=False, indent=1) + "\n")
    (OUT / "drafts.json").write_text(json.dumps(drafts, ensure_ascii=False, indent=1) + "\n")


def run(limit: int | None) -> None:
    config = load_config(ROOT / "docgap.toml")
    drafts = json.loads((OUT / "drafts.json").read_text())
    marts, _ = read_marts(BASE_MANIFEST, config.manifest)
    if marts.column_descriptions:
        raise ValueError(f"{BASE_MANIFEST} documents a column; the probe adds drafts to none")
    marts = dataclasses.replace(
        marts, column_descriptions={fqn: d["description"] for fqn, d in drafts.items()}
    )
    if missing := set(marts.fqns()) - set(marts.column_descriptions):
        raise ValueError(f"no draft for {sorted(missing)}")
    questions = load_questions(PILOT_QUESTIONS)
    warehouse = Warehouse(
        AGENT_DB,
        database="ANALYTICS",
        schema="MARTS",
        timeout_seconds=config.agent.statement_timeout_seconds,
    )
    tools = AgentTools(warehouse, marts, row_cap=config.agent.row_cap)
    runs = [(q, r) for q in questions for r in range(1, REPETITIONS + 1)]
    passed = made = 0
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        with (WORK / "events.jsonl").open("a") as sink:
            log = EventLog(sink, "probe-drafts")
            llm = client(log, spend=4.0)
            for question, rep in runs[:limit]:
                directory = RUNS / f"{question.id}.r{rep}"
                if (directory / "transcript.json").exists():
                    graded = json.loads((directory / "grade.json").read_text())
                else:
                    agent = run_agent(
                        Question(question.id, question.text),
                        rep,
                        run_id="probe-drafts",
                        site=ModelSettings(model=AGENT_ID, sampling={}),
                        llm=llm,
                        tools=tools,
                        config=config.agent,
                        log=log,
                    )
                    directory.mkdir(parents=True, exist_ok=True)
                    result = grade_run(
                        agent.transcript,
                        agent.outcome,
                        read_gold(PILOT_GOLD / f"{question.id}.parquet"),
                        ordered=question.ordered,
                        rows=directory / "rows.parquet",
                        accepted=read_accepted(question, PILOT_GOLD),
                    )
                    (directory / "grade.json").write_text(result.model_dump_json(indent=1) + "\n")
                    (directory / "transcript.json").write_text(
                        agent.transcript.model_dump_json() + "\n"
                    )
                    graded = json.loads(result.model_dump_json())
                made += 1
                ok = bool(graded["grade"] and graded["grade"]["passed"])
                passed += ok
                print(
                    question.id,
                    rep,
                    "pass" if ok else graded["grade"] and graded["grade"]["reason"],
                )
            print(
                f"agent: {passed}/{made} passed; {llm.budget.calls} calls, ${llm.budget.spend_usd:.2f}"
            )
    finally:
        warehouse.close()


if __name__ == "__main__":
    if sys.argv[1] == "draft":
        draft()
    else:
        run(int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--limit" else None)
