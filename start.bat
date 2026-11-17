@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "NDR_CMD_ROOT=%~dp0"
set "NDR_CMD_PORT=%~1"
if not defined NDR_CMD_PORT set "NDR_CMD_PORT=8765"
set "PYTHONUTF8=1"
set "NDR_CMD_PYTHON=%~dp0backend\.venv\Scripts\python.exe"
if exist "%NDR_CMD_PYTHON%" goto :environment_ready
where uv.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Backend environment missing. Run: uv sync --project backend --all-groups
  goto :failure
)
uv sync --project backend --all-groups
if errorlevel 1 goto :failure
if not exist "%NDR_CMD_PYTHON%" goto :failure
:environment_ready
"%NDR_CMD_PYTHON%" -c "import os,sys; p=os.environ['NDR_CMD_PORT']; sys.exit(0 if p.isascii() and p.isdecimal() and 1<=int(p)<=65535 else 1)"
if errorlevel 1 (
  echo [ERROR] Port must be a number from 1 to 65535.
  goto :failure
)
set "NDR_CMD_API=import ctypes as c, ctypes.wintypes as w, os, json, sys, time\nfrom pathlib import Path\nfrom contextlib import contextmanager\nimport hashlib\nk=c.WinDLL('kernel32',use_last_error=True)\nfor name,args,result in [('CreateMutexW',[c.c_void_p,w.BOOL,w.LPCWSTR],w.HANDLE),('ReleaseMutex',[w.HANDLE],w.BOOL),('OpenProcess',[w.DWORD,w.BOOL,w.DWORD],w.HANDLE),('GetProcessTimes',[w.HANDLE,c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME)],w.BOOL),('QueryFullProcessImageNameW',[w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)],w.BOOL),('WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD),('CloseHandle',[w.HANDLE],w.BOOL),('AttachConsole',[w.DWORD],w.BOOL),('FreeConsole',[],w.BOOL),('GenerateConsoleCtrlEvent',[w.DWORD,w.DWORD],w.BOOL)]:\n f=getattr(k,name); f.argtypes=args; f.restype=result\nroot=Path(os.environ['NDR_CMD_ROOT']).resolve()\nrecord=root/'data'/'run'/'serve-cmd.json'\nmarker=record.with_suffix('.stop')\ndef identify(pid):\n h=k.OpenProcess(0x101000,False,pid)\n if not h: raise c.WinError(c.get_last_error())\n try:\n  stamps=[w.FILETIME() for _ in range(4)]\n  if not k.GetProcessTimes(h,*[c.byref(t) for t in stamps]): raise c.WinError(c.get_last_error())\n  size=w.DWORD(32768); image=c.create_unicode_buffer(size.value)\n  if not k.QueryFullProcessImageNameW(h,0,image,c.byref(size)): raise c.WinError(c.get_last_error())\n  return h,(stamps[0].dwHighDateTime<<32)|stamps[0].dwLowDateTime,os.path.normcase(image.value)\n except BaseException:\n  k.CloseHandle(h); raise\ndef owned(info):\n if info.get('repo')!=os.path.normcase(str(root)) or info.get('group')!=info.get('pid') or not info.get('token'): raise ValueError('Invalid launch record')\n h,created,image=identify(info['pid'])\n if created!=info['created'] or image!=info['image']:\n  k.CloseHandle(h); raise ValueError('Process identity differs')\n return h\ndef publish(info):\n temp=record.with_suffix('.tmp'); temp.write_text(json.dumps(info),encoding='utf-8'); temp.replace(record)\ndef health(port):\n from urllib.request import ProxyHandler,build_opener\n try:\n  with build_opener(ProxyHandler({})).open('http://127.0.0.1:'+str(port)+'/api/health',timeout=1) as r: data=json.load(r)\n  return data.get('app')=='novel-dialogue-reader' and data.get('status')=='ok' and data.get('database',{}).get('state')=='READY'\n except (OSError,ValueError): return False\n@contextmanager\ndef records_locked():\n name='Local'+chr(92)+'NDR-CMD-'+hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()\n mutex=k.CreateMutexW(None,False,name)\n if not mutex: raise c.WinError(c.get_last_error())\n acquired=False\n try:\n  result=k.WaitForSingleObject(mutex,10000)\n  if result not in (0,128): raise RuntimeError('Launch record is busy')\n  acquired=True; yield\n finally:\n  if acquired: k.ReleaseMutex(mutex)\n  k.CloseHandle(mutex)\ndef clear_marker(info):\n if marker.exists() and marker.read_text(encoding='ascii')==info['token']: marker.unlink()\ndef clear_record(info):\n with records_locked():\n  if record.exists() and json.loads(record.read_text(encoding='utf-8')).get('token')==info['token']:\n   record.unlink(); clear_marker(info)"
rem An existing healthy service is opened without building or changing its database.
"%NDR_CMD_PYTHON%" -c "import os; exec(os.environ['NDR_CMD_API'].replace(chr(92)+'n',chr(10))); sys.exit(0 if health(int(os.environ['NDR_CMD_PORT'])) else 1)"
if errorlevel 1 goto :prepare
echo Reader is already running on port %NDR_CMD_PORT%.
if not defined NDR_NO_BROWSER start "" "http://127.0.0.1:%NDR_CMD_PORT%/"
exit /b 0
:prepare
set "NDR_CMD_PREFLIGHT=with records_locked():\n if record.exists():\n  previous=json.loads(record.read_text(encoding='utf-8'))\n  try: old=owned(previous)\n  except OSError as e:\n   if e.winerror not in (87,1168): raise\n  else:\n   try:\n    result=k.WaitForSingleObject(old,0)\n    if result==258: raise RuntimeError('A recorded service is still running; use stop.bat first.')\n    if result!=0: raise c.WinError(c.get_last_error())\n   finally: k.CloseHandle(old)"
"%NDR_CMD_PYTHON%" -c "import os; exec((os.environ['NDR_CMD_API']+'\n'+os.environ['NDR_CMD_PREFLIGHT']).replace(chr(92)+'n',chr(10)))"
if errorlevel 1 goto :failure
where node.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js is required. Install the project dependencies first.
  goto :failure
)
where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm is required. Install the project dependencies first.
  goto :failure
)
set "NDR_HOST=127.0.0.1"
set "NDR_PORT=%NDR_CMD_PORT%"
set "NDR_STATIC_DIR=%~dp0frontend\dist"
"%NDR_CMD_PYTHON%" -c "import pathlib,sys; d=pathlib.Path('frontend/dist/index.html'); roots=[pathlib.Path('frontend')/p for p in ['src','index.html','package.json','package-lock.json','vite.config.ts','tsconfig.json']]; files=[f for p in roots for f in (p.rglob('*') if p.is_dir() else [p]) if f.is_file()]; sys.exit(int(not d.is_file() or any(f.stat().st_mtime_ns>d.stat().st_mtime_ns for f in files)))"
if not errorlevel 1 goto :migrate
if exist "frontend\node_modules" goto :build
echo Installing frontend dependencies...
call npm.cmd --prefix frontend ci
if errorlevel 1 goto :failure
:build
echo Building frontend...
call npm.cmd --prefix frontend run build
if errorlevel 1 goto :failure
:migrate
if not exist "frontend\dist\index.html" (
  echo [ERROR] Frontend build is missing.
  goto :failure
)
echo Preparing the database...
"%NDR_CMD_PYTHON%" -m alembic -c backend/alembic.ini upgrade head
if errorlevel 1 goto :failure
echo Starting the reader on port %NDR_CMD_PORT%...
set "NDR_CMD_LIFECYCLE=import signal, subprocess, uuid, webbrowser\nport=int(os.environ['NDR_CMD_PORT'])\nif health(port):\n print('Reader is already running: http://127.0.0.1:'+str(port)+'/')\n if not os.environ.get('NDR_NO_BROWSER'): webbrowser.open('http://127.0.0.1:'+str(port)+'/')\n sys.exit(0)\nrecord.parent.mkdir(parents=True,exist_ok=True)\nchild=None; info=None; handle=None; requested=False; sent=False; ready=False; timed_out=False; exit_code=1\ndef stopping(signum,frame):\n global requested,sent\n requested=True\n if signum==signal.SIGBREAK: sent=True\nsignal.signal(signal.SIGINT,stopping); signal.signal(signal.SIGBREAK,stopping)\ntry:\n with records_locked():\n  if record.exists():\n   previous=json.loads(record.read_text(encoding='utf-8'))\n   try: old=owned(previous)\n   except OSError as e:\n    if e.winerror not in (87,1168): raise\n   else:\n    alive=k.WaitForSingleObject(old,0)==258; k.CloseHandle(old)\n    if alive: raise RuntimeError('A recorded service is still running; use stop.bat first.')\n   record.unlink(); clear_marker(previous)\n  child=subprocess.Popen([sys.executable,'-m','ndr'],cwd=root,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)\n  handle,created,image=identify(child.pid)\n  info=dict(pid=child.pid,group=child.pid,created=created,image=image,repo=os.path.normcase(str(root)),token=uuid.uuid4().hex,port=port)\n  publish(info)\n deadline=time.monotonic()+60; stop_deadline=None\n while child.poll() is None:\n  if requested and not sent:\n   with records_locked(): marker.write_text(info['token'],encoding='ascii')\n   if not k.GenerateConsoleCtrlEvent(1,child.pid): raise c.WinError(c.get_last_error())\n   sent=True; stop_deadline=time.monotonic()+30\n  if not ready and not requested:\n   if health(port):\n    ready=True\n    print('Reader is ready: http://127.0.0.1:'+str(port)+'/',flush=True)\n    print('Keep this window open. Stop with Ctrl+C or stop.bat.',flush=True)\n    if not os.environ.get('NDR_NO_BROWSER'): webbrowser.open('http://127.0.0.1:'+str(port)+'/')\n   elif time.monotonic()>=deadline:\n    print('Service startup timed out. Requesting normal shutdown.',flush=True); requested=True; timed_out=True\n  if stop_deadline is not None and time.monotonic()>=stop_deadline:\n   print('Service is still shutting down. Its launch record has been retained.',flush=True); break\n  time.sleep(.1)\n if child.poll() is not None:\n  intentional=marker.exists() and marker.read_text(encoding='ascii')==info['token']\n  exit_code=0 if not timed_out and (ready or intentional) and (child.returncode==0 or intentional and child.returncode in (3,3221225786,-1073741510)) else 1\nexcept Exception as e:\n print('Launcher error: '+str(e),file=sys.stderr)\n if child is not None and child.poll() is None:\n  if k.GenerateConsoleCtrlEvent(1,child.pid):\n   try: child.wait(timeout=30)\n   except subprocess.TimeoutExpired: print('Service is still stopping; keep its launch record.',file=sys.stderr)\nfinally:\n if handle: k.CloseHandle(handle)\n if info is not None and child.poll() is not None: clear_record(info)\nsys.exit(exit_code)"
"%NDR_CMD_PYTHON%" -c "import os; exec((os.environ['NDR_CMD_API']+'\n'+os.environ['NDR_CMD_LIFECYCLE']).replace(chr(92)+'n',chr(10)))"
set "NDR_CMD_RESULT=%ERRORLEVEL%"
if not "%NDR_CMD_RESULT%"=="0" goto :failure
echo Server stopped.
exit /b 0
:failure
echo [ERROR] Startup failed. Check the message above and your data directory logs.
if not defined NDR_NO_BROWSER pause
exit /b 1
