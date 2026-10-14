"""Turn results/arena_results.json into markdown tables (results/tables.md) for the report."""
import json

from arena import config as C

NAMES = {"A1": "Clockwork", "A2": "Forecaster", "A3": "Weather-regime", "A4": "Spike hunter", "A5": "Adaptive learner",
         "A5ts": "Adaptive learner, Thompson-sampling siting (first design)"}
ALGOS = ["A1", "A2", "A3", "A4", "A5"]


def k(v):
    """$/MW-yr -> $/kW-yr, one decimal."""
    return f"{v / 1000:.1f}"


def short(opt):
    mk, rest = opt.split(":", 1)
    z = rest.split(" [")[0]
    prog = rest.split(" [")[1][:-1] if " [" in rest else None
    lab = C.LABEL.get(z, z)
    return f"{lab}{' + ' + prog if prog else ''}" + (f" ({mk})" if mk == "NYISO" and not lab.endswith(")") else "")


def dispatch_league(bt, mk):
    lg = bt["markets"][mk]["league"]
    zones = bt["markets"][mk]["zones"]
    out = [f"| Algorithm | 1 year (2025) | 3 years (2023–25) | 5 years (2021–25) | $ per MWh discharged (5 y) | Share of perfect foresight (5 y) |",
           "|---|---:|---:|---:|---:|---:|"]
    for a in ALGOS:
        r = lg[a]
        out.append(f"| {a} {NAMES[a]} | {k(r['1y']['usd_per_mw_yr_mean'])} | {k(r['3y']['usd_per_mw_yr_mean'])} | "
                   f"{k(r['5y']['usd_per_mw_yr_mean'])} | {r['5y']['usd_per_mwh']:.0f} | {100 * r['5y']['capture_vs_oracle']:.0f}% |")
    o = lg["ORACLE"]
    out.append(f"| Perfect foresight (ceiling) | {k(o['1y']['usd_per_mw_yr_mean'])} | {k(o['3y']['usd_per_mw_yr_mean'])} | "
               f"{k(o['5y']['usd_per_mw_yr_mean'])} | | 100% |")
    # per region, 5-year
    reg = ["| Region | " + " | ".join(f"{a}" for a in ALGOS) + " | Ceiling | Best |", "|---|" + "---:|" * (len(ALGOS) + 2)]
    for z in zones:
        vals = [lg[a]["5y"]["usd_per_mw_yr_by_region"][z] for a in ALGOS]
        best = ALGOS[max(range(len(ALGOS)), key=lambda i: vals[i])]
        reg.append(f"| {C.LABEL[z]} | " + " | ".join(k(v) for v in vals) +
                   f" | {k(o['5y']['usd_per_mw_yr_by_region'][z])} | {best} |")
    return "\n".join(out), "\n".join(reg)


def portfolios(bt, scope):
    pf = bt["portfolios"][scope]
    out = ["| Algorithm | 1 year | 3 years | 5 years | Fleet after 5 years (MW) | Next 10 MW (using data through 2025) |",
           "|---|---:|---:|---:|---|---|"]
    for a in ALGOS + (["A5ts"] if "A5ts" in pf else []):
        r = pf[a]
        fleet = ", ".join(f"{short(o)} {mw:.0f}" for o, mw in sorted(r["5y"]["final_fleet"].items(), key=lambda x: -x[1]))
        nxt = ", ".join(f"{short(o)} {mw:.0f}" for o, mw in sorted(r["next_10mw"].items(), key=lambda x: -x[1]))
        out.append(f"| {a} {NAMES[a]} | {k(r['1y']['usd_per_mw_yr'])} | {k(r['3y']['usd_per_mw_yr'])} | "
                   f"{k(r['5y']['usd_per_mw_yr'])} | {fleet} | {nxt} |")
    return "\n".join(out)


