#!/usr/bin/env bash
# headless-kwin.sh -- a throwaway KWin Wayland compositor with no display, for testing
# the lyric overlay without a graphical session.
#
#   scripts/headless-kwin.sh start            start it (no-op if already running)
#   scripts/headless-kwin.sh stop             stop it
#   scripts/headless-kwin.sh status           say whether it is running
#   scripts/headless-kwin.sh run <command...> run a command inside it, starting it first
#                                             and stopping it afterwards if this call started it
#   scripts/headless-kwin.sh windows          print the compositor's own view of its windows
#                                             as JSON, bottom of the stack first
#
# The compositor renders to a virtual framebuffer (kwin_wayland --virtual) on its own
# private D-Bus session, so it cannot touch a real desktop session or its KWin. Clients
# reach it through WAYLAND_DISPLAY, which `run` sets; after `start`, set it yourself:
#   WAYLAND_DISPLAY=wl-pp-test <command>
#
# `windows` asks KWin itself, through its scripting interface, so a test can check from
# outside a client which layer that client's surface really landed on. Each entry has
# resourceClass, layer (KWin's number: 2 normal, 5 active/fullscreen, 8 on-screen display,
# 9 overlay) and geometry as [x, y, width, height].
#
# Settings (environment, optional):
#   PLAYLIST_PLAYER_TEST_WAYLAND   socket name, default wl-pp-test
#   PLAYLIST_PLAYER_TEST_SIZE      virtual screen as WIDTHxHEIGHT, default 1920x1080
#   PLAYLIST_PLAYER_TEST_OUTPUTS   number of virtual screens, side by side, default 1
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
SOCKET="${PLAYLIST_PLAYER_TEST_WAYLAND:-wl-pp-test}"
SIZE="${PLAYLIST_PLAYER_TEST_SIZE:-1920x1080}"
OUTPUTS="${PLAYLIST_PLAYER_TEST_OUTPUTS:-1}"
STATE_DIR="$XDG_RUNTIME_DIR/playlist-player-test"
PID_FILE="$STATE_DIR/$SOCKET.pid"
BUS_FILE="$STATE_DIR/$SOCKET.bus"
LOG_FILE="$STATE_DIR/$SOCKET.log"

die() {
    echo "headless-kwin: $*" >&2
    exit 1
}

is_running() {
    [ -s "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null && [ -S "$XDG_RUNTIME_DIR/$SOCKET" ]
}

start() {
    if is_running; then
        return 0
    fi
    command -v kwin_wayland >/dev/null || die "kwin_wayland is not installed"
    command -v dbus-run-session >/dev/null || die "dbus-run-session is not installed"
    [ -d "$XDG_RUNTIME_DIR" ] || die "XDG_RUNTIME_DIR does not exist: $XDG_RUNTIME_DIR"
    mkdir -p "$STATE_DIR"
    chmod 700 "$STATE_DIR"
    rm -f "$PID_FILE" "$BUS_FILE" "$XDG_RUNTIME_DIR/$SOCKET" "$XDG_RUNTIME_DIR/$SOCKET.lock"

    # The inner shell records the private bus address and its own PID, then becomes KWin,
    # so the PID file names the compositor itself. KWin logs OpenGL framebuffer warnings
    # when headless; they are harmless and go to the log file.
    # shellcheck disable=SC2016  # expanded by the inner shell, on purpose
    WAYLAND_DISPLAY='' DISPLAY='' QT_QPA_PLATFORM='' setsid dbus-run-session -- sh -c '
        printf "%s\n" "$DBUS_SESSION_BUS_ADDRESS" >"$1"
        printf "%s\n" "$$" >"$2"
        exec kwin_wayland --virtual --no-lockscreen --no-global-shortcuts \
            --width "$3" --height "$4" --socket "$5" --output-count "$6"
    ' sh "$BUS_FILE" "$PID_FILE" "${SIZE%x*}" "${SIZE#*x}" "$SOCKET" "$OUTPUTS" >"$LOG_FILE" 2>&1 &

    for _ in $(seq 100); do
        if is_running; then
            return 0
        fi
        sleep 0.1
    done
    echo "headless-kwin: the compositor did not come up; last log lines:" >&2
    tail -n 15 "$LOG_FILE" >&2 || true
    stop
    exit 1
}

stop() {
    if [ -s "$PID_FILE" ]; then
        pid="$(cat "$PID_FILE")"
        kill "$pid" 2>/dev/null || true
        for _ in $(seq 30); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.1
        done
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" "$BUS_FILE" "$XDG_RUNTIME_DIR/$SOCKET" "$XDG_RUNTIME_DIR/$SOCKET.lock"
}

windows() {
    is_running || die "the compositor is not running"
    command -v qdbus6 >/dev/null || die "qdbus6 is not installed"
    local nonce script name id line
    nonce="PPWINDOWS-$$-$RANDOM"
    name="pp-windows-$$-$RANDOM"
    script="$STATE_DIR/$name.js"
    # KWin scripts cannot return a value; console output lands in the compositor's log,
    # so the script tags its one line with a nonce and we read that line back.
    cat >"$script" <<JS
const found = [];
for (const w of workspace.stackingOrder) {
    const g = w.frameGeometry;
    found.push({
        resourceClass: String(w.resourceClass),
        layer: w.layer,
        geometry: [g.x, g.y, g.width, g.height],
    });
}
console.info("$nonce " + JSON.stringify(found));
JS
    export DBUS_SESSION_BUS_ADDRESS
    DBUS_SESSION_BUS_ADDRESS="$(cat "$BUS_FILE")"
    id="$(qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript "$script" "$name")" ||
        die "KWin refused to load the script"
    qdbus6 org.kde.KWin "/Scripting/Script$id" org.kde.kwin.Script.run >/dev/null ||
        die "KWin refused to run the script"
    line=""
    for _ in $(seq 50); do
        line="$(grep -F "$nonce " "$LOG_FILE" | tail -n 1 || true)"
        [ -n "$line" ] && break
        sleep 0.05
    done
    qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "$name" >/dev/null || true
    rm -f "$script"
    [ -n "$line" ] || die "KWin ran the script but logged no answer"
    printf '%s\n' "${line#*"$nonce" }"
}

case "${1:-}" in
    windows) windows ;;
    start)
        start
        echo "$SOCKET"
        ;;
    stop) stop ;;
    status)
        if is_running; then
            echo "running: WAYLAND_DISPLAY=$SOCKET (pid $(cat "$PID_FILE"))"
        else
            echo "not running"
            exit 1
        fi
        ;;
    run)
        shift
        [ $# -ge 1 ] || die "run needs a command"
        started_here=0
        if ! is_running; then
            start
            started_here=1
        fi
        status=0
        WAYLAND_DISPLAY="$SOCKET" DBUS_SESSION_BUS_ADDRESS="$(cat "$BUS_FILE")" \
            GDK_BACKEND=wayland "$@" || status=$?
        if [ "$started_here" -eq 1 ]; then
            stop
        fi
        exit "$status"
        ;;
    *)
        sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
        exit 1
        ;;
esac
