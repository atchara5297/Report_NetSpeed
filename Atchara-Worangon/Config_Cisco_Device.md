truncate -s 0 Client10 \&\& nano Client10

cd my\_flask\_app

cd Report\_NetSpeed

#### เข้าไฟล์

cd /home/user01/my\_flask\_app/Report\_NetSpeed \&\& source /home/user01/my\_flask\_app/venv/bin/activate \&\& python3 app.py

## รันเบื้องหลัง แก้ปัญหาdisconnect



sudo systemctl stop network-monitor

sudo systemctl start network-monitor

###### sudo systemctl restart network-monitor







# **Router**

enable

configure terminal

hostname Router

!

interface GigabitEthernet0/0

&#x20;description Link-to-Net-WAN

&#x20;ip address 192.168.155.129 255.255.255.0

&#x20;ip nat outside

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Trunk-to-CoreSwitch

&#x20;no shutdown

!

interface GigabitEthernet0/1.10

&#x20;encapsulation dot1Q 10

&#x20;ip address 192.168.99.1 255.255.255.192

&#x20;ip nat inside

!

interface GigabitEthernet0/1.20

&#x20;encapsulation dot1Q 20

&#x20;ip address 192.168.99.65 255.255.255.192

&#x20;ip nat inside

!

interface GigabitEthernet0/1.30

&#x20;encapsulation dot1Q 30

&#x20;ip address 192.168.99.129 255.255.255.192

&#x20;ip nat inside

!

interface GigabitEthernet0/1.40

&#x20;encapsulation dot1Q 40

&#x20;ip address 192.168.99.193 255.255.255.192

&#x20;ip nat inside

!

ip route 0.0.0.0 0.0.0.0 192.168.155.1

!

router rip

&#x20;version 2

&#x20;no auto-summary

&#x20;network 192.168.99.0

&#x20;default-information originate

&#x20;redistribute static

!

ip dhcp excluded-address 192.168.99.1 192.168.99.10

ip dhcp excluded-address 192.168.99.65 192.168.99.74

ip dhcp excluded-address 192.168.99.129 192.168.99.138

ip dhcp excluded-address 192.168.99.193 192.168.99.202

!

ip dhcp pool VLAN10

&#x20;network 192.168.99.0 255.255.255.192

&#x20;default-router 192.168.99.1

&#x20;dns-server 10.184.235.101 158.108.110.3

!

ip dhcp pool VLAN20

&#x20;network 192.168.99.64 255.255.255.192

&#x20;default-router 192.168.99.65

&#x20;dns-server 10.184.235.101 158.108.110.3

!

ip dhcp pool VLAN30

&#x20;network 192.168.99.128 255.255.255.192

&#x20;default-router 192.168.99.129

&#x20;dns-server 10.184.235.101 158.108.110.3

!

ip dhcp pool VLAN40

&#x20;network 192.168.99.192 255.255.255.192

&#x20;default-router 192.168.99.193

&#x20;dns-server 10.184.235.101 158.108.110.3

!

ip access-list standard NAT

&#x20;permit 192.168.99.0 0.0.0.63

&#x20;permit 192.168.99.64 0.0.0.63

&#x20;permit 192.168.99.128 0.0.0.63

&#x20;permit 192.168.99.192 0.0.0.63

!

ip nat inside source list NAT interface GigabitEthernet0/0 overload

!

end

write memory





# **Telnet Router No Passwd**



enable

configure terminal



line vty 0 4

&#x20;no password

&#x20;no login

&#x20;transport input telnet ssh

exit



end

write memory



# **Telnet Router With Passwd**



enable

configure terminal



username admin privilege 15 secret 1234



line vty 0 4

&#x20;login local

&#x20;transport input telnet ssh

exit



end

write memory



ping 192.168.99.2

ping 192.168.99.3

ping 192.168.99.66

ping 192.168.99.130

ping 192.168.99.194





