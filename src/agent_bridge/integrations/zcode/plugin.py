"""ZCode local plugin entrypoint delegates to Agent Bridge's stdio consumer."""

from agent_bridge.adapters.integration import main


if __name__ == "__main__":
    raise SystemExit(main())
