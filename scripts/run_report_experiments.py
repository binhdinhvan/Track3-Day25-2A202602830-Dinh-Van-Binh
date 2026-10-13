"""Reproduce report ablations without changing production config or protected files."""
from __future__ import annotations

import copy
import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gameday_lab.client import ResilientClient
from gameday_lab.factory import build_client
from gameday_lab.sim.config import load_config
from gameday_lab.sim.engine import run_scenario


class UnfilteredClient(ResilientClient):
    """Intentionally unsafe client used ONLY for the capability-filter experiment."""

    def ladder(self, task: str) -> list[str]:
        return list(self.order)


def main() -> None:
    cfg = load_config()
    experiments = {}

    def run(label, config, keys, unsafe=False, cached=False):
        results = {}
        for key in keys:
            client = (UnfilteredClient(config.provider_infos(), config.fallback_order, config.client, seed=1)
                      if unsafe else build_client("cached" if cached else "resilient", config))
            results[key] = run_scenario(config, key, client, seed=25).metrics()
        experiments[label] = results

    run("baseline", cfg, ["G2_overload_storm", "G3_brownout", "G4_slow_region", "G6_provider_outage"])
    settings = copy.deepcopy(cfg.client)
    settings["breaker"]["min_calls"] = 1000000000
    settings["max_attempts_per_provider"] = 5
    run("bad_breaker_budget_on", replace(cfg, client=settings), ["G2_overload_storm", "G3_brownout"])
    disabled = copy.deepcopy(settings)
    disabled.update(retry_budget_ratio=100, retry_budget_max_tokens=1e9, retry_budget_min_per_s=1e6)
    run("bad_breaker_budget_off", replace(cfg, client=disabled), ["G2_overload_storm", "G3_brownout"])
    run("timeout_30", replace(cfg, client={**cfg.client, "attempt_timeout_s": 30}),
        ["G4_slow_region", "G6_provider_outage"])
    run("no_tool_filter", cfg, ["G6_provider_outage"], unsafe=True)
    cache_cfg = load_config(scenarios_path="configs/scenarios_cache.yaml")
    for threshold in (0.80, 0.97):
        run(f"similarity_{threshold}", replace(cache_cfg, cache={**cache_cfg.cache,
            "similarity_threshold": threshold}), ["C1_faq_outage"], cached=True)
    for ttl in (30, 60, 300):
        run(f"ttl_{ttl}", replace(cache_cfg, cache={**cache_cfg.cache, "ttl_s": ttl}),
            ["C1_faq_outage"], cached=True)
    run("no_stale", replace(cache_cfg, cache={**cache_cfg.cache, "max_stale_s": 0}),
        ["C1_faq_outage"], cached=True)

    with Path("reports/gameday/naive_G2_overload_storm_timeseries.csv").open() as f:
        rows = list(csv.DictReader(f))
    timeline = []
    for t in (120, 150, 180, 240, 300, 330, 455):
        win = [r for r in rows if t - 9 <= float(r["t"]) <= t]
        done = sum(float(r["calls_done"]) for r in win)
        ok = sum(float(r["calls_ok"]) for r in win)
        attempts = sum(sum(float(v) for k, v in r.items() if k.startswith("attempts_")) for r in win)
        timeline.append({"t": t, "attempts_per_s_10s": attempts / len(win),
                         "calls_done_10s": done, "call_success_ratio_10s": ok / done if done else None})
    experiments["g2_timeline"] = timeline
    out = Path("reports/experiments.json")
    out.write_text(json.dumps(experiments, indent=2), encoding="utf-8")
    print("wrote", out)
    for label, results in experiments.items():
        if isinstance(results, list):
            print(label, results)
            continue
        for key, m in results.items():
            fields = ["availability", "latency_p95_s", "amplification", "retry_ratio", "recovery_s",
                      "give_up_reasons", "incompatible_routes", "cache_hit_ratio", "wrong_answers",
                      "wrong_answers_by_reason", "outdated_answers", "faq_availability", "cost_per_good_turn"]
            print(label, key, {k: m[k] for k in fields if k in m})


if __name__ == "__main__":
    main()