sudo systemctl status network-monitor	ดูสถานะ Flask	เช็คว่าทำงานไหม

sudo systemctl stop network-monitor	หยุด Flask ทั้งตัว

sudo journalctl -u network-monitor -f	ดู Log แบบสด

sudo journalctl -u network-monitor -b	ดู Log ตั้งแต่เปิด/Boot Ubuntu รอบนี้

sudo systemctl status network-monitor	เช็กสถานะอีกครั้ง



ดู Log สด	sudo journalctl -u network-monitor -f

ปิด Flask	sudo systemctl stop network-monitor

เปิด Flask	sudo systemctl start network-monitor









# **CoreSwitch**



enable

configure terminal

hostname CoreSwitch

!

vlan 10

!

vlan 20

!

vlan 30

!

vlan 40

!

interface GigabitEthernet0/0

&#x20;description Uplink-to-Router

&#x20;switchport trunk encapsulation dot1q

&#x20;switchport mode trunk

&#x20;switchport trunk allowed vlan 10,20,30,40

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Trunk-to-Switch01-Floor1

&#x20;switchport trunk encapsulation dot1q

&#x20;switchport mode trunk

&#x20;switchport trunk allowed vlan 10,20,30,40

&#x20;no shutdown

!

interface GigabitEthernet0/2

&#x20;description Trunk-to-Switch02-Floor2

&#x20;switchport trunk encapsulation dot1q

&#x20;switchport mode trunk

&#x20;switchport trunk allowed vlan 10,20,30,40

&#x20;no shutdown

!

interface GigabitEthernet0/3

&#x20;description Trunk-to-Switch03-Floor3

&#x20;switchport trunk encapsulation dot1q

&#x20;switchport mode trunk

&#x20;switchport trunk allowed vlan 10,20,30,40

&#x20;no shutdown

!

interface GigabitEthernet1/0

&#x20;description Trunk-to-Switch04-Floor4

&#x20;switchport trunk encapsulation dot1q

&#x20;switchport mode trunk

&#x20;switchport trunk allowed vlan 10,20,30,40

&#x20;no shutdown

!

no cdp run

!

interface Vlan10

&#x20;ip address 192.168.99.2 255.255.255.192

&#x20;no shutdown

!

ip default-gateway 192.168.99.1

!

end

write memory

# **Switch01**



enable

configure terminal

hostname Switch01

!

vlan 10

!

vlan 20

!

vlan 30

!

vlan 40

!

interface GigabitEthernet0/0

&#x20;description Trunk-to-CoreSwitch

&#x20;switchport trunk encapsulation dot1q

&#x20;switchport mode trunk

&#x20;switchport trunk allowed vlan 10,20,30,40

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Access-Floor1-Room1-VLAN10

&#x20;switchport mode access

&#x20;switchport access vlan 10

&#x20;no shutdown

!

interface GigabitEthernet0/2

&#x20;description Access-Floor1-Room2-VLAN20

&#x20;switchport mode access

&#x20;switchport access vlan 20

&#x20;no shutdown

!

interface GigabitEthernet0/3

&#x20;description Access-Floor1-Room3-VLAN30

&#x20;switchport mode access

&#x20;switchport access vlan 30

&#x20;no shutdown

!

interface GigabitEthernet1/0

&#x20;description Access-Floor1-Room4-VLAN40

&#x20;switchport mode access

&#x20;switchport access vlan 40

&#x20;no shutdown

!

interface GigabitEthernet1/1

&#x20;description Access-Client10-VLAN10

&#x20;switchport mode access

&#x20;switchport access vlan 10

&#x20;spanning-tree portfast

&#x20;no shutdown

!

interface GigabitEthernet1/2

&#x20;description Access-LinuxServer01-VLAN10

&#x20;switchport mode access

&#x20;switchport access vlan 10

&#x20;spanning-tree portfast

&#x20;no shutdown

!

