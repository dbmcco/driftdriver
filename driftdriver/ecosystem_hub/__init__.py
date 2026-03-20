# ABOUTME: Subpackage init that re-exports all public names for backward compatibility.
# ABOUTME: Ensures `from driftdriver.ecosystem_hub import X` still works for every X.
from __future__ import annotations

# Re-export models
from .models import (
    DraftPRRequest,
    NextWorkItem,
    RepoSnapshot,
    UpstreamCandidate,
)

# Re-export discovery / helpers
from .discovery import (
    _age_days,
    _collect_central_reports_summary,
    _collect_cross_repo_dependencies,
    _collect_repo_north_star,
    _compute_ready_tasks,
    _default_update_checker,
    _discover_active_workspace_repos,
    _extract_north_star_summary,
    _git_default_ref,
    _iso_now,
    _load_ecosystem_repo_meta,
    _load_ecosystem_repos,
    _normalize_dependencies,
    _north_star_candidate_paths,
    _parse_iso_datetime,
    _path_age_seconds,
    _policy_uses_speedrift,
    _process_alive,
    _read_json,
    _read_small_text,
    _repo_token_present,
    _run,
    _safe_ts_for_file,
    _service_port_alive,
    _write_json,
    apply_upstream_automation,
    build_draft_pr_requests,
    classify_upstream_candidate,
    generate_upstream_candidates,
    render_upstream_packets,
    resolve_central_repo_path,
    run_draft_pr_requests,
    write_central_register,
)

# Re-export snapshot / aggregation
from .snapshot import (
    _SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
    _SUPERVISOR_DEFAULT_MAX_STARTS,
    _SUPERVISOR_LAST_ATTEMPT,
    _attach_sec_qa_signals,
    _build_convergence_summary,
    _build_repo_narrative,
    _build_repo_task_graph,
    _decorate_snapshot_with_northstardrift,
    _derive_repo_activity_state,
    _finalize_repo_snapshot,
    _northstardrift_config,
    _repo_attention_entry,
    _service_agents_alive,
    _service_warning,
    _task_status_rank,
    build_ecosystem_narrative,
    build_ecosystem_overview,
    build_qadrift_overview,
    build_repo_dependency_overview,
    build_secdrift_overview,
    collect_ecosystem_snapshot,
    collect_repo_snapshot,
    rank_next_work,
    read_service_status,
    service_paths,
    supervise_repo_services,
    write_snapshot_once,
)

# Re-export websocket
from .websocket import (
    LiveStreamHub,
    _encode_ws_frame,
    _read_ws_frame,
    _recv_exact,
    _ws_accept_key,
)

# Re-export API handler
from .api import (
    _HubHandler,
    _handler_factory,
)

# Re-export dashboard
from .dashboard import render_dashboard_html

# Re-export server / CLI
from .server import (
    _CHILD_PROCS,
    _build_parser,
    main,
    run_service_foreground,
    start_service_process,
    stop_service_process,
)

# Module-level constants that were previously at the top of the monolith
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_STALE_OPEN_DAYS = 14.0
_STALE_IN_PROGRESS_DAYS = 3.0
_MAX_TASK_GRAPH_NODES = 140
_DISCOVERY_ACTIVE_DAYS = 30.0
_DISCOVERY_MAX_REPOS = 0
_NORTH_STAR_MAX_BYTES = 160_000


if __name__ == "__main__":
    raise SystemExit(main())
