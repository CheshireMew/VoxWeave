from __future__ import annotations

import sys
import traceback

SERVICE_ARGUMENT = "--voxweave-service"
RELEASE_SMOKE_ARGUMENT = "--voxweave-release-smoke"
STORAGE_MIGRATION_ARGUMENT = "--voxweave-storage-migrate"
UPDATE_BOOTSTRAP_ARGUMENT = "--voxweave-update-bootstrap"


def _record_service_crash() -> None:
    from .config import resolve_data_root

    log_path = resolve_data_root() / "logs" / "service-crash.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    """Dispatch the frozen desktop executable or its background service process."""

    if len(sys.argv) > 1 and sys.argv[1] == RELEASE_SMOKE_ARGUMENT:
        del sys.argv[1]
        from voxweave.release_smoke import main as release_smoke_main

        return release_smoke_main(sys.argv[1:])

    if len(sys.argv) > 2 and sys.argv[1] == STORAGE_MIGRATION_ARGUMENT:
        from voxweave.storage_migration_helper import run_migration

        return run_migration(sys.argv[2])

    if len(sys.argv) > 4 and sys.argv[1] == UPDATE_BOOTSTRAP_ARGUMENT:
        from voxweave.update_bootstrap import run_update_bootstrap

        return run_update_bootstrap(sys.argv[2], sys.argv[3], sys.argv[4])

    if len(sys.argv) > 1 and sys.argv[1] == SERVICE_ARGUMENT:
        del sys.argv[1]
        from voxweave.service import main as service_main

        try:
            return service_main()
        except Exception:  # noqa: BLE001 - last-resort frozen process diagnostics
            _record_service_crash()
            return 1

    from voxweave.gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