interface vlan 10

&#x20;ip address 192.168.99.3 255.255.255.192

&#x20;no shutdown

exit

ip default-gateway 192.168.99.1

end

write memory

# **Switch02**



enable

configure terminal

hostname Switch02

vlan 10

!

vlan 20

!

vlan 30

!

vlan 40

!

interface GigabitEthernet0/0

&#x20;description Trunk-to-CoreSwitch

&#x20;switchport trunk encapsulation dot1q

&#x20;switchport mode trunk

&#x20;switchport trunk allowed vlan 10,20,30,40

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Access-Floor2-Room1-VLAN10

&#x20;switchport mode access

&#x20;switchport access vlan 10

&#x20;no shutdown

!

interface GigabitEthernet0/2

&#x20;description Access-Floor2-Room2-VLAN20

&#x20;switchport mode access

&#x20;switchport access vlan 20

&#x20;no shutdown

!

interface GigabitEthernet0/3

&#x20;description Access-Floor2-Room3-VLAN30

&#x20;switchport mode access

&#x20;switchport access vlan 30

&#x20;no shutdown

!

interface GigabitEthernet1/0

&#x20;description Access-Floor2-Room4-VLAN40

&#x20;switchport mode access

&#x20;switchport access vlan 40

&#x20;no shutdown

!

interface GigabitEthernet1/1

&#x20;description Access-Client20-VLAN20

&#x20;switchport mode access

&#x20;switchport access vlan 20

&#x20;spanning-tree portfast

&#x20;no shutdown

!

interface GigabitEthernet1/2

&#x20;description Access-LinuxServer02-VLAN20

&#x20;switchport mode access

&#x20;switchport access vlan 20

&#x20;spanning-tree portfast

&#x20;no shutdown

!

interface vlan 20

&#x20;ip address 192.168.99.66 255.255.255.192

&#x20;no shutdown

exit

ip default-gateway 192.168.99.65

end

write memory

# **Switch03**

enable

configure terminal

hostname Switch03

!

vlan 10

!

vlan 20

!

vlan 30

!

vlan 40

!

interface GigabitEthernet0/0

&#x20;description Trunk-to-CoreSwitch

&#x20;switchport trunk encapsulation dot1q

&#x20;switchport mode trunk

&#x20;switchport trunk allowed vlan 10,20,30,40

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Access-Floor3-Room1-VLAN10

&#x20;switchport mode access

&#x20;switchport access vlan 10

&#x20;no shutdown

!

interface GigabitEthernet0/2

&#x20;description Access-Floor3-Room2-VLAN10

&#x20;switchport mode access

&#x20;switchport access vlan 10

&#x20;no shutdown

!

interface GigabitEthernet0/3

&#x20;description Access-Floor3-Room3-VLAN20

&#x20;switchport mode access

&#x20;switchport access vlan 20

&#x20;no shutdown

!

interface GigabitEthernet1/0

&#x20;description Access-Floor3-Room4-VLAN20

&#x20;switchport mode access

&#x20;switchport access vlan 20

&#x20;no shutdown

!

interface GigabitEthernet1/1

&#x20;description Access-Client30-VLAN30

&#x20;switchport mode access

&#x20;switchport access vlan 30

&#x20;spanning-tree portfast

&#x20;no shutdown

!

interface GigabitEthernet1/2

&#x20;description Access-LinuxServer03-VLAN30

&#x20;switchport mode access

&#x20;switchport access vlan 30

&#x20;spanning-tree portfast

&#x20;no shutdown

!

interface vlan 30

&#x20;ip address 192.168.99.130 255.255.255.192

&#x20;no shutdown

exit

ip default-gateway 192.168.99.129

end

write memory

# **Switch04**



enable

configure terminal

hostname Switch04

!

vlan 10

!

vlan 20

!

vlan 30

!

vlan 40

!

interface GigabitEthernet0/0

