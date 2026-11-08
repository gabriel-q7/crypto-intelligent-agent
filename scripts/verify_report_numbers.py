#!/usr/bin/env python3
"""Confere os números do relatório contra os arquivos de evidência versionados.

Para cada afirmação numérica dos capítulos, o script:

1. lê o valor bruto da evidência (resultado do benchmark, JSON da avaliação, log do pytest,
   JSONL do Text-to-Query), que é a fonte da verdade;
2. recalcula as grandezas derivadas (médias da avaliação sem os exemplos que falharam por
   infraestrutura, contagens de status, contagem de testes);
3. verifica se o valor, formatado como no capítulo, aparece no texto.

Também faz checagens de consistência (o resumo salvo pela avaliação final deve coincidir com
o recalculado a partir das linhas; o log da auditoria não pode conter avisos de depreciação).

Usa apenas a biblioteca padrão. Uso (a partir da raiz do repositório):
    python3 scripts/verify_report_numbers.py
"""

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT = REPO_ROOT / "Report"
FINAL = REPORT / "relatorio-final"

FASE1 = REPORT / "fase-01-chain-estruturada.md"
FASE4 = REPORT / "fase-04-ferramentas-rag.md"
FASE5 = REPORT / "fase-05-agente-integrado.md"
DECISOES = FINAL / "09-decisoes-transversais.md"
CONCLUSAO = FINAL / "10-conclusao.md"

BENCHMARKS = REPO_ROOT / "benchmarks" / "results"
EVIDENCE5 = REPO_ROOT / "docs" / "evidence" / "phase-5"
EVIDENCE4 = REPO_ROOT / "docs" / "evidence" / "phase-4"
EVIDENCE_FINAL = REPO_ROOT / "docs" / "evidence" / "final"

# Mesma separação do runner (app/evaluation/evaluators.py).
TRAJECTORY_KEYS = {"tool_selection", "required_tools", "no_unnecessary_tools", "tool_combination",
                   "tool_inputs", "error_handling", "evidence_used"}

failures: list[str] = []
checked = 0


def br(value: float, decimals: int) -> str:
    """Número no formato do texto em português (vírgula decimal)."""
    return f"{value:.{decimals}f}".replace(".", ",")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def expect(path: Path, needle: str, what: str) -> None:
    """O valor formatado precisa aparecer no capítulo."""
    global checked
    checked += 1
    if not path.exists():
        failures.append(f"capítulo ausente: {path.relative_to(REPO_ROOT)}")
        return
    if needle not in text(path):
        failures.append(f"{path.relative_to(REPO_ROOT)}: esperado '{needle}' ({what})")


def check(condition: bool, message: str) -> None:
    global checked
    checked += 1
    if not condition:
        failures.append(message)


def load_json(path: Path) -> dict:
    if not path.exists():
        failures.append(f"evidência ausente: {path.relative_to(REPO_ROOT)}")
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- Fase 1: benchmark


def verify_benchmark() -> None:
    labels = {
        "20261017T224551_qwen2.5-7b.json": "qwen2.5:7b",
        "20261017T232039_gemma4-e4b_thinking-off.json": "gemma4:e4b sem thinking",
        "20261017T233355_gemma4-e4b_thinking-on.json": "gemma4:e4b com thinking",
        "20261017T235946_mistral-7b.json": "mistral:7b",
        "20261018T002057_gemma4-e2b_thinking-off.json": "gemma4:e2b sem thinking",
    }
    means = {}
    for name, label in labels.items():
        summary = load_json(BENCHMARKS / name).get("summary", {})
        if not summary:
            continue
        mean = summary["wall_mean_s"]
        means[label] = mean
        # Diário da Fase 1 (ponto decimal, como escrito em 2026-10-17) e capítulo de decisões (vírgula).
        expect(FASE1, f"{mean:.1f}", f"latência média de {label}")
        expect(DECISOES, br(mean, 1), f"latência média de {label}")
        expect(FASE1, f"{summary['wall_p95_s']:.1f}", f"p95 de {label}")
        expect(DECISOES, f"{round(summary['valid_rate'] * 100)}%", f"JSON válido de {label}")
    if {"qwen2.5:7b", "gemma4:e2b sem thinking"} <= means.keys():
        reduction = 1 - means["gemma4:e2b sem thinking"] / means["qwen2.5:7b"]
        check(round(reduction * 100) == 40, f"redução de latência e2b × qwen deveria ser ~40%, é {reduction:.1%}")


# --------------------------------------------------------------------------- Fase 4: Text-to-Query


def verify_text_to_query() -> None:
    path = EVIDENCE4 / "text-to-query-final.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    counts = Counter(r["status"] for r in rows)
    expect(FASE4, f"Text-to-Query real ({len(rows)} perguntas)", "perguntas do Text-to-Query")
    for status, n in counts.items():
        expect(FASE4, f"{n} `{status}`", f"contagem do status {status}")


