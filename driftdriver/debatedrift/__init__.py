# ABOUTME: debatedrift — three-agent tmux debate lane for speedrift.
# ABOUTME: Exposes run_as_lane() for the internal lane interface and session management.


def __getattr__(name: str):
    if name == "run_as_lane":
        from driftdriver.debatedrift.lane import run_as_lane
        return run_as_lane
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["run_as_lane"]
