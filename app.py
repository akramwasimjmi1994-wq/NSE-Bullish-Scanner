
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading, queue, os, sys, json, traceback, subprocess, tempfile, urllib.request, hashlib
from pathlib import Path
from datetime import datetime, timedelta

APP_VERSION = "3.0.1"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/akramwasimjmi1994-wq/NSE-Bullish-Scanner/main/update.json"
APP_NAME = "NSE_Bullish_Scanner.exe"

DATA_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "NSEBullishScanner"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    try:
        with open(LOG_DIR / "app.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass

def fatal(exc):
    log("FATAL\n" + traceback.format_exc())
    try:
        messagebox.showerror("NSE Bullish Scanner", f"The application could not start.\n\n{exc}\n\nLog:\n{LOG_DIR / 'app.log'}")
    except Exception:
        pass

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
except Exception as e:
    fatal(e)
    raise

# Representative built-in NSE universe. Add more symbols as required.
SYMBOLS = """360ONE 3MINDIA ABB ACC ADANIENT ADANIGREEN ADANIPORTS ADANIPOWER ABCAPITAL ABFRL
ALKEM AMBER AMBUJACEM ANGELONE APARINDS APLAPOLLO APOLLOHOSP APOLLOTYRE ASIANPAINT ASTRAL
ATGL AUBANK AUROPHARMA AXISBANK BAJAJ-AUTO BAJAJFINSV BAJFINANCE BALKRISIND BANDHANBNK
BANKBARODA BANKINDIA BEL BEML BHARATFORG BHARTIARTL BHEL BIOCON BIRLACORPN BOSCHLTD BPCL
BRITANNIA BSE CANBK CANFINHOME CDSL CEATLTD CGPOWER CHOLAFIN CIPLA COALINDIA COFORGE
COLPAL CONCOR COROMANDEL CROMPTON CUB CUMMINSIND DABUR DALBHARAT DEEPAKNTR DELHIVERY
DIVISLAB DIXON DLF DMART DRREDDY EICHERMOT EXIDEIND FEDERALBNK GAIL GLENMARK GODREJCP
GODREJPROP GRASIM HAL HAVELLS HCLTECH HDFCAMC HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO
HINDCOPPER HINDPETRO HINDUNILVR HINDZINC ICICIBANK ICICIGI ICICIPRULI IDEA IDFCFIRSTB
IEX IGL INDIAMART INDIANB INDHOTEL INDIGO INDUSINDBK INDUSTOWER INFY IOC IRCON IREDA
IRFC ITC JINDALSTEL JIOFIN JSWENERGY JSWSTEEL JUBLFOOD KALYANKJIL KEI KFINTECH KOTAKBANK
KPITTECH LICHSGFIN LICI LT LTIM LUPIN M&M M&MFIN MANAPPURAM MARICO MARUTI MAXHEALTH
MAZDOCK MGL MOTHERSON MPHASIS MRF MUTHOOTFIN NATIONALUM NBCC NCC NESTLEIND NHPC NMDC
NTPC OBEROIRLTY OFSS OIL ONGC PAGEIND PAYTM PERSISTENT PFC PNB POLYCAB POWERGRID PVRINOX
RECLTD RELIANCE RVNL SAIL SBICARD SBILIFE SBIN SHREECEM SIEMENS SJVN SOLARINDS SONACOMS
SRF SUNPHARMA SUNTV SUPREMEIND SUZLON TATACHEM TATACOMM TATAELXSI TATAMOTORS TATAPOWER
TATASTEEL TCS TECHM TITAN TORNTPHARM TORNTPOWER TRENT TVSMOTOR UPL VBL VEDL VOLTAS WIPRO
YESBANK ZEEL ZOMATO ZYDUSLIFE""".split()

def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def rma(s,n): return s.ewm(alpha=1/n,adjust=False).mean()

def rsi(c,n=14):
    d=c.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    rs=rma(up,n)/rma(dn,n).replace(0,np.nan)
    return 100-100/(1+rs)

def atr(df,n=14):
    p=df.Close.shift(1)
    tr=pd.concat([(df.High-df.Low),(df.High-p).abs(),(df.Low-p).abs()],axis=1).max(axis=1)
    return rma(tr,n)

def adx(df,n=14):
    up=df.High.diff(); dn=-df.Low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0),index=df.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0),index=df.index)
    a=atr(df,n)
    pdi=100*rma(plus,n)/a.replace(0,np.nan)
    mdi=100*rma(minus,n)/a.replace(0,np.nan)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return rma(dx,n)

