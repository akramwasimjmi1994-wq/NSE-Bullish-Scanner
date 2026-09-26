
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading, queue, os, sys, json, traceback, subprocess, tempfile, urllib.request, hashlib, io
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta

APP_VERSION = "3.0.6"
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

# Current NSE equity universe, refreshed daily and cached locally.
NSE_SYMBOL_CACHE = DATA_DIR / "nse_symbols.json"

def _download_nse(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149 Safari/537.36",
        "Accept": "text/csv,application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def get_nse_symbols(force=False):
    if not force and NSE_SYMBOL_CACHE.exists():
        try:
            obj=json.loads(NSE_SYMBOL_CACHE.read_text(encoding="utf-8"))
            if datetime.now().timestamp()-float(obj.get("timestamp",0)) < 86400 and len(obj.get("symbols",[])) >= 500:
                return list(obj["symbols"])
        except Exception:
            pass
    last_err=None
    for url in ("https://archives.nseindia.com/content/equities/EQUITY_L.csv",
                "https://www.nseindia.com/api/equity-master"):
        try:
            raw=_download_nse(url)
            df=pd.read_csv(io.StringIO(raw.decode("utf-8-sig",errors="replace")))
            cols={str(x).strip().upper():x for x in df.columns}
            sym_col=cols.get("SYMBOL")
            series_col=cols.get("SERIES")
            if not sym_col: raise ValueError("NSE equity list has no SYMBOL column")
            if series_col:
                df=df[df[series_col].astype(str).str.upper().isin({"EQ","BE","BZ"})]
            syms=sorted({str(x).strip().upper() for x in df[sym_col].dropna() if str(x).strip()})
            if len(syms)<500: raise ValueError(f"NSE returned only {len(syms)} symbols")
            NSE_SYMBOL_CACHE.write_text(json.dumps({"timestamp":datetime.now().timestamp(),"symbols":syms}),encoding="utf-8")
            return syms
        except Exception as e:
            last_err=e
            log(f"NSE list failed: {e}")
    if NSE_SYMBOL_CACHE.exists():
        try:
            obj=json.loads(NSE_SYMBOL_CACHE.read_text(encoding="utf-8"))
            if len(obj.get("symbols",[]))>=500: return list(obj["symbols"])
        except Exception: pass
    raise RuntimeError(f"Could not load NSE equity universe: {last_err}")

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
    d["MACD"]=ema(d.Close,12)-ema(d.Close,26)
    d["MACDSignal"]=ema(d["MACD"],9)
    return d

def fetch(sym,interval,period=None,start=None,end=None):
    try:
        t=yf.Ticker(sym+".NS")
        if start: d=t.history(start=start,end=end,interval=interval,auto_adjust=False,prepost=False)
        else: d=t.history(period=period,interval=interval,auto_adjust=False,prepost=False)
        if d is None or d.empty: return pd.DataFrame()
        if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(-1)
        cols=["Open","High","Low","Close","Volume"]
        return d[cols].dropna() if all(x in d.columns for x in cols) else pd.DataFrame()
    except Exception as e:
        log(f"fetch {sym}: {e}"); return pd.DataFrame()

def fetch_batch(symbols,interval,period):
    if not symbols: return {}
    try:
        raw=yf.download(tickers=[s+".NS" for s in symbols],interval=interval,period=period,
                        auto_adjust=False,prepost=False,group_by="ticker",threads=True,progress=False,timeout=20)
    except Exception as e:
        log(f"batch download failed ({len(symbols)}): {e}"); return {}
    out={}
    if raw is None or raw.empty: return out
    for sym in symbols:
        ticker=sym+".NS"
        try:
            if isinstance(raw.columns,pd.MultiIndex):
                if ticker in raw.columns.get_level_values(0): d=raw[ticker].copy()
                elif ticker in raw.columns.get_level_values(1): d=raw.xs(ticker,axis=1,level=1).copy()
                else: continue
            else:
                if len(symbols)!=1: continue
                d=raw.copy()
            cols=["Open","High","Low","Close","Volume"]
            if all(x in d.columns for x in cols):
                d=d[cols].dropna()
                if not d.empty: out[sym]=d
        except Exception as e: log(f"extract {sym}: {e}")
    return out

def scan_one(sym,d):
    if d is None or len(d)<60: return None
    try:
        d=calc(d); x=d.iloc[-1]
        cond=[x.Close>x.EMA20 and x.EMA20>x.EMA50,x.Close>x.VWAP,x.RSI14>50,
              x.ADX14>=20,x.RelVol>=1.2,x.STDir==1,x.MACD>x.MACDSignal]
        sc=sum(bool(v) for v in cond)
        names=["EMA","VWAP","RSI","ADX","RelVol","Supertrend","MACD"]
        row=(sym,f"{x.Close:.2f}",sc,f"{x.RSI14:.1f}",f"{x.ADX14:.1f}",f"{x.RelVol:.2f}",
             f"{x.EMA20:.2f}",f"{x.EMA50:.2f}",f"{x.VWAP:.2f}",
             "BULLISH" if x.STDir==1 else "BEARISH",", ".join(n for n,v in zip(names,cond) if v))
        return sc,bool(x.STDir==1),row
    except Exception as e:
        log(f"scan {sym}: {e}"); return None



def trade_setup(sym, interval):
    period = {"15 min":"60d","1 hour":"730d","1 day":"10y"}[interval]
    d = fetch(sym.upper().strip(), interval, period=period)
    if d.empty or len(d) < 60:
        raise ValueError("Not enough market data available for this stock/timeframe.")
    d = calc(d)
    x = d.iloc[-1]
    close = float(x.Close)
    atrv = float(atr(d,14).iloc[-1])
    score = sum(bool(v) for v in [
        x.Close>x.EMA20 and x.EMA20>x.EMA50,
        x.Close>x.VWAP, x.RSI14>50, x.ADX14>=20,
        x.RelVol>=1.2, x.STDir==1
    ])
    entry = close
    stop = max(0.01, entry - 1.5*atrv)
    target = entry + 3.0*atrv
    risk = entry - stop
    reward = target - entry
    breakout = float(d.High.tail(20).max())
    return {
        "symbol": sym.upper().strip(), "price": close, "entry": entry,
        "stop": stop, "target": target, "risk": risk, "reward": reward,
        "rr": reward/risk if risk else 0, "atr": atrv, "score": score,
        "rsi": float(x.RSI14), "adx": float(x.ADX14),
        "relvol": float(x.RelVol), "ema20": float(x.EMA20),
        "ema50": float(x.EMA50), "vwap": float(x.VWAP),
        "supertrend": "BULLISH" if x.STDir==1 else "BEARISH",
        "breakout": breakout, "time": str(d.index[-1])
    }

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
        scan=ttk.Frame(nb,padding=8); bt=ttk.Frame(nb,padding=8); setup=ttk.Frame(nb,padding=8)
        nb.add(scan,text="Live Scanner"); nb.add(bt,text="Backtest"); nb.add(setup,text="Trade Setup")

        c=ttk.Frame(scan); c.pack(fill="x")
        ttk.Label(c,text="Timeframe").pack(side="left")
        self.tf=ttk.Combobox(c,values=["15 min","1 hour","1 day"],state="readonly",width=10)
        self.tf.set("15 min"); self.tf.pack(side="left",padx=5)
        ttk.Button(c,text="Refresh NSE List",command=lambda:self.refresh_symbols()).pack(side="left",padx=5)
        ttk.Label(c,text="Minimum confirmations").pack(side="left",padx=(15,5))
        self.score=tk.IntVar(value=6); ttk.Spinbox(c,from_=1,to=7,textvariable=self.score,width=5).pack(side="left")
        ttk.Button(c,text="Scan All NSE Stocks",command=self.scan).pack(side="left",padx=10)
        self.status=ttk.Label(c,text="Ready"); self.status.pack(side="right")
        cols=["Symbol","Price","Score","RSI","ADX","RelVol","EMA20","EMA50","VWAP","Supertrend","MACD","Signal","Confirmations"]
        self.tree=ttk.Treeview(scan,columns=cols,show="headings")
        for x in cols: self.tree.heading(x,text=x); self.tree.column(x,width=105)
        self.tree.column("Signal",width=105); self.tree.column("MACD",width=110); self.tree.column("Confirmations",width=270); self.tree.pack(fill="both",expand=True,pady=8)
        self.tree.bind("<Double-1>", lambda e:self.use_selected_stock())

        f=ttk.Frame(bt); f.pack(fill="x")
        ttk.Label(f,text="Timeframe").grid(row=0,column=0); self.btf=ttk.Combobox(f,values=["15 min","1 hour","1 day"],state="readonly",width=10); self.btf.set("1 day"); self.btf.grid(row=0,column=1,padx=5)
        ttk.Label(f,text="Start").grid(row=0,column=2); self.start=ttk.Entry(f,width=12); self.start.insert(0,(datetime.now()-timedelta(days=365)).strftime("%Y-%m-%d")); self.start.grid(row=0,column=3,padx=5)
        ttk.Label(f,text="End").grid(row=0,column=4); self.end=ttk.Entry(f,width=12); self.end.insert(0,datetime.now().strftime("%Y-%m-%d")); self.end.grid(row=0,column=5,padx=5)
        ttk.Label(f,text="Score").grid(row=1,column=0); self.bs=tk.IntVar(value=6); ttk.Spinbox(f,from_=1,to=7,textvariable=self.bs,width=5).grid(row=1,column=1)
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

        # Single-stock trade setup
        sf=ttk.Frame(setup); sf.pack(fill="x",pady=5)
        ttk.Label(sf,text="NSE Stock").pack(side="left")
        self.setup_symbol=ttk.Entry(sf,width=16); self.setup_symbol.pack(side="left",padx=5)
        ttk.Label(sf,text="Timeframe").pack(side="left",padx=(15,5))
        self.setup_tf=ttk.Combobox(sf,values=["15 min","1 hour","1 day"],state="readonly",width=10)
        self.setup_tf.set("1 day"); self.setup_tf.pack(side="left")
        ttk.Button(sf,text="Calculate Entry & Exit",command=self.calculate_setup).pack(side="left",padx=12)
        ttk.Button(sf,text="Use Selected Stock",command=self.use_selected_stock).pack(side="left")

        self.setup_summary=ttk.Label(setup,text="Select a stock and calculate its trade setup.",font=("Segoe UI",12,"bold"))
        self.setup_summary.pack(fill="x",pady=15)
        sg=ttk.Frame(setup); sg.pack(fill="x")
        labels=[
            ("Current Price","setup_price"),("Suggested Entry","setup_entry"),
            ("Stop Loss","setup_stop"),("Target","setup_target"),
            ("Risk / Share","setup_risk"),("Reward / Share","setup_reward"),
            ("Risk : Reward","setup_rr"),("ATR(14)","setup_atr"),
            ("20-Bar Breakout","setup_breakout"),("Scanner Score","setup_score"),
            ("RSI","setup_rsi"),("ADX","setup_adx"),("Rel Volume","setup_relvol"),
            ("Supertrend","setup_st")
        ]
        self.setup_vars={}
        for i,(lab,key) in enumerate(labels):
            r=i//4; c=(i%4)*2
            ttk.Label(sg,text=lab).grid(row=r,column=c,sticky="w",padx=8,pady=8)
            v=tk.StringVar(value="-"); self.setup_vars[key]=v
            ttk.Label(sg,textvariable=v,font=("Segoe UI",10,"bold")).grid(row=r,column=c+1,sticky="w",padx=8,pady=8)
        ttk.Label(setup,text="Method: long setup using current price as entry, 1.5x ATR stop and 3x ATR target (2R). These are algorithmic reference levels, not guaranteed prices.",wraplength=1100).pack(anchor="w",pady=18)

    def use_selected_stock(self):
        sel=self.tree.selection()
        if not sel:
            messagebox.showinfo("Trade Setup","Select a stock in Live Scanner first.")
            return
        vals=self.tree.item(sel[0],"values")
        if vals:
            self.setup_symbol.delete(0,"end"); self.setup_symbol.insert(0,vals[0])

    def calculate_setup(self):
        sym=self.setup_symbol.get().strip().upper()
        if not sym:
            messagebox.showinfo("Trade Setup","Enter an NSE stock symbol, for example RELIANCE.")
            return
        self.setup_summary.config(text=f"Calculating setup for {sym}...")
        threading.Thread(target=self.setup_worker,args=(sym,self.setup_tf.get()),daemon=True).start()

    def setup_worker(self,sym,tf):
        try:
            result=trade_setup(sym,tf)
            self.q.put(("setup",result))
        except Exception as e:
            log("trade_setup: "+traceback.format_exc())
            self.q.put(("msg",f"Trade setup failed for {sym}:\n{e}"))

    def refresh_symbols(self):
        def worker():
            try:
                syms=get_nse_symbols(force=True)
                self.q.put(("status",f"NSE list refreshed: {len(syms)} equity symbols available."))
            except Exception as e:
                self.q.put(("msg",f"Could not refresh NSE list:\n{e}"))
        threading.Thread(target=worker,daemon=True).start()

    def scan(self):
        for x in self.tree.get_children(): self.tree.delete(x)
        self.stop_flag=False; threading.Thread(target=self.scan_worker,daemon=True).start()

    def scan_worker(self):
        try:
            interval,period={"15 min":("15m","60d"),"1 hour":("60m","730d"),"1 day":("1d","10y")}[self.tf.get()]
            symbols=get_nse_symbols()
            self.q.put(("status",f"Loaded {len(symbols)} NSE equities. Downloading market data in parallel..."))
            batch_size=100
            batches=[symbols[i:i+batch_size] for i in range(0,len(symbols),batch_size)]
            confirmed=[]; candidates=[]; completed=0; minimum=self.score.get()
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures=[pool.submit(fetch_batch,b,interval,period) for b in batches]
                for fut in as_completed(futures):
                    if self.stop_flag: break
                    data_map=fut.result()
                    for sym,d in data_map.items():
                        result=scan_one(sym,d)
                        if not result: continue
                        sc,bullish,row=result
                        if sc>=minimum:
                            confirmed.append((sc,sym,row))
                        elif bullish and sc>=max(5,minimum-1):
                            candidates.append((sc,sym,row))
                    completed+=len(data_map)
                    self.q.put(("status",f"Downloaded/analyzed {completed}/{len(symbols)} stocks..."))
            confirmed.sort(key=lambda z:(-z[0],z[1]))
            for _,_,row in confirmed: self.q.put(("row",row))
            if not confirmed:
                candidates.sort(key=lambda z:(-z[0],z[1]))
                for _,_,row in candidates[:15]:
                    row=list(row); row[9]="CANDIDATE"; row[10]="Near confirmation: "+row[10]
                    self.q.put(("candidate",tuple(row)))
                if candidates:
                    self.q.put(("status",f"No {minimum}/6 fully confirmed signals. Showing top {min(15,len(candidates))} bullish candidates."))
                else:
                    self.q.put(("status","Scan complete — no bullish candidates met the fallback threshold."))
            else:
                self.q.put(("status",f"Scan complete — {len(confirmed)} fully confirmed bullish signals across {len(symbols)} NSE equities."))
        except Exception as e:
            log("scan_worker: "+traceback.format_exc())
            self.q.put(("msg",f"Scan failed:\n{e}"))

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
                    cond=[x.Close>x.EMA20 and x.EMA20>x.EMA50,x.Close>x.VWAP,x.RSI14>50,x.ADX14>=20,x.RelVol>=1.2,x.STDir==1,x.MACD>x.MACDSignal]
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
                if typ in ("row","candidate"): self.tree.insert("", "end", values=data)
                elif typ=="status": self.status.config(text=data); self.summary.config(text=data)
                elif typ=="msg": messagebox.showinfo("NSE Bullish Scanner",data)
                elif typ=="setup":
                    r=data
                    self.setup_summary.config(text=f"{r['symbol']} | {r['supertrend']} | Data as of {r['time']}")
                    vals={
                        "setup_price":f"{r['price']:.2f}","setup_entry":f"{r['entry']:.2f}",
                        "setup_stop":f"{r['stop']:.2f}","setup_target":f"{r['target']:.2f}",
                        "setup_risk":f"{r['risk']:.2f}","setup_reward":f"{r['reward']:.2f}",
                        "setup_rr":f"1 : {r['rr']:.2f}","setup_atr":f"{r['atr']:.2f}",
                        "setup_breakout":f"{r['breakout']:.2f}","setup_score":f"{r['score']}/6",
                        "setup_rsi":f"{r['rsi']:.1f}","setup_adx":f"{r['adx']:.1f}",
                        "setup_relvol":f"{r['relvol']:.2f}","setup_st":r["supertrend"]
                    }
                    for k,v in vals.items(): self.setup_vars[k].set(v)
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
