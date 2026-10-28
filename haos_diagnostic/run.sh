#!/command/with-contenv sh
set -eu
cd /opt/surplus-pv
exec python3 -m app.diagnostic_server