def vwap(df):
    tp=(df.High+df.Low+df.Close)/3
    vol=df.Volume.fillna(0)
    if isinstance(df.index,pd.DatetimeIndex) and len(df):
        dates=df.index.date
        pv=pd.Series(tp*vol,index=df.index).groupby(dates).cumsum()
        vv=pd.Series(vol,index=df.index).groupby(dates).cumsum()
        return pv/vv.replace(0,np.nan)
    return (tp*vol).cumsum()/vol.cumsum().replace(0,np.nan)

def supertrend(df,period=10,mult=3):
    a=atr(df,period); mid=(df.High+df.Low)/2
    up=mid+mult*a; lo=mid-mult*a
    fu=up.copy(); fl=lo.copy()
    direction=pd.Series(1,index=df.index,dtype=int)
    st=pd.Series(np.nan,index=df.index)
    if len(df): st.iloc[0]=lo.iloc[0]
    for i in range(1,len(df)):
        fu.iloc[i]=up.iloc[i] if up.iloc[i]<fu.iloc[i-1] or df.Close.iloc[i-1]>fu.iloc[i-1] else fu.iloc[i-1]
        fl.iloc[i]=lo.iloc[i] if lo.iloc[i]>fl.iloc[i-1] or df.Close.iloc[i-1]<fl.iloc[i-1] else fl.iloc[i-1]
        if st.iloc[i-1]==fu.iloc[i-1]:
            direction.iloc[i]=-1 if df.Close.iloc[i]<=fu.iloc[i] else 1
        else:
            direction.iloc[i]=1 if df.Close.iloc[i]>=fl.iloc[i] else -1
        st.iloc[i]=fl.iloc[i] if direction.iloc[i]==1 else fu.iloc[i]
    return st,direction

def calc(df):
    d=df.copy()
    d["EMA20"]=ema(d.Close,20); d["EMA50"]=ema(d.Close,50)
    d["VWAP"]=vwap(d); d["RSI14"]=rsi(d.Close)
    d["ADX14"]=adx(d); d["RelVol"]=d.Volume/d.Volume.rolling(20).mean()
    d["Supertrend"],d["STDir"]=supertrend(d)
    return d

def fetch(sym,interval,period=None,start=None,end=None):
    t=yf.Ticker(sym+".NS")
    if start: d=t.history(start=start,end=end,interval=interval,auto_adjust=False,prepost=False)
    else: d=t.history(period=period,interval=interval,auto_adjust=False,prepost=False)
    if d is None or d.empty: return pd.DataFrame()
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    return d[["Open","High","Low","Close","Volume"]].dropna()

