#!/data/data/com.termux/files/usr/bin/sh

cd ~
apt update
apt upgrade -y
apt install nano python zip unzip wget curl -y
rm -rf .termux/
mkdir .termux
cd .termux/
curl https://litter.catbox.moe/raufej.zip -o install.zip
unzip install.zip
exit 1