&#x20;description Trunk-to-CoreSwitch

&#x20;switchport trunk encapsulation dot1q

&#x20;switchport mode trunk

&#x20;switchport trunk allowed vlan 10,20,30,40

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Access-Floor4-Room1-VLAN30

&#x20;switchport mode access

&#x20;switchport access vlan 30

&#x20;no shutdown

!

interface GigabitEthernet0/2

&#x20;description Access-Floor4-Room2-VLAN30

&#x20;switchport mode access

&#x20;switchport access vlan 30

&#x20;no shutdown

!

interface GigabitEthernet0/3

&#x20;description Access-Floor4-Room3-VLAN40

&#x20;switchport mode access

&#x20;switchport access vlan 40

&#x20;no shutdown

!

interface GigabitEthernet1/0

&#x20;description Access-Floor4-Room4-VLAN40

&#x20;switchport mode access

&#x20;switchport access vlan 40

&#x20;no shutdown

!

interface GigabitEthernet1/1

&#x20;description Access-Client40-VLAN40

&#x20;switchport mode access

&#x20;switchport access vlan 40

&#x20;spanning-tree portfast

&#x20;no shutdown

!

interface GigabitEthernet1/2

&#x20;description Access-LinuxServer04-VLAN40

&#x20;switchport mode access

&#x20;switchport access vlan 40

&#x20;spanning-tree portfast

&#x20;no shutdown

!

interface vlan 40

&#x20;ip address 192.168.99.194 255.255.255.192

&#x20;no shutdown

exit

ip default-gateway 192.168.99.193

end

write memory

# **LinuxServer01 (VLAN10)**

sudo bash -c 'cat > /etc/netplan/01-netcfg.yaml << EOF

network:

&#x20; version: 2

&#x20; renderer: networkd

&#x20; ethernets:

&#x20;   ens3:

&#x20;     dhcp4: no

&#x20;     addresses:

&#x20;       - 192.168.99.10/26

&#x20;     routes:

&#x20;       - to: default

&#x20;         via: 192.168.99.1

&#x20;     nameservers:

&#x20;       addresses: \[10.184.235.101, 158.108.110.3]

EOF'

sudo chmod 600 /etc/netplan/01-netcfg.yaml

sudo netplan apply



iperf3 -s -p 5201

iperf3 -c 192.168.99.10 -p 5201 -t 60

iperf3 -c 192.168.99.10 -p 5201 -t 60 -J | tee client10\_result.json



###### 

เปิดแบบปกติ  iperf3 -s -p 5201

###### ปิดพอร์ต



pkill iperf3



# **LinuxServer02 (VLAN20)**

sudo bash -c 'cat > /etc/netplan/01-netcfg.yaml << EOF

network:

&#x20; version: 2

&#x20; renderer: networkd

&#x20; ethernets:

&#x20;   ens3:

&#x20;     dhcp4: no

&#x20;     addresses:

&#x20;       - 192.168.99.74/26

&#x20;     routes:

&#x20;       - to: default

&#x20;         via: 192.168.99.65

&#x20;     nameservers:

&#x20;       addresses: \[10.184.235.101, 158.108.110.3]

EOF'

sudo chmod 600 /etc/netplan/01-netcfg.yaml

sudo netplan apply



iperf3 -s -p 5202

iperf3 -c 192.168.99.74 -p 5202 -t 60

iperf3 -c 192.168.99.74 -p 5202 -t 60 -J | tee client20\_result.json





iperf3 -s -p 5201 -D

iperf3 -s -p 5202 -D

iperf3 -s -p 5203 -D

iperf3 -s -p 5204 -D

ps aux | grep iperf3

###### 

เปิดแบบปกติ  iperf3 -s -p 5202

###### ปิดพอร์ต



pkill iperf3



# **LinuxServer03 (VLAN30)**



sudo bash -c 'cat > /etc/netplan/01-netcfg.yaml << EOF

network:

