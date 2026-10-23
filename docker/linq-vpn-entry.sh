#!/bin/sh
set -e
umask 077
mkdir -p /run/vpn
grep -E '^VPN_login=' /creds | head -1 | cut -d= -f2- | tr -d '\r' > /run/vpn/auth
grep -E '^VPN_pass=' /creds | head -1 | cut -d= -f2- | tr -d '\r' >> /run/vpn/auth

openvpn --config /vpn/client.ovpn --auth-user-pass /run/vpn/auth --log /run/vpn/openvpn.log --daemon
for i in $(seq 1 60); do
    if grep -q "Initialization Sequence Completed" /run/vpn/openvpn.log 2>/dev/null; then break; fi
    if grep -qE "AUTH_FAILED|Exiting due to fatal error" /run/vpn/openvpn.log 2>/dev/null; then
        echo "VPN: ошибка подключения"; grep -E "AUTH_FAILED|fatal|ERROR" /run/vpn/openvpn.log | tail -5; exit 1
    fi
    sleep 1
done
if ! grep -q "Initialization Sequence Completed" /run/vpn/openvpn.log; then
    echo "VPN: не поднялся за 60 с"; tail -5 /run/vpn/openvpn.log; exit 1
fi
dns=$(grep -o "dhcp-option DNS [0-9.]*" /run/vpn/openvpn.log | head -1 | awk '{print $3}')
host=${LINQ_HOST:-hackathon.lin-q.ru}
ip=$( [ -n "$dns" ] && dig +short +time=3 "$host" @"$dns" | tail -1 )
[ -n "$ip" ] && echo "$ip $host" >> /etc/hosts
echo "VPN: подключен ($(ip -4 addr show tun0 | awk '/inet /{print $2}')), $host -> ${ip:-не найден}"
LINQ_LOGIN=$(grep -E '^jupyter_notebook_log=' /creds | head -1 | cut -d= -f2- | tr -d '\r')
export LINQ_LOGIN
exec "$@"