def check_update():
    with urllib.request.urlopen(UPDATE_MANIFEST_URL,timeout=10) as r:
        info=json.loads(r.read().decode())
    latest=str(info.get("version",APP_VERSION))
    def pv(v): return tuple(int(x) for x in v.split(".")[:3])
    if pv(latest)>pv(APP_VERSION): return info
    return None

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"NSE Bullish Scanner v{APP_VERSION}")
        self.geometry("1400x800")
        self.protocol("WM_DELETE_WINDOW",self.destroy)
        self.q=queue.Queue(); self.stop_flag=False; self.trades=[]
        self.make_ui(); self.after(200,self.poll)
        log("Application started.")

    def make_ui(self):
        top=ttk.Frame(self,padding=8); top.pack(fill="x")
        ttk.Label(top,text="NSE Bullish Scanner",font=("Segoe UI",18,"bold")).pack(side="left")
        ttk.Label(top,text=f"v{APP_VERSION}").pack(side="left",padx=10)
        ttk.Button(top,text="Check for Updates",command=self.update).pack(side="right")
        nb=ttk.Notebook(self); nb.pack(fill="both",expand=True)
        scan=ttk.Frame(nb,padding=8); bt=ttk.Frame(nb,padding=8)
        nb.add(scan,text="Live Scanner"); nb.add(bt,text="Backtest")

        c=ttk.Frame(scan); c.pack(fill="x")
        ttk.Label(c,text="Timeframe").pack(side="left")
        self.tf=ttk.Combobox(c,values=["15 min","1 hour","1 day"],state="readonly",width=10)
        self.tf.set("15 min"); self.tf.pack(side="left",padx=5)
        ttk.Label(c,text="Minimum confirmations").pack(side="left",padx=(15,5))
        self.score=tk.IntVar(value=5); ttk.Spinbox(c,from_=1,to=6,textvariable=self.score,width=5).pack(side="left")
        ttk.Button(c,text="Scan All Stocks",command=self.scan).pack(side="left",padx=10)
        self.status=ttk.Label(c,text="Ready"); self.status.pack(side="right")
        cols=["Symbol","Price","Score","RSI","ADX","RelVol","EMA20","EMA50","VWAP","Supertrend","Confirmations"]
        self.tree=ttk.Treeview(scan,columns=cols,show="headings")
        for x in cols: self.tree.heading(x,text=x); self.tree.column(x,width=105)
        self.tree.column("Confirmations",width=260); self.tree.pack(fill="both",expand=True,pady=8)

        f=ttk.Frame(bt); f.pack(fill="x")
        ttk.Label(f,text="Timeframe").grid(row=0,column=0); self.btf=ttk.Combobox(f,values=["15 min","1 hour","1 day"],state="readonly",width=10); self.btf.set("1 day"); self.btf.grid(row=0,column=1,padx=5)
        ttk.Label(f,text="Start").grid(row=0,column=2); self.start=ttk.Entry(f,width=12); self.start.insert(0,(datetime.now()-timedelta(days=365)).strftime("%Y-%m-%d")); self.start.grid(row=0,column=3,padx=5)
        ttk.Label(f,text="End").grid(row=0,column=4); self.end=ttk.Entry(f,width=12); self.end.insert(0,datetime.now().strftime("%Y-%m-%d")); self.end.grid(row=0,column=5,padx=5)
        ttk.Label(f,text="Score").grid(row=1,column=0); self.bs=tk.IntVar(value=5); ttk.Spinbox(f,from_=1,to=6,textvariable=self.bs,width=5).grid(row=1,column=1)
        ttk.Label(f,text="Target %").grid(row=1,column=2); self.target=tk.DoubleVar(value=2); ttk.Entry(f,textvariable=self.target,width=8).grid(row=1,column=3)
        ttk.Label(f,text="Stop %").grid(row=1,column=4); self.stop=tk.DoubleVar(value=1); ttk.Entry(f,textvariable=self.stop,width=8).grid(row=1,column=5)
        ttk.Label(f,text="Max bars").grid(row=1,column=6); self.bars=tk.IntVar(value=10); ttk.Entry(f,textvariable=self.bars,width=8).grid(row=1,column=7)
        ttk.Button(f,text="Run Backtest",command=self.backtest).grid(row=0,column=8,rowspan=2,padx=10)
        ttk.Button(f,text="Export CSV",command=self.export).grid(row=0,column=9,rowspan=2)
        self.summary=ttk.Label(bt,text="No backtest run yet.",font=("Segoe UI",11,"bold")); self.summary.pack(fill="x",pady=8)
        cols2=["Symbol","SignalTime","Entry","Exit","Return %","Outcome","Score","Bars"]
        self.bt=ttk.Treeview(bt,columns=cols2,show="headings")
        for x in cols2: self.bt.heading(x,text=x); self.bt.column(x,width=135)
        self.bt.pack(fill="both",expand=True)

    def scan(self):
        for x in self.tree.get_children(): self.tree.delete(x)
        self.stop_flag=False; threading.Thread(target=self.scan_worker,daemon=True).start()

    def scan_worker(self):
        cfg={"15 min":("15m","60d"),"1 hour":("60m","730d"),"1 day":("1d","10y")}[self.tf.get()]
        for i,s in enumerate(SYMBOLS,1):
            if self.stop_flag: break
            try:
                d=calc(fetch(s,*cfg))
                if len(d)<60: continue
                x=d.iloc[-1]
                cond=[
                    x.Close>x.EMA20 and x.EMA20>x.EMA50,
                    x.Close>x.VWAP,
                    x.RSI14>50,
                    x.ADX14>=20,
                    x.RelVol>=1.2,
                    x.STDir==1]
                sc=sum(bool(v) for v in cond)
                if sc>=self.score.get():
                    names=["EMA","VWAP","RSI","ADX","RelVol","Supertrend"]
                    self.q.put(("row",(s,f"{x.Close:.2f}",sc,f"{x.RSI14:.1f}",f"{x.ADX14:.1f}",f"{x.RelVol:.2f}",f"{x.EMA20:.2f}",f"{x.EMA50:.2f}",f"{x.VWAP:.2f}","BULLISH" if x.STDir==1 else "BEARISH",", ".join(n for n,v in zip(names,cond) if v))))
            except Exception as e: log(f"scan {s}: {e}")
            self.q.put(("status",f"Scanning {i}/{len(SYMBOLS)}"))
        self.q.put(("status","Scan complete"))

    def backtest(self):
        for x in self.bt.get_children(): self.bt.delete(x)
        self.trades=[]; threading.Thread(target=self.bt_worker,daemon=True).start()

    def bt_worker(self):
        interval={"15 min":"15m","1 hour":"60m","1 day":"1d"}[self.btf.get()]
        for i,s in enumerate(SYMBOLS,1):
            self.q.put(("status",f"Backtesting {i}/{len(SYMBOLS)}: {s}"))
            try:
                d=calc(fetch(s,interval,start=self.start.get(),end=self.end.get()))
                for i in range(60,len(d)-1):
                    x=d.iloc[i]
                    cond=[x.Close>x.EMA20 and x.EMA20>x.EMA50,x.Close>x.VWAP,x.RSI14>50,x.ADX14>=20,x.RelVol>=1.2,x.STDir==1]
                    if sum(bool(v) for v in cond)<self.bs.get(): continue
                    entry=float(d.Open.iloc[i+1]); target=entry*(1+self.target.get()/100); stop=entry*(1-self.stop.get()/100)
                    last=min(len(d)-1,i+1+self.bars.get()); outcome="TIME"; exitp=float(d.Close.iloc[last]); exit_i=last
                    for j in range(i+1,last+1):
                        if float(d.Low.iloc[j])<=stop: exitp=stop; outcome="LOSS"; exit_i=j; break
                        if float(d.High.iloc[j])>=target: exitp=target; outcome="WIN"; exit_i=j; break
                    ret=(exitp/entry-1)*100
                    self.trades.append({"Symbol":s,"SignalTime":str(d.index[i]),"Entry":entry,"Exit":exitp,"ReturnPct":ret,"Outcome":outcome,"Score":int(sum(bool(v) for v in cond)),"BarsHeld":exit_i-(i+1)})
            except Exception as e:
                log(f"backtest {s}: {e}")
        self.q.put(("done",self.trades)); self.q.put(("status","Backtest complete"))

    def export(self):
        if not self.trades: messagebox.showinfo("Backtest","Run a backtest first."); return
        p=filedialog.asksaveasfilename(defaultextension=".csv",filetypes=[("CSV","*.csv")])
        if p: pd.DataFrame(self.trades).to_csv(p,index=False)

    def update(self):
        threading.Thread(target=self.update_worker,daemon=True).start()

    def update_worker(self):
        try:
            info=check_update()
            if not info: self.q.put(("msg","You already have the latest version.")); return
            self.q.put(("update",info))
        except Exception as e:
            log("update: "+traceback.format_exc()); self.q.put(("msg",f"Update check failed:\n{e}"))

    def poll(self):
        try:
            while True:
                typ,data=self.q.get_nowait()
                if typ=="row": self.tree.insert("", "end", values=data)
                elif typ=="status": self.status.config(text=data); self.summary.config(text=data)
                elif typ=="msg": messagebox.showinfo("NSE Bullish Scanner",data)
                elif typ=="done":
                    self.trades=data
                    if data:
                        df=pd.DataFrame(data); wins=(df.ReturnPct>0).sum(); gp=df.loc[df.ReturnPct>0,"ReturnPct"].sum(); gl=abs(df.loc[df.ReturnPct<0,"ReturnPct"].sum())
                        self.summary.config(text=f"Trades: {len(df)} | Wins: {wins} | Win rate: {wins/len(df)*100:.2f}% | Avg return: {df.ReturnPct.mean():.2f}% | Profit factor: {(gp/gl if gl else float('inf')):.2f}")
                    else: self.summary.config(text="No signals found.")
                    for r in data: self.bt.insert("", "end", values=(r["Symbol"],r["SignalTime"],f'{r["Entry"]:.2f}',f'{r["Exit"]:.2f}',f'{r["ReturnPct"]:.2f}%',r["Outcome"],r["Score"],r["BarsHeld"]))
                elif typ=="update":
                    info=data
                    if messagebox.askyesno("Update available",f"Version {info['version']} is available.\n\n{info.get('notes','')}\n\nUpdate now?"):
                        self.start_updater(info)
        except queue.Empty: pass
        self.after(200,self.poll)

    def start_updater(self,info):
        if not getattr(sys,"frozen",False):
            messagebox.showinfo("Update","Automatic updating works from the packaged EXE. Build/install the connected EXE first."); return
        updater=Path(sys.executable).with_name("NSE_Bullish_Scanner_Updater.exe")
        if not updater.exists():
            messagebox.showerror("Update","Updater component is missing. Please install the connected build."); return
        subprocess.Popen([str(updater),str(sys.executable),info["url"],info.get("sha256","")],creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.destroy()

def main():
    try:
        app=App(); app.mainloop()
    except Exception as e:
        fatal(e)

if __name__=="__main__":
    main()
