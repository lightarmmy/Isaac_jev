"""Send Ctrl+C only to the recorded bridge's private Windows console."""
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import time

root=Path(__file__).resolve().parents[1]
record=json.loads((root/'.local/bridge-process.json').read_text(encoding='utf-8-sig'))
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
kernel.OpenProcess.restype=wintypes.HANDLE
kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
kernel.CloseHandle.argtypes=[wintypes.HANDLE]
handle=kernel.OpenProcess(0x1000,False,record['pid'])
if not handle:raise RuntimeError('Recorded bridge process is not available')
times=[wintypes.FILETIME() for _ in range(4)]
try:
    if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):raise ctypes.WinError(ctypes.get_last_error())
finally:kernel.CloseHandle(handle)
created=((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)/10_000_000-11644473600
if abs(created-datetime.fromisoformat(record['started_at']).timestamp())>3:
    raise RuntimeError('Recorded PID has a different creation time; refusing to signal it')
kernel.FreeConsole()
if not kernel.AttachConsole(record['pid']):raise ctypes.WinError(ctypes.get_last_error())
try:
    pids=(wintypes.DWORD*32)()
    count=kernel.GetConsoleProcessList(pids,32)
    if set(pids[:count])!={os.getpid(),record['pid']}:
        raise RuntimeError('Console is shared with another process; refusing to signal it')
    if not kernel.SetConsoleCtrlHandler(None,True):raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GenerateConsoleCtrlEvent(0,0):raise ctypes.WinError(ctypes.get_last_error())
    time.sleep(0.2)
    # Wake Windows' event loop if it is waiting on I/O when SIGINT arrives.
    try:
        with socket.create_connection(('127.0.0.1',9527),timeout=1) as client:
            client.sendall(b'{"protocol_version":1,"type":"control","payload":{"command":"get_status"}}\n')
    except OSError:pass
finally:kernel.FreeConsole()
print('Sent Ctrl+C to the verified private bridge console; recorder drain requested.')
