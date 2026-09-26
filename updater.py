
import sys, os, time, urllib.request, hashlib, zipfile, tempfile, shutil, subprocess, traceback
def main():
    if len(sys.argv)<3: return 2
    target=sys.argv[1]; url=sys.argv[2]; expected=sys.argv[3] if len(sys.argv)>3 else ""
    logdir=os.path.join(os.getenv("LOCALAPPDATA",os.path.expanduser("~")),"NSEBullishScanner","logs")
    os.makedirs(logdir,exist_ok=True)
    log=os.path.join(logdir,"updater.log")
    def w(s):
        with open(log,"a",encoding="utf8") as f: f.write(s+"\n")
    try:
        tmp=os.path.join(tempfile.gettempdir(),"NSE_Bullish_Scanner_Update.zip")
        urllib.request.urlretrieve(url,tmp)
        if expected:
            h=hashlib.sha256()
            with open(tmp,"rb") as f:
                for c in iter(lambda:f.read(1024*1024),b""): h.update(c)
            if h.hexdigest().lower()!=expected.lower(): raise RuntimeError("SHA256 mismatch")
        extract=os.path.join(tempfile.gettempdir(),"NSE_Bullish_Scanner_Update")
        shutil.rmtree(extract,ignore_errors=True); os.makedirs(extract)
        with zipfile.ZipFile(tmp) as z: z.extractall(extract)
        exe=None
        for root,dirs,files in os.walk(extract):
            for fn in files:
                if fn.lower()=="nse_bullish_scanner.exe": exe=os.path.join(root,fn); break
            if exe: break
        if not exe: raise RuntimeError("Updated EXE not found in package")
        time.sleep(2)
        for _ in range(20):
            try:
                os.replace(exe,target); break
            except PermissionError: time.sleep(.5)
        else: raise RuntimeError("Could not replace running EXE")
        subprocess.Popen([target],cwd=os.path.dirname(target))
        w("Update completed")
        return 0
    except Exception:
        w(traceback.format_exc()); return 1
if __name__=="__main__": raise SystemExit(main())
