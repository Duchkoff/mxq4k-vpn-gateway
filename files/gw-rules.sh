#!/bin/sh
# VPN-gateway rules: forward LAN traffic into xray transparent inbound (dokodemo 12345)
LAN=192.168.3.0/24
BOX=192.168.3.59
DEV=eth0

iptables -t nat -F PREROUTING 2>/dev/null
iptables -t nat -F POSTROUTING 2>/dev/null

# TCP: everything from LAN (except box itself and local destinations) -> xray 12345
iptables -t nat -A PREROUTING -i $DEV -s $LAN -d $LAN -j RETURN
iptables -t nat -A PREROUTING -i $DEV -s $LAN -p tcp -j REDIRECT --to-ports 12345
# DNS (UDP) -> локальный dnsmasq на приставке (стабильно; через туннель UDP unreliable)
iptables -t nat -A PREROUTING -i $DEV -s $LAN -p udp --dport 53 -j REDIRECT --to-ports 53

# QUIC (UDP/443): блокируем, клиенты падают на TCP -> туннель работает
iptables -F FORWARD 2>/dev/null
iptables -A FORWARD -i $DEV -s $LAN -p udp --dport 443 -j REJECT --reject-with icmp-port-unreachable
iptables -A FORWARD -i $DEV -s $LAN -j ACCEPT

# symmetric return path
iptables -t nat -A POSTROUTING -o $DEV -j MASQUERADE

sysctl -w net.ipv4.ip_forward=1 >/dev/null
