@echo off
cd /d "%~dp0"
echo Enter the server password in this window. Keep this window open while using the policy.
ssh.exe -p 31203 -o "UserKnownHostsFile=%~dp0.local\isaac-lab\ssh-known-hosts" -o StrictHostKeyChecking=accept-new -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:19528:127.0.0.1:19528 root@10.28.0.22 "cd /xufanghui/isaac-jev && .venv/bin/python scripts/start_policy_service.py && exec sleep infinity"
pause