# --------------------------------------------------------------------------- Fase 5: avaliação


def summarize(rows: list[dict]) -> dict:
    """Reimplementação de app/evaluation/runner._summarize (sem importar a aplicação)."""
    by_key: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("error"):
            continue
        for key, ev in row["scores"].items():
            if ev["score"] is not None:
                by_key[key].append(float(ev["score"]))
    mean = lambda xs: round(statistics.fmean(xs), 3) if xs else None  # noqa: E731
    return {
        "overall": {k: mean(v) for k, v in by_key.items()},
        "trajectory_mean": mean([s for k, v in by_key.items() if k in TRAJECTORY_KEYS for s in v]),
        "answer_mean": mean([s for k, v in by_key.items() if k not in TRAJECTORY_KEYS for s in v]),
        "errored": [r["id"] for r in rows if r.get("error")],
    }


def verify_evaluation() -> None:
    baseline = load_json(EVIDENCE5 / "eval-local-baseline.json")
    if baseline:
        s = summarize(baseline["rows"])
        check(not s["errored"], "a avaliação de base não deveria ter exemplos com erro")
        expect(FASE5, br(s["trajectory_mean"], 3), "média de trajetória da avaliação de base")
        expect(FASE5, br(s["answer_mean"], 3), "média de resposta da avaliação de base")
        expect(FASE5, br(s["overall"]["tool_selection"], 3), "tool_selection da avaliação de base")

    after = load_json(EVIDENCE5 / "eval-local-after-fixes.json")
    if after:
        errored = [r for r in after["rows"] if r.get("error")]
        expect(FASE5, f"{len(errored)} dos {len(after['rows'])} exemplos", "exemplos com erro de infraestrutura")
        # O número citado é o do resumo salvo na época (com as notas 0), que é justamente o problema.
        expect(FASE5, f"para {br(after['summary']['overall']['tool_selection'], 1)}", "tool_selection contaminado")

    middle = load_json(EVIDENCE5 / "eval-local-final.json")  # execução intermediária (dataset v2)
    final = load_json(EVIDENCE5 / "eval-local-final-v3.json")  # execução final (dataset v3)
    if not (final and middle and baseline):
        return
    runs = {"final": final, "intermediária": middle, "base": baseline}
    summaries = {name: summarize(run["rows"]) for name, run in runs.items()}
    for name, run in runs.items():
        saved, s = run["summary"], summaries[name]
        for key in ("trajectory_mean", "answer_mean"):
            check(saved[key] == s[key] or "n_scored" not in saved and s["errored"],
                  f"{name}: {key} salvo {saved[key]} ≠ recalculado {s[key]}")
    s, m, b = summaries["final"], summaries["intermediária"], summaries["base"]
    for key in ("trajectory_mean", "answer_mean"):
        expect(FASE5, f"{br(s[key], 3)}** | {br(m[key], 3)} | {br(b[key], 3)}", f"{key}: final × intermediária × base")
        expect(CONCLUSAO, br(s[key], 3), f"{key} da avaliação final")
    n_rows, n_err = len(final["rows"]), len(s["errored"])
    expect(FASE5, f"{n_rows - n_err} de {n_rows} exemplos", "exemplos avaliados na avaliação final")
    expect(CONCLUSAO, f"{n_rows - n_err} de {n_rows} exemplos", "exemplos avaliados na avaliação final")
    grouped = {"required_tools", "no_unnecessary_tools", "tool_combination", "tool_inputs"}
    for key, value in s["overall"].items():
        if value is None:
            continue
        values = [value, m["overall"].get(key), b["overall"].get(key)]
        if key in grouped:
            check(all(v == 1.0 for v in values), f"{key} aparece agrupado com 1,000 na tabela da Fase 5: {values}")
            continue
        expect(FASE5, f"`{key}` | " + " | ".join(br(v, 3) for v in values), f"{key}: final × intermediária × base")

    def agent_rows(rows):
        return [r for r in rows if r["inputs"].get("mode") == "agent"]

    def statuses(run):
        counts = Counter((r["outputs"].get("verification") or {}).get("status") for r in agent_rows(run["rows"]))
        order = ("verified", "partially_verified", "unsupported", "contradicted")
        return ", ".join(f"{counts[k]} `{k}`" for k in order if counts[k])

    expect(FASE5, f"os {len(agent_rows(final['rows']))} exemplos do agente terminaram com {statuses(final)}", "status final")
    expect(FASE5, f"na base: {statuses(baseline)}", "status da avaliação de base")
    elapsed = [r["outputs"]["elapsed_s"] for r in agent_rows(final["rows"])]
    expect(FASE5, f"de {br(min(elapsed), 1)} s a {br(max(elapsed), 1)} s", "latência mínima e máxima da avaliação final")
    selection = [r["scores"]["tool_selection"]["score"] for r in final["rows"]
                 if r["scores"].get("tool_selection", {}).get("score") is not None]
    expect(DECISOES, f"em {sum(x == 1.0 for x in selection)} dos {len(selection)} exemplos do agente",
           "acertos de seleção de ferramenta")
    expect(DECISOES, f"`tool_selection` {br(s['overall']['tool_selection'], 3)}", "tool_selection no capítulo de decisões")
    corrected = sorted(r["id"] for r in final["rows"] if (r["outputs"].get("verification") or {}).get("corrected"))
    expect(FASE5, ", ".join(f"`{c}`" for c in corrected[:-1]) + f" e `{corrected[-1]}`", "exemplos corrigidos")
    if all(x["overall"].get("groundedness") == 1.0 for x in (s, m, b)):
        expect(DECISOES, "`groundedness` 1,0", "groundedness citado no capítulo de decisões")

    advice = load_json(EVIDENCE5 / "eval-local-advice-01.json")
    if advice:
        scores = [v["score"] for v in advice["rows"][0]["scores"].values() if v["score"] is not None]
        check(all(x == 1.0 for x in scores), f"advice-01 reavaliado deveria ter só notas 1,0: {scores}")
        expect(FASE5, "nota 1,0 em todos os avaliadores aplicáveis", "reavaliação do advice-01")


