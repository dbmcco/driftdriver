# North Star — driftdriver

Driftdriver is the judgment engine and ecosystem orchestrator for Workgraph-first agent development. It decides what is drifting across a multi-repo portfolio, what to do about it, and who should act — while Workgraph handles all execution. It hosts the ecosystem hub (port 8777), runs specialized drift lanes (coredrift, northstardrift, factorydrift, secdrift, qadrift, and nine optional external lanes), operates the Factory Brain (a tiered LLM supervisor for autonomous repo management), and drives repos toward declared attractor states through a diagnose-plan-execute convergence loop.

## Outcome target

Every enrolled repo converges to its declared attractor state (onboarded, production-ready, or hardened) with zero manual drift remediation — drift findings are detected, scored, and resolved through automated follow-up tasks, outcome feedback loops, and prompt evolution, with human judgment required only at the Gate layer.

## Current phase

**Active development** — Core judgment engine, directive interface (14 actions), ecosystem hub dashboard, Factory Brain (three-tier Haiku/Sonnet/Opus), attractor convergence loop, and northstardrift scoring (6-axis weighted model) are implemented and running across 40+ repos. Current work focuses on governance drift, conformance remediation, ecosystem evaluation, and intelligence/tracking features.

## Dependencies

| Service | Port | Role |
|---|---|---|
| Ecosystem Hub | 8777 | Dashboard, API, upstream discovery, factory cycle visualization, intelligence panel |
| Workgraph (`wg`) | CLI | Task graph spine — all tasks, dependencies, contracts, agent dispatch |
| Factory Brain | embedded | Three-tier LLM supervisor (Haiku/Sonnet/Opus) for autonomous repo monitoring |
| speedriftd | per-repo | Repo-local runtime supervisor — snapshots, worker ledgers, dispatch loops |
