#!/usr/bin/env bash
# Installs bharat-diagnose as a systemd *user* service that records and reports on every
# Bharat Browser session (starts at login, idles cheaply until the browser is launched).
#
#   tools/diagnose-service.sh install     copy the tool to ~/.local/share/bharat-diagnose and enable it
#   tools/diagnose-service.sh uninstall   stop, disable, remove the service and the installed copy
#   tools/diagnose-service.sh status | logs | latest
#
# Logs: <project>/logs/session-<timestamp>.json, latest.json -> newest (last 50 kept)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HOME}/.local/share/bharat-diagnose"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT="${UNIT_DIR}/bharat-diagnose.service"
REPORTS="$(cd "$SRC/.." && pwd)/logs"   # <project>/logs, JSON log per session

case "${1:-status}" in
    install)
        # Copy, so the service keeps working if this checkout moves; re-run to pick up updates.
        mkdir -p "$DEST" "$UNIT_DIR" "$REPORTS"
        install -m 0755 "$SRC/bharat-diagnose.py" "$SRC/monitor-resources.py" "$DEST/"
        cat > "$UNIT" <<UNIT
[Unit]
Description=Bharat Browser diagnostics (reports on every browser session)

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${DEST}/bharat-diagnose.py --watch --watch-dir ${REPORTS} --notify --quiet
Restart=on-failure
RestartSec=15
Nice=10
IOSchedulingClass=idle
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
UNIT
        systemctl --user daemon-reload
        systemctl --user enable --now bharat-diagnose.service
        mkdir -p "$REPORTS"
        echo "Installed. JSON logs: ${REPORTS}/ (latest.json = newest session)"
        systemctl --user status bharat-diagnose.service --no-pager || true
        ;;
    uninstall)
        systemctl --user disable --now bharat-diagnose.service 2>/dev/null || true
        rm -f "$UNIT"
        rm -rf "$DEST"
        systemctl --user daemon-reload
        echo "Removed service and installed copy. Logs in ${REPORTS} were kept."
        ;;
    status) systemctl --user status bharat-diagnose.service --no-pager ;;
    logs)   journalctl --user -u bharat-diagnose.service -n 50 --no-pager ;;
    latest) cat "${REPORTS}/latest.json" ;;
    *) echo "Usage: $0 {install|uninstall|status|logs|latest}"; exit 1 ;;
esac