def forward_board(fw, scope):
    b = fw["markets"][scope]["board"]
    out = ["| Algorithm | 2027 (1 y): mean · P10–P90 · wins | 2027–29 (3 y) | 2027–31 (5 y) |", "|---|---|---|---|"]
    for a in ALGOS + (["A5ts"] if "A5ts" in b else []):
        cells = []
        for h in ("1y", "3y", "5y"):
            s = b[a][h]
            win = f"{100 * s['win_rate']:.0f}%" if "win_rate" in s else "not ranked"
            cells.append(f"{k(s['mean'])} · {k(s['p10'])}–{k(s['p90'])} · {win}")
        out.append(f"| {a} {NAMES[a]} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def forward_fleets(fw, scope):
    b = fw["markets"][scope]["board"]
    out = ["| Algorithm | First 10 MW in 2027 | Average fleet in 2031 (MW of 50) |", "|---|---|---|"]
    for a in ALGOS:
        first = ", ".join(f"{short(o)} {mw:.0f}" for o, mw in sorted(b[a]["first_10mw_2027"].items(), key=lambda x: -x[1]))
        fl = ", ".join(f"{short(o)} {mw:.1f}" for o, mw in list(b[a]["fleet_2031_mean_mw"].items())[:6])
        out.append(f"| {a} {NAMES[a]} | {first} | {fl} |")
    return "\n".join(out)


def ranking(fw, scope, algo="A5", n=12):
    r = fw["markets"][scope]["ranking"][algo][:n]
    out = ["| Rank | Site (region + program) | Median 2027–29 $/kW-yr | Bad-case (P10) |", "|---:|---|---:|---:|"]
    for i, (o, med, p10) in enumerate(r, 1):
        out.append(f"| {i} | {short(o)} | {k(med)} | {k(p10)} |")
    return "\n".join(out)


def dispatch_forward(fw, mk):
    d = fw["dispatch_forward"][mk]
    zones = [z for z in d["A1"] if z != "backup_hours"]
    out = ["| Region | " + " | ".join(ALGOS) + " |", "|---|" + "---:|" * len(ALGOS)]
    for z in zones:
        out.append(f"| {C.LABEL[z]} | " + " | ".join(k(d[a][z]) for a in ALGOS) + " |")
    out.append("| Backup hours at outage start | " + " | ".join(f"{d[a]['backup_hours']:.1f}" for a in ALGOS) + " |")
    return "\n".join(out)


def infra(fw):
    out = ["| Transmission upgrade | Region | After − before, scenarios with it | Same split, scenarios without | "
           "Effect on a battery there ($/kW-yr) | Scenarios with / without |", "|---|---|---:|---:|---:|---:|"]
    for r in fw["infrastructure"]:
        e = r["effect"]
        out.append(f"| {r['event']} | {C.LABEL[r['zone']]} | {k(r['after_minus_before_with'])} | "
                   f"{k(r['after_minus_before_without'])} | {'+' if e >= 0 else '−'}{k(abs(e))} | "
                   f"{r['scenarios_with']} / {r['scenarios_without']} |")
    f = fw["feeder"]
    out.append("")
    out.append(f"Feeder hosting (repo SMART-DS feeder, an Oncor-suburb stand-in): {f['homes']} homes, {f['free_slots']} Cores fit "
               f"with no upgrade, {f['upgraded_slots']} more after {f['upgrades']} transformer upgrades "
               f"({100 * f['share_needing_upgrade']:.0f}% of a concentrated build). Upgrade cost spread over a concentrated "
               f"build: ${f['usd_per_kw_concentrated']:.0f}/kW, or ${fw['upgrade_usd_per_kw_yr']:.1f}/kW-yr over 12 years at 8%.")
    return "\n".join(out)


def decomposition(fw, n=14):
    out = ["| Site | Energy trading | Capacity | Program payment | Event energy | Transformer upgrades | Total ($/kW-yr) |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for d in fw["decomposition"][:n]:
        out.append(f"| {short(d['option'])} | {k(d['energy'])} | {k(d['capacity'])} | {k(d['program'])} | "
                   f"{k(d['event_energy'])} | {k(d['upgrades'])} | {k(d['total'])} |")
    return "\n".join(out)


def effects(eq, mk):
    rows = eq[mk]["effects"]
    out = ["| Region | July spread ($/MWh) | +5 °F hotter | Spike chance → hotter | January spread | −10 °F colder | "
           "Spike chance → colder | +50% gas: price · spread | Spike chance after a spike day |",
           "|---|---:|---:|---|---:|---:|---|---|---:|"]
    for r in rows:
        out.append(f"| {r['label']} | {r['summer_spread_usd']:.0f} | {r['summer_shock_spread_pct']:+.0f}% | "
                   f"{r['summer_spike_pct']:.1f}% → {r['summer_shock_spike_pct']:.1f}% | {r['winter_spread_usd']:.0f} | "
                   f"{r['winter_shock_spread_pct']:+.0f}% | {r['winter_spike_pct']:.1f}% → {r['winter_shock_spike_pct']:.1f}% | "
                   f"{r['gas50_level_pct']:+.0f}% · {r['gas50_spread_pct']:+.0f}% | {r['spike_after_spike_pct']:.1f}% |")
    sk = eq[mk]["spike_skill_vs_climatology"]
    out.append("")
    out.append("Spike-equation skill against each zone's historical spike rate (Brier skill, out of sample): " +
               ", ".join(f"{y} {v:+.2f}" for y, v in sk.items()) + ".")
    return "\n".join(out)


def expert_share(bt, mk):
    use = bt["markets"][mk]["league"].get("A5_expert_share", {})
    if not use:
        return ""
    names = list(next(iter(use.values())).keys())
    out = ["| Region | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
    for z, row in use.items():
        out.append(f"| {C.LABEL[z]} | " + " | ".join(f"{100 * row[n]:.0f}%" for n in names) + " |")
    return "\n".join(out)


def main():
    rep = json.load(open(C.OUT / "arena_results.json", encoding="utf-8"))
    parts = []
    for spec in ("backtest_home", "backtest_grid"):
        bt = rep[spec]
        for mk in ("ERCOT", "NYISO"):
            lg, reg = dispatch_league(bt, mk)
            parts += [f"## {spec} · {mk} · dispatch league ($/kW-yr)", lg, "", f"### {mk} by region, 5 years ($/kW-yr)", reg, ""]
            parts += [f"### {mk} adaptive learner: share of days following each expert", expert_share(bt, mk), ""]
        for scope in ("ERCOT", "NYISO", "BOTH"):
            parts += [f"## {spec} · siting portfolio · {scope} ($/kW-yr)", portfolios(bt, scope), ""]
    if "forward" in rep:
        fw = rep["forward"]
        for scope in ("ERCOT", "NYISO", "BOTH"):
            parts += [f"## forward · {scope} · portfolio $/kW-yr", forward_board(fw, scope), "",
                      f"### forward · {scope} · fleets", forward_fleets(fw, scope), "",
                      f"### forward · {scope} · site ranking (A5 dispatch)", ranking(fw, scope), ""]
        for mk in ("ERCOT", "NYISO"):
            parts += [f"## forward · {mk} · energy trading only, by region ($/kW-yr, median scenario, 2027-31)", dispatch_forward(fw, mk), ""]
        parts += ["## forward · infrastructure", infra(fw), "", "## forward · where the money comes from (A5, median, 2027-29)", decomposition(fw), ""]
    for mk in ("ERCOT", "NYISO"):
        parts += [f"## equations · {mk}", effects(rep["equations"], mk), ""]
    (C.OUT / "tables.md").write_text("\n".join(parts), encoding="utf-8")
    print("wrote", C.OUT / "tables.md")


if __name__ == "__main__":
    main()
