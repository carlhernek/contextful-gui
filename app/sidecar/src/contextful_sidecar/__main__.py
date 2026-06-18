"""NDJSON stdio entrypoint for the Contextful sidecar."""
from contextful_sidecar.server import run_server


def main() -> None:
    run_server()


if __name__ == "__main__":
    try:
        main()
    except (OSError, BrokenPipeError, SystemExit):
        # Parent closed the pipe (app quit). Exit quietly, never crash.
        pass
