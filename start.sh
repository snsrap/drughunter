#!/usr/bin/env bash
# Start the viewer, first stopping any viewer server already on the same port.
#
#     ./start.sh            # port 8000, opens a browser
#     ./start.sh 9000       # pick a port
#     ./start.sh --no-open  # just serve
#
# Arguments are passed straight through to serve.py.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

port=8000
for a in "$@"; do
  case "$a" in --*) ;; *) port="$a" ;; esac
done

# Only stop a process that is actually this viewer's serve.py; anything else on
# the port is left alone and serve.py will report the port as taken.
for pid in $(ss -ltnpH "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
  if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q 'serve\.py'; then
    echo "stopping viewer already on port $port (pid $pid)"
    kill "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
  fi
done

exec python3 serve.py "$@"