# --------------------------------------------------------------------------- testes


def verify_test_logs() -> None:
    integration = (EVIDENCE5 / "integration-run1.log").read_text(encoding="utf-8")
    match = re.search(r"(\d+) failed, (\d+) passed, (\d+) skipped in ([\d.]+)s", integration)
    check(match is not None, "integration-run1.log sem linha de resumo do pytest")
    if match:
        failed, passed, skipped, seconds = match.groups()
        expect(FASE5, f"{passed} passed, {failed} failed", "resultado da 1ª integração da Fase 5")
        expect(FASE5, f"{skipped} skipped", "testes pulados na 1ª integração")
        expect(FASE5, br(float(seconds), 2), "duração da 1ª integração")

    audit_path = EVIDENCE_FINAL / "pytest-deprecation-audit.log"
    audit = audit_path.read_text(encoding="utf-8")
    match = re.search(r"(\d+) passed", audit)
    check(match is not None, "auditoria de depreciação sem linha de resumo do pytest")
    body = "\n".join(line for line in audit.splitlines() if not line.startswith("#"))
    check(not re.search(r"Warning|failed|error", body), "a auditoria de depreciação registrou aviso, falha ou erro")
    if match:
        expect(DECISOES, f"**{match.group(1)} testes aprovados e nenhum aviso**", "contagem da auditoria")
        expect(CONCLUSAO, f"{match.group(1)} testes", "contagem final de testes")

    probe = EVIDENCE_FINAL / "advice-probe.jsonl"
    rows = [json.loads(line) for line in probe.read_text(encoding="utf-8").splitlines() if line.strip()]
    good = [r for r in rows if {"get_market_data"} <= set(r["tools"]) and r["declines_advice"] and r["answer"].strip()]
    expect(FASE5, f"{len(good)} de {len(rows)} execuções", "resultado do teste de pedido de recomendação")

    final_integration = EVIDENCE_FINAL / "integration-phase5.log"
    if final_integration.exists():
        log = final_integration.read_text(encoding="utf-8")
        summary = re.search(r"=+ (.*) in ([\d.]+)s", log.splitlines()[-1] if log.strip() else "")
        check(summary is not None, "integration-phase5.log sem linha de resumo")
        if summary:
            expect(FASE5, f"**{summary.group(1)}** em {br(float(summary.group(2)), 2)} s",
                   "resultado e duração da integração final da Fase 5")
        nli = re.search(r"acurácia (\d+)/(\d+); neutral→contradiction: (\d+)", log)
        check(nli is not None, "integration-phase5.log sem a linha de acurácia da NLI")
        if nli:
            expect(FASE5, f"acurácia {nli.group(1)}/{nli.group(2)}", "acurácia da NLI na integração final")
            pairs = Counter(tuple(m.groups()) for m in re.finditer(r"(entailment|contradiction|neutral) → (\w+)", log))
            for label in ("entailment", "contradiction", "neutral"):
                total = sum(n for (exp, _), n in pairs.items() if exp == label)
                hits = pairs[(label, label)]
                expect(FASE5, f"{hits}/{total} `{label}`", f"acertos da NLI para {label}")


def main() -> int:
    verify_benchmark()
    verify_text_to_query()
    verify_evaluation()
    verify_test_logs()
    if failures:
        print(f"{len(failures)} de {checked} verificações falharam:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"{checked} verificações de números: todas conferem com as evidências.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