&#x20; version: 2

&#x20; renderer: networkd

&#x20; ethernets:

&#x20;   ens3:

&#x20;     dhcp4: no

&#x20;     addresses:

&#x20;       - 192.168.99.138/26

&#x20;     routes:

&#x20;       - to: default

&#x20;         via: 192.168.99.129

&#x20;     nameservers:

&#x20;       addresses: \[10.184.235.101, 158.108.110.3]

EOF'

sudo chmod 600 /etc/netplan/01-netcfg.yaml

sudo netplan apply



iperf3 -s -p 5203

iperf3 -c 192.168.99.138 -p 5203 -t 60

iperf3 -c 192.168.99.138 -p 5203 -t 60 -J | tee client30\_result.json







iperf3 -s -p 5201 -D

iperf3 -s -p 5202 -D

iperf3 -s -p 5203 -D

iperf3 -s -p 5204 -D

ps aux | grep iperf3

###### 

เปิดแบบปกติ  iperf3 -s -p 5201

###### ปิดพอร์ต



pkill iperf3



# **LinuxServer04 (VLAN40)**



sudo bash -c 'cat > /etc/netplan/01-netcfg.yaml << EOF

network:

&#x20; version: 2

&#x20; renderer: networkd

&#x20; ethernets:

&#x20;   ens3:

&#x20;     dhcp4: no

&#x20;     addresses:

&#x20;       - 192.168.99.202/26

&#x20;     routes:

&#x20;       - to: default

&#x20;         via: 192.168.99.193

&#x20;     nameservers:

&#x20;       addresses: \[10.184.235.101, 158.108.110.3]

EOF'

sudo chmod 600 /etc/netplan/01-netcfg.yaml

sudo netplan apply



iperf3 -s -p 5204

iperf3 -c 192.168.99.202 -p 5204 -t 60 -J | tee client40\_result.json



iperf3 -s -p 5201 -D

iperf3 -s -p 5202 -D

iperf3 -s -p 5203 -D

iperf3 -s -p 5204 -D

ps aux | grep iperf3

###### 

เปิดแบบปกติ  iperf3 -s -p 5201

###### ปิดพอร์ต



pkill iperf3





# **Client-10 (VLAN10)**



sudo bash -c 'cat > /etc/netplan/01-netcfg.yaml << EOF

network:

&#x20; version: 2

&#x20; renderer: networkd

&#x20; ethernets:

&#x20;   ens3:

&#x20;     dhcp4: true

&#x20;     nameservers:

&#x20;       addresses:

&#x20;         - 10.184.235.101

&#x20;         - 158.108.110.3

EOF'



sudo chmod 600 /etc/netplan/01-netcfg.yaml

sudo netplan apply



ip a

ip route



ping -c 4 192.168.99.1        # gateway (เหมือนเดิม)

ping -c 4 192.168.99.10       # LinuxServer01 (แทน 192.168.100.10 เดิม)



iperf3 -c 192.168.99.10 -p 5201 -t 60 -J | tee client10\_result.json

sudo apt update

sudo apt install -y iperf3

iperf3 --version











# **Client-20 (VLAN20)**



sudo bash -c 'cat > /etc/netplan/01-netcfg.yaml << EOF

network:

&#x20; version: 2

&#x20; renderer: networkd

&#x20; ethernets:

&#x20;   ens3:

&#x20;     dhcp4: true

&#x20;     nameservers:

&#x20;       addresses:

&#x20;         - 10.184.235.101

&#x20;         - 158.108.110.3

EOF'



sudo chmod 600 /etc/netplan/01-netcfg.yaml

sudo netplan apply



ip a

ip route



ping -c 4 192.168.99.65

ping -c 4 192.168.99.74



sudo apt update

sudo apt install -y iperf3

iperf3 --version



iperf3 -c 192.168.99.74 -p 5202 -t 60 -J | tee client20\_result.json







