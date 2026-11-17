@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "NDR_CMD_ROOT=%~dp0"
set "PYTHONUTF8=1"
if not exist "backend\.venv\Scripts\python.exe" (
  echo [ERROR] Backend Python is missing. Use Ctrl+C in the original launch window.
  exit /b 1
)
set "NDR_CMD_API=import ctypes as c, ctypes.wintypes as w, os, json, sys, time\nfrom pathlib import Path\nfrom contextlib import contextmanager\nimport hashlib\nk=c.WinDLL('kernel32',use_last_error=True)\nfor name,args,result in [('CreateMutexW',[c.c_void_p,w.BOOL,w.LPCWSTR],w.HANDLE),('ReleaseMutex',[w.HANDLE],w.BOOL),('OpenProcess',[w.DWORD,w.BOOL,w.DWORD],w.HANDLE),('GetProcessTimes',[w.HANDLE,c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME)],w.BOOL),('QueryFullProcessImageNameW',[w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)],w.BOOL),('WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD),('CloseHandle',[w.HANDLE],w.BOOL),('AttachConsole',[w.DWORD],w.BOOL),('FreeConsole',[],w.BOOL),('GenerateConsoleCtrlEvent',[w.DWORD,w.DWORD],w.BOOL)]:\n f=getattr(k,name); f.argtypes=args; f.restype=result\nroot=Path(os.environ['NDR_CMD_ROOT']).resolve()\nrecord=root/'data'/'run'/'serve-cmd.json'\nmarker=record.with_suffix('.stop')\ndef identify(pid):\n h=k.OpenProcess(0x101000,False,pid)\n if not h: raise c.WinError(c.get_last_error())\n try:\n  stamps=[w.FILETIME() for _ in range(4)]\n  if not k.GetProcessTimes(h,*[c.byref(t) for t in stamps]): raise c.WinError(c.get_last_error())\n  size=w.DWORD(32768); image=c.create_unicode_buffer(size.value)\n  if not k.QueryFullProcessImageNameW(h,0,image,c.byref(size)): raise c.WinError(c.get_last_error())\n  return h,(stamps[0].dwHighDateTime<<32)|stamps[0].dwLowDateTime,os.path.normcase(image.value)\n except BaseException:\n  k.CloseHandle(h); raise\ndef owned(info):\n if info.get('repo')!=os.path.normcase(str(root)) or info.get('group')!=info.get('pid') or not info.get('token'): raise ValueError('Invalid launch record')\n h,created,image=identify(info['pid'])\n if created!=info['created'] or image!=info['image']:\n  k.CloseHandle(h); raise ValueError('Process identity differs')\n return h\ndef publish(info):\n temp=record.with_suffix('.tmp'); temp.write_text(json.dumps(info),encoding='utf-8'); temp.replace(record)\ndef health(port):\n from urllib.request import ProxyHandler,build_opener\n try:\n  with build_opener(ProxyHandler({})).open('http://127.0.0.1:'+str(port)+'/api/health',timeout=1) as r: data=json.load(r)\n  return data.get('app')=='novel-dialogue-reader' and data.get('status')=='ok' and data.get('database',{}).get('state')=='READY'\n except (OSError,ValueError): return False\n@contextmanager\ndef records_locked():\n name='Local'+chr(92)+'NDR-CMD-'+hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()\n mutex=k.CreateMutexW(None,False,name)\n if not mutex: raise c.WinError(c.get_last_error())\n acquired=False\n try:\n  result=k.WaitForSingleObject(mutex,10000)\n  if result not in (0,128): raise RuntimeError('Launch record is busy')\n  acquired=True; yield\n finally:\n  if acquired: k.ReleaseMutex(mutex)\n  k.CloseHandle(mutex)\ndef clear_marker(info):\n if marker.exists() and marker.read_text(encoding='ascii')==info['token']: marker.unlink()\ndef clear_record(info):\n with records_locked():\n  if record.exists() and json.loads(record.read_text(encoding='utf-8')).get('token')==info['token']:\n   record.unlink(); clear_marker(info)"
set "NDR_CMD_STOP=h=None; info=None; marked=False; signalled=False\ntry:\n with records_locked():\n  if not record.exists():\n   if (root/'data'/'run'/'serve-pids.json').exists(): sys.exit(7)\n   sys.exit(2)\n  info=json.loads(record.read_text(encoding='utf-8'))\n  try: h=owned(info)\n  except OSError as e:\n   if e.winerror in (87,1168):\n    clear_record(info); sys.exit(2)\n   raise\n  if k.WaitForSingleObject(h,0)==0:\n   clear_record(info); sys.exit(2)\n  marker.write_text(info['token'],encoding='ascii'); marked=True\n k.FreeConsole()\n if not k.AttachConsole(info['pid']): sys.exit(5)\n handler_type=c.WINFUNCTYPE(w.BOOL,w.DWORD)\n handler=handler_type(lambda event: event in (0,1))\n k.SetConsoleCtrlHandler.argtypes=[handler_type,w.BOOL]; k.SetConsoleCtrlHandler.restype=w.BOOL\n if not k.SetConsoleCtrlHandler(handler,True): sys.exit(5)\n if not k.GenerateConsoleCtrlEvent(1,info['group']): sys.exit(5)\n signalled=True\n result=k.WaitForSingleObject(h,30000)\n if result==258: sys.exit(4)\n if result!=0: sys.exit(5)\n sys.exit(0)\nexcept (ValueError,KeyError,TypeError,OSError,RuntimeError):\n sys.exit(3)\nfinally:\n if marked and not signalled:\n  with records_locked(): clear_marker(info)\n if h: k.CloseHandle(h)"
"backend\.venv\Scripts\python.exe" -c "import os; exec((os.environ['NDR_CMD_API']+'\n'+os.environ['NDR_CMD_STOP']).replace(chr(92)+'n',chr(10)))"
set "NDR_CMD_RESULT=%ERRORLEVEL%"
if "%NDR_CMD_RESULT%"=="0" (
  echo Reader stopped normally.
  exit /b 0
)
if "%NDR_CMD_RESULT%"=="2" (
  echo No recorded reader is running.
  exit /b 0
)
if "%NDR_CMD_RESULT%"=="7" (
  echo [ERROR] The old launcher record has no process identity.
  echo Use Ctrl+C in the original launch window, then use the new start.bat.
  exit /b 1
)
if "%NDR_CMD_RESULT%"=="4" (
  echo [ERROR] The reader is still shutting down. Keep the launch window open and retry.
  exit /b 1
)
if "%NDR_CMD_RESULT%"=="3" (
  echo [ERROR] The recorded process identity could not be verified. No process was stopped.
  exit /b 1
)
echo [ERROR] Could not signal the recorded reader. Use Ctrl+C in its launch window.
exit /b 1
