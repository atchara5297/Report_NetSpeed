สามารถติดตั้งLinuxserverได้โดยใช้เวอร์ชั่น linux-ubuntu-22.04-server เผื่อให้สามารถรองรับกับโปรเจคและติดตั้งลงeve.ng เมื่อติดตั้งสำเร็จสามารถต่อเข้ากับคราวด์คอนฟิกไฟล์netplan อัปเดต และติดตั้งiperf3 และ ไลเบอรี่อื่นๆตามที่เขียนระบุไว้ หรือสามารถต่อผ่านเราเตอร์สวิตซ์ได้เพื่อให้สามารถติดตั้งหลายตัวได้พร้อมกันโดยต้องกำหนดพอร์ตที่ต่อเข้าให้ถูกต้อง

**1. คำสั่ง Router (vIOS)**



enable



configure terminal

hostname Router

!

interface GigabitEthernet0/0

&#x20;description Link-to-Switch-Gi0/0

&#x20;ip address 192.168.1.1 255.255.255.0

&#x20;ip nat inside

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Link-to-Net-WAN

&#x20;ip address dhcp

&#x20;ip nat outside

&#x20;no shutdown

!

ip route 0.0.0.0 0.0.0.0 dhcp

!

ip dhcp excluded-address 192.168.1.1 192.168.1.10

!

ip dhcp pool LAN

&#x20;network 192.168.1.0 255.255.255.0

&#x20;default-router 192.168.1.1

&#x20;dns-server 8.8.8.8 10.184.235.101

!

ip access-list standard NAT

&#x20;permit 192.168.1.0 0.0.0.255

!

ip nat inside source list NAT interface GigabitEthernet0/1 overload

!

end

write memory





**2. คำสั่ง Switch (Layer 2)**



enable

configure terminal

hostname Switch38

!

interface GigabitEthernet0/0

&#x20;description Uplink-to-Router

&#x20;switchport mode access

&#x20;switchport access vlan 1

&#x20;no shutdown

!

interface GigabitEthernet0/1

&#x20;description Link-to-LinuxServer20-e0

&#x20;switchport mode access

&#x20;switchport access vlan 1

&#x20;spanning-tree portfast

&#x20;no shutdown

!

end

write memory



**Linux**



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









apt update อัปเดต 

apt install -y python3 iperf3 tzdata iproute2 iputils-ping curl ติดตั้ง 



#### เช็คว่าติดตั้งสำเร็จหรือไม่

for pkg in python3 iperf3 tzdata iproute2 iputils-ping curl; do

&#x20;   if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then

&#x20;       echo "\[OK]      $pkg ติดตั้งแล้ว"

&#x20;   else

&#x20;       echo "\[MISSING] $pkg ยังไม่ได้ติดตั้ง"

&#x20;   fi

done





sudo hostnamectl set-hostname LinuxServer40

exec bash



sudo hostnamectl set-hostname Client-40

exec bash





#### ขยายพื้นที่พาทิชั่น พื้นที่ในอิมเมจลีนุ๊ก กรณีพื้นที่ไม่พอ

growpart /dev/vda 1

resize2fs /dev/vda1



###### Linux immage เปิดผ่านputtyได้ต้องติดตั้ง tty ก่อน