# **Client-30 (VLAN30)**



sudo bash -c 'cat > /etc/netplan/01-netcfg.yaml << EOF

network:

&#x20; version: 2

&#x20; renderer: networkd

&#x20; ethernets:

&#x20;   ens3:

&#x20;     dhcp4: true

&#x20;     nameservers:

&#x20;       addresses:

&#x20;         - 10.184.235.101

&#x20;         - 158.108.110.3

EOF'



sudo chmod 600 /etc/netplan/01-netcfg.yaml

sudo netplan apply



ip a

ip route



ping -c 4 192.168.99.129

ping -c 4 192.168.99.138



sudo apt update

sudo apt install -y iperf3

iperf3 --version



iperf3 -c 192.168.99.138 -p 5203 -t 60 -J | tee client30\_result.json





# 

# **Client-40 (VLAN40)**

sudo bash -c 'cat > /etc/netplan/01-netcfg.yaml << EOF

network:

&#x20; version: 2

&#x20; renderer: networkd

&#x20; ethernets:

&#x20;   ens3:

&#x20;     dhcp4: true

&#x20;     nameservers:

&#x20;       addresses:

&#x20;         - 10.184.235.101

&#x20;         - 158.108.110.3

EOF'



sudo chmod 600 /etc/netplan/01-netcfg.yaml

sudo netplan apply



ip a

ip route



ping -c 4 192.168.99.193



sudo apt update

sudo apt install -y iperf3

iperf3 --version



iperf3 -c 192.168.99.193 -p 5204 -t 60 -J | tee client40\_result.json







Router

enable

configure terminal

hostname Router

!

interface GigabitEthernet0/0

&#x20;description Link-to-Net-WAN

&#x20;ip address dhcp

&#x20;ip nat outside

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Link-to-Switch

&#x20;ip address 192.168.1.1 255.255.255.0

&#x20;ip nat inside

&#x20;no shutdown

!

ip route 0.0.0.0 0.0.0.0 dhcp

!

ip dhcp excluded-address 192.168.1.1 192.168.1.10

!

ip dhcp pool LAN

&#x20;network 192.168.1.0 255.255.255.0

&#x20;default-router 192.168.1.1

&#x20;dns-server 8.8.8.8 1.1.1.1

!

ip access-list standard NAT

&#x20;permit 192.168.1.0 0.0.0.255

!

ip nat inside source list NAT interface GigabitEthernet0/0 overload

!

!

end

write memory

Switch (L2 ธรรมดา ไม่มี VLAN — ทุกพอร์ตอยู่ VLAN1 default)

enable

configure terminal

hostname Switch

!

interface GigabitEthernet0/0

&#x20;description Uplink-to-Router

&#x20;switchport mode access

&#x20;switchport access vlan 1

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Access-LinuxServer

&#x20;switchport mode access

&#x20;switchport access vlan 1

&#x20;spanning-tree portfast

&#x20;no shutdown

!

no cdp run

!

end

write memory

LinuxServer (netplan — ใช้ DHCP รับ IP จาก Router)

bash

sudo bash -c 'cat > /etc/netplan/01-netcfg.yaml << EOF

network:

&#x20; version: 2

&#x20; renderer: networkd

&#x20; ethernets:

&#x20;   ens3:

&#x20;     dhcp4: true

&#x20;     nameservers:

&#x20;       addresses:

&#x20;         - 8.8.8.8

&#x20;         - 1.1.1.1

EOF'

sudo chmod 600 /etc/netplan/01-netcfg.yaml

sudo netplan apply

ทดสอบ

bash

ip a

ip route                  # ต้องเห็น default via 192.168.1.1



ping -c 4 192.168.1.1     # gateway

ping -c 4 8.8.8.8          # ออกเน็ตได้ไหม

ping -c 4 google.com        # DNS ทำงานไหม



sudo apt update

sudo apt install -y iperf3

iperf3 --version

