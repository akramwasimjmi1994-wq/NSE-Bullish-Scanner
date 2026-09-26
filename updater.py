import sys, os, time, urllib.request, hashlib, zipfile, tempfile, shutil, subprocess, traceback, threading
import tkinter as tk
from tkinter import ttk, messagebox

def main():
    if len(sys.argv)<3: return 2
    target=sys.argv[1]; url=sys.argv[2]; expected=sys.argv[3] if len(sys.argv)>3 else ""

    root=tk.Tk()
    root.title("NSE Bullish Scanner - Updating")
    root.geometry("460x170")
    root.resizable(False,False)
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    frame=ttk.Frame(root,padding=20)
    frame.pack(fill="both",expand=True)
    title=ttk.Label(frame,text="Updating NSE Bullish Scanner",font=("Segoe UI Semibold",12))
    title.pack(anchor="w")
    status=ttk.Label(frame,text="Preparing update...")
    status.pack(anchor="w",pady=(12,6))
    progress=ttk.Progressbar(frame,orient="horizontal",mode="determinate",length=420,maximum=100)
    progress.pack(fill="x")
    percent=ttk.Label(frame,text="0%")
    percent.pack(anchor="e",pady=(5,0))

    logdir=os.path.join(os.getenv("LOCALAPPDATA",os.path.expanduser("~")),"NSEBullishScanner","logs")
    os.makedirs(logdir,exist_ok=True)
    log=os.path.join(logdir,"updater.log")

    def w(s):
        with open(log,"a",encoding="utf8") as f: f.write(s+"\\n")

    def ui(text_value, value=None):
        status.config(text=text_value)
        if value is not None:
            progress["value"]=max(0,min(100,value))
            percent.config(text=f"{int(value)}%")
        root.update_idletasks()

    def fail(exc):
        w(traceback.format_exc())
        ui("Update failed.",100)
        messagebox.showerror("Update failed",str(exc),parent=root)
        root.destroy()

    try:
        tmp=os.path.join(tempfile.gettempdir(),"NSE_Bullish_Scanner_Update.zip")
        ui("Downloading update...",0)

        def reporthook(block_count, block_size, total_size):
            if total_size > 0:
                downloaded=min(block_count*block_size,total_size)
                ui("Downloading update...",downloaded*70/total_size)

        urllib.request.urlretrieve(url,tmp,reporthook)

        ui("Download complete. Verifying...",72)
        if expected:
            h=hashlib.sha256()
            size=os.path.getsize(tmp)
            done=0
            with open(tmp,"rb") as f:
                for c in iter(lambda:f.read(1024*1024),b""):
                    h.update(c)
                    done+=len(c)
                    if size:
                        ui("Verifying update...",72+done*13/size)
            if h.hexdigest().lower()!=expected.lower():
                raise RuntimeError("SHA256 mismatch")

        ui("Preparing files...",86)
        extract=os.path.join(tempfile.gettempdir(),"NSE_Bullish_Scanner_Update")
        shutil.rmtree(extract,ignore_errors=True)
        os.makedirs(extract)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(extract)

        exe=None
        for root_dir,dirs,files in os.walk(extract):
            for fn in files:
                if fn.lower()=="nse_bullish_scanner.exe":
                    exe=os.path.join(root_dir,fn); break
            if exe: break
        if not exe: raise RuntimeError("Updated EXE not found in package")

        ui("Installing update...",92)
        time.sleep(2)
        for _ in range(20):
            try:
                os.replace(exe,target)
                break
            except PermissionError:
                time.sleep(.5)
        else:
            raise RuntimeError("Could not replace running EXE")

        ui("Update installed. Restarting...",98)
        subprocess.Popen([target],cwd=os.path.dirname(target))
        w("Update completed")
        ui("Update complete.",100)
        root.after(700,root.destroy)
        root.mainloop()
        return 0
    except Exception as e:
        fail(e)
        root.mainloop()
        return 1

if __name__=="__main__":
    raise SystemExit(main())
